import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from verovo_healthcheck import mcp_server, monitor_state, spend_forecast

ROOT = Path(__file__).resolve().parents[2]


def record(task_id, amount, currency="USD", day=10):
    return {"observed_at": "2026-11-%02dT10:00:00Z" % day, "tool": "search",
            "model": "m1", "task_id": task_id, "amount": amount, "currency": currency}


class SpendForecastTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state.json"
        monitor_state.track([
            record("task-1", "10"), record("task-1", "20", day=11),
            record("task-1", "99", currency="EUR"), record("task-2", "500"),
        ], self.state, now="2026-11-12T00:00:00Z")
        self.contract = {
            "schema": "verovo.spend-forecast/1", "forecast_id": "f1", "task_id": "task-1",
            "currency": "USD", "period": {"start": "2026-11-01T00:00:00Z",
                                         "end": "2026-12-01T00:00:00Z"},
            "progress": {"completed_units": 3, "planned_units": 10, "unit": "accepted_subtask",
                         "source_ref": "task-board://task-1", "observed_at": "2026-11-12T00:00:00Z"},
        }

    def test_forecast_uses_observed_cost_per_actual_completed_unit(self):
        result = spend_forecast.evaluate(self.state, self.contract)
        self.assertEqual("estimated", result["status"])
        self.assertEqual("30", result["observed_spend"])
        self.assertEqual("10.00", result["observed_cost_per_completed_unit"])
        self.assertEqual("70.00", result["projected_remaining_cost"])
        self.assertEqual("100.00", result["projected_total_cost"])
        self.assertEqual(2, result["coverage"]["matched_records"])
        self.assertEqual(1, result["coverage"]["other_currency_records"])
        self.assertEqual("estimate_not_cap", result["forecast_state"])

    def test_zero_progress_or_missing_records_stays_unknown(self):
        self.contract["progress"]["completed_units"] = 0
        result = spend_forecast.evaluate(self.state, self.contract)
        self.assertEqual("insufficient_data", result["status"])
        self.assertIsNone(result["projected_remaining_cost"])
        self.contract["progress"]["completed_units"] = 3
        self.contract["task_id"] = "missing-task"
        missing = spend_forecast.evaluate(self.state, self.contract)
        self.assertEqual("insufficient_data", missing["status"])
        self.assertIsNone(missing["projected_total_cost"])

    def write_contract(self):
        path = self.root / "forecast.json"
        path.write_text(json.dumps(self.contract), encoding="utf-8")
        return path

    def test_cli_forecasts_local_task_cost(self):
        contract = self.write_contract()
        result = subprocess.run([
            sys.executable, str(ROOT / "healthcheck/verovo"), "monitor", "forecast",
            "--state", str(self.state), "--contract", str(contract), "--json",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("100.00", json.loads(result.stdout)["projected_total_cost"])

    def test_mcp_forecast_uses_same_evaluator(self):
        result = mcp_server.call_tool("verovo_economics_ops", {
            "action": "monitor_forecast", "state": str(self.state),
            "contract": str(self.write_contract()),
        })
        self.assertFalse(result.get("isError"), result)
        self.assertEqual("100.00", json.loads(result["content"][0]["text"])["projected_total_cost"])


if __name__ == "__main__":
    unittest.main()
