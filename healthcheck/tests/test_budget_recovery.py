import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from verovo_healthcheck import budget_control, budget_recovery, monitor_state

ROOT = Path(__file__).resolve().parents[2]


def record(amount):
    return {"observed_at": "2026-11-01T10:00:00Z", "tool": "search", "model": "m1",
            "task_id": "task-1", "quantity": None, "unit_price": None,
            "amount": str(amount), "currency": "USD", "retry": False,
            "attempt": "1", "source": "fixture"}


class BudgetRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state.json"
        self.out = self.root / "recovery-history.jsonl"
        monitor_state.track([record("100")], self.state, now="2026-11-01T11:00:00Z")
        self.budget = {
            "schema": "verovo.spend-budget/1", "budget_id": "b1", "currency": "USD",
            "limit": "100", "warn_at": "0.8", "hold_at": "1",
            "period": {"start": "2026-11-01T00:00:00Z", "end": "2026-12-01T00:00:00Z"},
            "scope": {"task_id": "task-1"}, "review_on_severity": "high",
        }

    def recovery(self):
        return {
            "schema": "verovo.spend-budget-recovery/1", "recovery_id": "recovery-1",
            "budget_id": "b1", "scope": {"task_id": "task-1"},
            "confirmed_by": "budget-owner", "confirmed_at": "2026-11-01T12:00:00Z",
            "confirmation_ref": "owner-approval-1", "reason": "The failed retry loop was fixed.",
            "readback": {"status": "running", "observed_at": "2026-11-01T12:01:00Z",
                         "source_type": "runtime_readback", "source_ref": "runtime-job-7"},
        }

    def test_recovery_requires_current_budget_below_hold_and_running_readback(self):
        self.budget["limit"] = "200"
        got = budget_recovery.evaluate(self.state, self.budget, self.recovery(), self.out)
        self.assertEqual("PASS", got["status"])
        self.assertTrue(got["control"]["execution_applied"])
        self.assertFalse(got["control"]["external_runtime_contacted"])
        self.assertFalse(got["control"]["budget_reset"])
        self.assertEqual("100", got["observed_spend_preserved"])
        second = budget_recovery.evaluate(self.state, self.budget, self.recovery(), self.out)
        self.assertEqual(1, second["history"]["duplicate_records"])

    def test_hold_budget_cannot_be_resumed_by_owner_declaration(self):
        got = budget_recovery.evaluate(self.state, self.budget, self.recovery(), self.out)
        self.assertEqual("FAIL", got["status"])
        self.assertIn("CURRENT_BUDGET_STILL_HOLD", got["reason_codes"])
        self.assertFalse(got["control"]["execution_applied"])

    def test_failed_then_successful_recovery_preserves_both_history_records(self):
        failed = budget_recovery.evaluate(self.state, self.budget, self.recovery(), self.out)
        self.assertEqual("FAIL", failed["status"])
        self.budget["limit"] = "200"
        recovered = budget_recovery.evaluate(self.state, self.budget, self.recovery(), self.out)
        self.assertEqual("PASS", recovered["status"])
        self.assertEqual(2, recovered["history"]["records"])
        duplicate = budget_recovery.evaluate(self.state, self.budget, self.recovery(), self.out)
        self.assertEqual(1, duplicate["history"]["duplicate_records"])
        self.assertEqual(2, duplicate["history"]["records"])
        statuses = [json.loads(line)["status"] for line in self.out.read_text().splitlines()]
        self.assertEqual(["FAIL", "PASS"], statuses)

    def test_missing_running_readback_is_unprovable(self):
        self.budget["limit"] = "200"
        recovery = self.recovery()
        recovery["readback"]["status"] = "requested"
        got = budget_recovery.evaluate(self.state, self.budget, recovery, self.out)
        self.assertEqual("UNPROVABLE", got["status"])
        self.assertIn("RUNNING_READBACK_NOT_CONFIRMED", got["reason_codes"])

    def test_cli_records_recovery_without_resetting_spend(self):
        self.budget["limit"] = "200"
        budget = self.root / "budget.json"
        recovery = self.root / "recovery.json"
        budget.write_text(json.dumps(self.budget), encoding="utf-8")
        recovery.write_text(json.dumps(self.recovery()), encoding="utf-8")
        result = subprocess.run([
            sys.executable, str(ROOT / "healthcheck/verovo"), "budget", "resume",
            "--state", str(self.state), "--budget", str(budget),
            "--recovery", str(recovery), "--out", str(self.out), "--json",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual("PASS", payload["status"])
        self.assertEqual("100", payload["observed_spend_preserved"])


if __name__ == "__main__":
    unittest.main()
