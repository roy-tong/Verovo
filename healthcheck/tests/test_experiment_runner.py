import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _support

from verovo_healthcheck import experiment_runner
from verovo_local_runner import run_process

ROOT = Path(__file__).resolve().parents[2]


class ExperimentRunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.script = self.root / "task.py"
        self.script.write_text(
            "import os,sys\n"
            "print('task='+os.environ['OVO_EXPERIMENT_TASK_ID'])\n"
            "print('arm='+os.environ['OVO_EXPERIMENT_ARM_ID'])\n"
            "print('result-ok')\n", encoding="utf-8")

    def contract(self):
        return {
            "schema": "verovo.controlled-task-run/1", "experiment_id": "exp-1",
            "task_type": "research", "task_ids": ["task-1", "task-2"],
            "authorization": {"authorized_by": "buyer-owner", "local_only": True},
            "limits": {"timeout_seconds": 5, "max_output_bytes": 4096},
            "success": {"exit_code": 0, "stdout_contains": ["result-ok"]},
            "arms": [
                {"arm_id": "control", "argv": [sys.executable, str(self.script)]},
                {"arm_id": "treatment", "argv": [sys.executable, str(self.script)]},
            ],
        }

    def run_contract(self, spec, out):
        return experiment_runner.run(spec, out, process_runner=run_process)

    def test_runs_each_arm_task_pair_in_fresh_processes(self):
        result = self.run_contract(self.contract(), self.root / "out")
        self.assertEqual("PASS", result["status"])
        self.assertEqual(4, result["summary"]["observations"])
        self.assertEqual(4, result["summary"]["successful"])
        self.assertEqual(2, result["summary"]["arms"])
        self.assertEqual(2, result["summary"]["tasks"])
        for row in result["observations"]:
            self.assertIn("task_id", row)
            self.assertIn("process_id", row)
            self.assertEqual("unknown", row["cost_status"])
            self.assertIsNone(row["cost"])
        self.assertEqual(0, result["external_actions"])

    def test_failed_output_does_not_become_success(self):
        spec = self.contract()
        spec["success"]["stdout_contains"] = ["not-present"]
        result = self.run_contract(spec, self.root / "out")
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(0, result["summary"]["successful"])

    def test_non_loopback_or_unauthorized_run_is_refused(self):
        spec = self.contract()
        spec["authorization"]["local_only"] = False
        with self.assertRaisesRegex(ValueError, "local_only"):
            self.run_contract(spec, self.root / "out")
        spec = self.contract()
        spec["authorization"]["authorized_by"] = ""
        with self.assertRaisesRegex(ValueError, "authorized_by"):
            self.run_contract(spec, self.root / "out")

    def test_duplicate_arm_or_task_ids_are_rejected(self):
        spec = self.contract()
        spec["arms"][1]["arm_id"] = "control"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.run_contract(spec, self.root / "out")
        spec = self.contract()
        spec["task_ids"] = ["task-1", "task-1"]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.run_contract(spec, self.root / "out")

    def test_cli_executes_bounded_local_task_run(self):
        path = self.root / "contract.json"
        path.write_text(json.dumps(self.contract()), encoding="utf-8")
        result = subprocess.run([
            sys.executable, str(ROOT / "healthcheck/verovo"), "experiment", "run",
            "--contract", str(path), "--out", str(self.root / "out"), "--json",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual("PASS", json.loads(result.stdout)["status"])


if __name__ == "__main__":
    unittest.main()
