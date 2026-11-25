import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from verovo_healthcheck import monitor_series, monitor_state

ROOT = Path(__file__).resolve().parents[2]


def record(at, amount, *, currency="USD", task="task-1", tool="search", model="m1", retry=False):
    return {"observed_at": at, "tool": tool, "model": model, "task_id": task,
            "quantity": None, "unit_price": None, "amount": str(amount),
            "currency": currency, "retry": retry, "attempt": "2" if retry else "1",
            "source": "fixture"}


class MonitorSeriesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"
        monitor_state.track([
            record("2026-11-01T10:05:00Z", "1.25"),
            record("2026-11-01T10:35:00Z", "2.75", retry=True),
            record("2026-11-01T11:00:00Z", "4"),
            record("2026-11-02T00:00:00Z", "10"),
            record("2026-11-01T10:10:00Z", "100", currency="EUR"),
            record("2026-11-01T10:15:00Z", "200", currency=None),
            record("2026-11-01T10:20:00Z", "999", task="other"),
        ], self.state, now="2026-11-02T01:00:00Z")

    def test_daily_series_separates_currency_and_unknown_currency(self):
        got = monitor_series.build(self.state, scope={"task_id": "task-1"})
        self.assertEqual("day", got["granularity"])
        self.assertEqual("UTC", got["timezone"])
        usd = [row for row in got["series"] if row["currency"] == "USD"]
        self.assertEqual(["2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"],
                         [row["bucket_start"] for row in usd])
        self.assertEqual("8.00", usd[0]["amount"])
        self.assertEqual(3, usd[0]["records"])
        self.assertEqual(1, usd[0]["retry_records"])
        self.assertEqual(1, got["coverage"]["unknown_currency_buckets"])
        self.assertNotIn("308", json.dumps(usd))

    def test_hour_scope_and_half_open_period(self):
        got = monitor_series.build(
            self.state, granularity="hour", scope={"task_id": "task-1"},
            start="2026-11-01T10:00:00Z", end="2026-11-01T11:00:00Z")
        row = next(row for row in got["series"] if row["currency"] == "USD")
        self.assertEqual("2026-11-01T10:00:00Z", row["bucket_start"])
        self.assertEqual("4.00", row["amount"])
        self.assertEqual(2, row["records"])
        self.assertEqual(1, got["coverage"]["excluded_other_scope_records"])

    def test_invalid_granularity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "granularity"):
            monitor_series.build(self.state, granularity="week")

    def test_cli_emits_time_series(self):
        result = subprocess.run([
            sys.executable, str(ROOT / "healthcheck/verovo"), "monitor", "series",
            "--state", str(self.state), "--granularity", "hour", "--json",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual("verovo.spend-time-series/1", payload["schema"])
        self.assertEqual("hour", payload["granularity"])


if __name__ == "__main__":
    unittest.main()
