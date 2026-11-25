import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from verovo_healthcheck import budget_control, control_receipt, monitor_state

ROOT = Path(__file__).resolve().parents[2]


def usage(at, amount, *, task="task-1", tool="search", model="m1"):
    return {
        "observed_at": at, "task_id": task, "tool": tool, "model": model,
        "quantity": None, "unit_price": None, "amount": str(amount),
        "currency": "USD", "retry": False, "attempt": "", "source": "fixture",
    }


class ControlReceiptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state.json"
        monitor_state.track([
            usage("2026-11-01T10:00:00Z", "100"),
        ], self.state, now="2026-11-01T13:00:00Z")
        self.decision = budget_control.evaluate(self.state, {
            "schema": "verovo.spend-budget/1", "budget_id": "research-budget",
            "currency": "USD", "limit": "100", "warn_at": "0.8", "hold_at": "1",
            "period": {"start": "2026-11-01T00:00:00Z", "end": "2026-12-01T00:00:00Z"},
            "scope": {"task_id": "task-1", "tool": "search", "model": "m1"},
            "review_on_severity": "high",
        }, now="2026-11-01T12:00:00Z")

    def receipt(self):
        return {
            "schema": "verovo.spend-control-receipt/1",
            "receipt_id": "receipt-1", "budget_id": "research-budget",
            "requested_action": "hold",
            "scope": {"task_id": "task-1", "tool": "search", "model": "m1"},
            "readback": {
                "status": "stopped", "observed_at": "2026-11-01T12:05:00Z",
                "source_type": "runtime_readback", "source_ref": "runtime-job-7",
            },
            "coverage": {
                "start": "2026-11-01T12:05:00Z", "end": "2026-11-01T13:00:00Z",
                "complete": True, "source_ref": "usage-export-9",
            },
        }

    def test_matching_stop_readback_and_complete_quiet_window_passes(self):
        got = control_receipt.evaluate(self.state, self.decision, self.receipt())
        self.assertEqual("PASS", got["status"])
        self.assertTrue(got["control"]["execution_applied"])
        self.assertFalse(got["control"]["external_runtime_contacted"])
        self.assertEqual("0", got["post_stop_spend"]["amount"])
        self.assertIn("NO_POST_STOP_SPEND_IN_COMPLETE_WINDOW", got["reason_codes"])
        self.assertEqual(self.decision["scope"], got["scope"])

    def test_post_stop_spend_fails_even_with_a_stop_readback(self):
        monitor_state.track([
            usage("2026-11-01T10:00:00Z", "100"),
            usage("2026-11-01T12:10:00Z", "2.50"),
        ], self.state, now="2026-11-01T13:00:00Z")
        got = control_receipt.evaluate(self.state, self.decision, self.receipt())
        self.assertEqual("FAIL", got["status"])
        self.assertTrue(got["control"]["execution_applied"])
        self.assertEqual("2.50", got["post_stop_spend"]["amount"])
        self.assertIn("POST_STOP_SPEND_DETECTED", got["reason_codes"])

    def test_post_stop_credit_does_not_mask_a_positive_charge(self):
        monitor_state.track([
            usage("2026-11-01T10:00:00Z", "100"),
            usage("2026-11-01T12:10:00Z", "2.50"),
            usage("2026-11-01T12:20:00Z", "-2.50"),
        ], self.state, now="2026-11-01T13:00:00Z")
        got = control_receipt.evaluate(self.state, self.decision, self.receipt())
        self.assertEqual("FAIL", got["status"])
        self.assertEqual("2.50", got["post_stop_spend"]["amount"])
        self.assertEqual(1, got["post_stop_spend"]["matched_records"])

    def test_missing_complete_coverage_cannot_prove_no_later_spend(self):
        receipt = self.receipt()
        receipt["coverage"]["complete"] = False
        got = control_receipt.evaluate(self.state, self.decision, receipt)
        self.assertEqual("UNPROVABLE", got["status"])
        self.assertTrue(got["control"]["execution_applied"])
        self.assertIsNone(got["post_stop_spend"]["amount"])
        self.assertIn("POST_STOP_COVERAGE_INCOMPLETE", got["reason_codes"])

    def test_requested_stop_without_stopped_readback_is_unprovable(self):
        receipt = self.receipt()
        receipt["readback"]["status"] = "requested"
        got = control_receipt.evaluate(self.state, self.decision, receipt)
        self.assertEqual("UNPROVABLE", got["status"])
        self.assertFalse(got["control"]["execution_applied"])
        self.assertIn("STOP_READBACK_NOT_CONFIRMED", got["reason_codes"])

    def test_missing_receipt_timestamps_are_unprovable_not_crashes(self):
        receipt = self.receipt()
        del receipt["readback"]["observed_at"]
        del receipt["coverage"]["end"]
        got = control_receipt.evaluate(self.state, self.decision, receipt)
        self.assertEqual("UNPROVABLE", got["status"])
        self.assertFalse(got["control"]["execution_applied"])
        self.assertIn("STOP_READBACK_NOT_CONFIRMED", got["reason_codes"])
        self.assertIn("POST_STOP_COVERAGE_INCOMPLETE", got["reason_codes"])

    def test_scope_mismatch_fails_closed(self):
        receipt = self.receipt()
        receipt["scope"]["task_id"] = "another-task"
        got = control_receipt.evaluate(self.state, self.decision, receipt)
        self.assertEqual("FAIL", got["status"])
        self.assertFalse(got["control"]["execution_applied"])
        self.assertIn("CONTROL_SCOPE_MISMATCH", got["reason_codes"])

    def test_cli_reads_decision_and_receipt_without_contacting_runtime(self):
        decision = self.root / "decision.json"
        receipt = self.root / "receipt.json"
        decision.write_text(json.dumps(self.decision), encoding="utf-8")
        receipt.write_text(json.dumps(self.receipt()), encoding="utf-8")
        result = subprocess.run([
            sys.executable, str(ROOT / "healthcheck/verovo"), "budget", "verify",
            "--state", str(self.state), "--decision", str(decision),
            "--receipt", str(receipt), "--json",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual("PASS", payload["status"])
        self.assertEqual("local_receipt_and_monitor_consistency",
                         payload["control"]["verification_scope"])


if __name__ == "__main__":
    unittest.main()
