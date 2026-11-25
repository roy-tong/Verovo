import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _support

from verovo_healthcheck import experiment_export, tool_value

ROOT = Path(__file__).resolve().parents[2]


class ToolValueExperimentExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "tasks.jsonl"
        rows = []
        for index in range(2):
            rows.append({
                "task_id": "control-%d" % index,
                "group": "control",
                "success": "true" if index == 0 else "false",
                "cost": "1.00",
                "retry_cost": "0",
                "comparable_group": "research-v1",
                "observed_at": "2026-11-2%dT10:00:00Z" % (index + 1),
            })
            rows.append({
                "task_id": "treatment-%d" % index,
                "group": "treatment",
                "success": "true",
                "cost": "1.20",
                "retry_cost": "0.10",
                "comparable_group": "research-v1",
                "observed_at": "2026-11-2%dT11:00:00Z" % (index + 1),
            })
        self.source.write_text("\n".join(json.dumps(row) for row in rows) + "\n",
                               encoding="utf-8")
        self.tasks = tool_value.load_tasks(self.source)

    def design(self):
        return {
            "experiment_id": "tool-value-demo",
            "task_id": "procurement-demo",
            "title": "Synthetic tool comparison",
            "hypothesis": {
                "statement": "Tool B lowers cost per verified success for research-v1 tasks.",
                "falsification_condition": "Tool B does not lower cost per verified success.",
            },
            "design": {
                "experiment_type": "controlled_trial",
                "unit_of_analysis": "task",
                "population": "Synthetic research-v1 tasks",
                "assignment": "fixed_groups",
                "arms": [
                    {"arm_id": "control", "role": "control", "description": "Tool A",
                     "planned_sample_size": 2},
                    {"arm_id": "treatment", "role": "treatment", "description": "Tool B",
                     "planned_sample_size": 2},
                ],
                "primary_metrics": [
                    {"metric_id": "task_success", "definition": "Buyer accepts the task result.",
                     "unit": "boolean", "direction": "increase"},
                    {"metric_id": "task_cost", "definition": "Fully loaded task cost in USD.",
                     "unit": "USD", "direction": "decrease"},
                ],
                "comparability": {
                    "same_task_set": True,
                    "same_acceptance_rubric": True,
                    "same_budget": True,
                    "controlled_variables": ["Task set", "Acceptance rubric", "Budget"],
                    "known_differences": ["Tool"],
                },
            },
            "window": {"start": "2026-11-20T00:00:00Z", "end": "2026-11-25T23:59:59Z"},
            "stop_conditions": ["Stop after the registered task set completes."],
            "external_actions": False,
            "authorization_refs": [],
            "registered_by": "procurement-owner",
            "registered_at": "2026-11-19T00:00:00Z",
            "evidence_grade": "observed",
            "eligible": True,
        }

    def test_load_tasks_preserves_observation_time(self):
        self.assertEqual("2026-11-21T10:00:00Z", self.tasks[0]["observed_at"])

    def test_export_keeps_success_and_cost_at_task_grain(self):
        analysis = tool_value.analyze(self.tasks)
        got = experiment_export.from_tool_value(
            self.tasks, analysis, self.source, self.design(), "0.19.0")
        self.assertEqual("ovo.experiment-contract/1", got["schema"])
        self.assertEqual("verovo", got["origin_product"])
        self.assertEqual(8, len(got["observations"]))
        self.assertEqual("inconclusive", got["outcome"]["status"])
        self.assertEqual("descriptive_association", got["outcome"]["causality"])
        self.assertNotEqual("causal_supported", got["outcome"]["causality"])
        self.assertEqual(got["design_sha256"], experiment_export.design_hash(got))

    def test_missing_observation_time_is_refused(self):
        tasks = [dict(row) for row in self.tasks]
        tasks[0]["observed_at"] = ""
        with self.assertRaisesRegex(ValueError, "observed_at"):
            experiment_export.from_tool_value(
                tasks, tool_value.analyze(self.tasks), self.source, self.design(), "0.19.0")

    def test_same_task_set_claim_needs_a_comparable_group(self):
        tasks = [dict(row, comparable_group="") for row in self.tasks]
        with self.assertRaisesRegex(ValueError, "comparable_group"):
            experiment_export.from_tool_value(
                tasks, tool_value.analyze(self.tasks), self.source, self.design(), "0.19.0")

    def test_cli_exports_new_shared_contract(self):
        design_path = Path(self.tmp.name) / "design.json"
        design_path.write_text(json.dumps(self.design()), encoding="utf-8")
        out = Path(self.tmp.name) / "experiment.json"
        argv = [
            sys.executable, str(ROOT / "healthcheck/verovo"), "tool-value-export",
            "--file", str(self.source), "--design", str(design_path), "--out", str(out),
        ]
        result = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual("ovo.experiment-contract/1", payload["schema"])
        second = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(2, second.returncode)


if __name__ == "__main__":
    unittest.main()
