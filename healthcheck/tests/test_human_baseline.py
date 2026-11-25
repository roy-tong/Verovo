import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _support

from verovo_healthcheck import human_baseline, tool_value

ROOT = Path(__file__).resolve().parents[2]


def task(task_id, group, cost, minutes, success="true"):
    return {"task_id": task_id, "group": group, "success": success,
            "cost": str(cost), "retry_cost": "0", "human_minutes": str(minutes),
            "comparable_group": "research-v1", "observed_at": "2026-11-01T10:00:00Z",
            "currency": "USD", "task_type": "research"}


def contract():
    return {
        "schema": "verovo.human-baseline/1", "comparison_id": "human-vs-agent-1",
        "task_type": "research", "acceptance_rubric_ref": "rubric-1", "currency": "USD",
        "min_tasks_per_arm": 2, "human_hourly_cost": "60",
        "human_cost_basis": {"source_ref": "payroll-export-1", "method": "loaded_hourly_cost",
                             "includes_loaded_labor": True},
        "comparability": {"same_task_set": True, "same_acceptance_rubric": True,
                          "same_currency": True, "same_quality_requirements": True},
        "quality_guardrail": {"max_success_rate_drop": "0.05"},
    }


class HumanBaselineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.human = [task("h1", "human", 0, 60), task("h2", "human", 0, 60)]
        self.agent = [task("a1", "agent", 1, 6), task("a2", "agent", 1, 6)]

    def evaluate(self, human=None, agent=None, spec=None):
        return human_baseline.evaluate(human or self.human, agent or self.agent, spec or contract())

    def test_agent_comparison_includes_tools_and_human_intervention(self):
        got = self.evaluate()
        self.assertEqual("lower_observed_cost", got["decision"])
        self.assertEqual("60.00", got["human"]["fully_loaded_cost_per_task"])
        self.assertEqual("7.00", got["agent"]["fully_loaded_cost_per_task"])
        self.assertEqual("53.00", got["observed_change"]["cost_per_task_reduction"])
        self.assertEqual(54, got["observed_change"]["human_minutes_reduction_per_task"])
        self.assertEqual("not_claimed", got["causality"])
        self.assertEqual("not_reconciled", got["saving_verification"])

    def test_quality_drop_makes_economic_claim_inconclusive(self):
        agent = [task("a1", "agent", 1, 6), task("a2", "agent", 1, 6, "false")]
        got = self.evaluate(agent=agent)
        self.assertEqual("inconclusive", got["decision"])
        self.assertIn("QUALITY_GUARDRAIL_BREACHED", got["reason_codes"])

    def test_non_comparable_task_groups_are_rejected(self):
        agent = [dict(row, comparable_group="other") for row in self.agent]
        with self.assertRaisesRegex(ValueError, "comparable_group"):
            self.evaluate(agent=agent)

    def test_no_verified_success_cannot_produce_cost_per_success_saving(self):
        agent = [task("a1", "agent", 1, 6, "false"), task("a2", "agent", 1, 6, "false")]
        spec = contract()
        spec["quality_guardrail"]["max_success_rate_drop"] = "1"
        got = self.evaluate(agent=agent, spec=spec)
        self.assertEqual("inconclusive", got["decision"])
        self.assertIn("COST_PER_SUCCESS_UNKNOWN", got["reason_codes"])
        self.assertIsNone(got["observed_change"]["cost_per_success_reduction"])

    def test_cli_runs_human_baseline(self):
        human = self.root / "human.jsonl"
        agent = self.root / "agent.jsonl"
        contract_path = self.root / "contract.json"
        for path, rows in ((human, self.human), (agent, self.agent)):
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        contract_path.write_text(json.dumps(contract()), encoding="utf-8")
        result = subprocess.run([
            sys.executable, str(ROOT / "healthcheck/verovo"), "human-baseline",
            "--human", str(human), "--agent", str(agent), "--contract", str(contract_path), "--json",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual("lower_observed_cost", json.loads(result.stdout)["decision"])


if __name__ == "__main__":
    unittest.main()
