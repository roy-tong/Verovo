import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from verovo_healthcheck import budget_control, monitor_state

ROOT = Path(__file__).resolve().parents[2]


def record(day, amount, *, task="task-1", tool="search", model="m1", currency="USD"):
    return {
        "observed_at": "2026-11-%02dT10:00:00Z" % day,
        "tool": tool, "model": model, "task_id": task,
        "quantity": None, "unit_price": None, "amount": str(amount),
        "currency": currency, "retry": False, "attempt": "", "source": "fixture",
    }


def budget(*, limit="100", warn="0.8", hold="1", task="task-1"):
    return {
        "schema": "verovo.spend-budget/1",
        "budget_id": "research-budget",
        "currency": "USD",
        "limit": limit,
        "warn_at": warn,
        "hold_at": hold,
        "period": {"start": "2026-11-01T00:00:00Z", "end": "2026-12-01T00:00:00Z"},
        "scope": {"task_id": task, "tool": "search", "model": "m1"},
        "review_on_severity": "high",
    }


class BudgetControlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "monitor-state.json"

    def test_allow_warn_and_hold_use_observed_spend(self):
        monitor_state.track([record(1, "50")], self.state, now="2026-11-01T11:00:00Z")
        allowed = budget_control.evaluate(self.state, budget(), now="2026-11-01T12:00:00Z")
        self.assertEqual("allow", allowed["decision"])
        self.assertEqual("50", allowed["observed_spend"])
        self.assertEqual("50", allowed["remaining"])
        monitor_state.track([record(2, "30")], self.state, now="2026-11-02T11:00:00Z")
        warned = budget_control.evaluate(self.state, budget(), now="2026-11-02T12:00:00Z")
        self.assertEqual("warn", warned["decision"])
        monitor_state.track([record(3, "20")], self.state, now="2026-11-03T11:00:00Z")
        held = budget_control.evaluate(self.state, budget(), now="2026-11-03T12:00:00Z")
        self.assertEqual("hold", held["decision"])
        self.assertFalse(held["control"]["execution_applied"])

    def test_scope_period_and_currency_are_not_mixed(self):
        monitor_state.track([
            record(1, "40"),
            record(2, "999", task="other-task"),
            record(3, "999", currency="EUR"),
            record(1, "999", tool="other-tool"),
        ], self.state, now="2026-11-03T11:00:00Z")
        got = budget_control.evaluate(self.state, budget(), now="2026-11-03T12:00:00Z")
        self.assertEqual("40", got["observed_spend"])
        self.assertEqual(1, got["coverage"]["matched_records"])

    def test_unknown_currency_requires_review_and_is_not_zero(self):
        monitor_state.track([record(1, "50"), record(2, "20", currency=None)],
                            self.state, now="2026-11-02T11:00:00Z")
        got = budget_control.evaluate(self.state, budget(), now="2026-11-02T12:00:00Z")
        self.assertEqual("review", got["decision"])
        self.assertIn("CURRENCY_UNKNOWN", got["reason_codes"])
        self.assertEqual(1, got["coverage"]["unknown_currency_records"])
        self.assertEqual("50", got["observed_spend"])

    def test_current_high_anomaly_can_trigger_review(self):
        history = [record(day, "5") for day in range(1, 9)]
        monitor_state.track(history, self.state, freeze=True, now="2026-11-09T00:00:00Z")
        monitor_state.track(history + [record(9, "50")], self.state,
                            now="2026-11-09T12:00:00Z")
        got = budget_control.evaluate(self.state, budget(limit="1000"),
                                      now="2026-11-09T13:00:00Z")
        self.assertEqual("review", got["decision"])
        self.assertIn("ACTIVE_HIGH_RISK_ANOMALY", got["reason_codes"])

    def test_invalid_threshold_order_is_rejected(self):
        monitor_state.track([record(1, "50")], self.state, now="2026-11-01T11:00:00Z")
        with self.assertRaisesRegex(ValueError, "warn_at"):
            budget_control.evaluate(self.state, budget(warn="1", hold="0.8"),
                                    now="2026-11-01T12:00:00Z")

    def test_cli_outputs_control_decision_without_applying_it(self):
        monitor_state.track([record(1, "100")], self.state, now="2026-11-01T11:00:00Z")
        budget_path = Path(self.tmp.name) / "budget.json"
        budget_path.write_text(json.dumps(budget()), encoding="utf-8")
        result = subprocess.run([
            sys.executable, str(ROOT / "healthcheck/verovo"), "budget", "check",
            "--state", str(self.state), "--budget", str(budget_path), "--json",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual("hold", payload["decision"])
        self.assertEqual("advisory_only", payload["control"]["mode"])
        self.assertFalse(payload["control"]["execution_applied"])


if __name__ == "__main__":
    unittest.main()
