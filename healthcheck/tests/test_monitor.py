import json
import tempfile
import unittest
from pathlib import Path

from verovo_healthcheck import monitor


def row(day, amount="5", tool="search", model="m1", task="t1",
        unit_price="1", currency="USD", retry=False):
    return {
        "observed_at": "2026-11-%02dT10:00:00+08:00" % day,
        "tool": tool, "model": model, "task_id": task,
        "quantity": "5", "unit_price": unit_price, "amount": amount,
        "currency": currency, "retry": retry, "source": "fixture",
    }


class MonitorTest(unittest.TestCase):
    def write(self, rows):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "usage.jsonl"
        path.write_text("\n".join(json.dumps(item) for item in rows) + "\n",
                        encoding="utf-8")
        return str(path)

    def test_spike_with_constant_baseline(self):
        rows = [row(day) for day in range(1, 9)] + [row(9, amount="50")]
        records = monitor.load_records(self.write(rows))
        base = monitor.baseline(records)
        found = monitor.anomalies(records, base)
        self.assertTrue(any(item["kind"] == "spend_spike" for item in found))

    def test_price_drift_and_retry_storm_and_orphan(self):
        rows = [row(day) for day in range(1, 9)]
        rows.append(row(9, unit_price="2"))
        rows.extend(row(10, task="t-retry", retry=True) for _ in range(6))
        rows.append(row(11, task=""))
        records = monitor.load_records(self.write(rows))
        found = monitor.anomalies(records, monitor.baseline(records))
        kinds = {item["kind"] for item in found}
        self.assertIn("unit_price_drift", kinds)
        self.assertIn("retry_storm", kinds)
        self.assertIn("orphan_call", kinds)

    def test_insufficient_baseline_is_explicit(self):
        rows = [row(1), row(2), row(3)]
        records = monitor.load_records(self.write(rows))
        base = monitor.baseline(records)
        self.assertEqual("insufficient_baseline", base["groups"][0]["status"])

    def test_missing_amount_is_rejected(self):
        rows = [{"observed_at": "2026-11-01T00:00:00+08:00", "tool": "search"}]
        path = self.write(rows)
        with self.assertRaises(ValueError):
            monitor.load_records(path)

    def test_uniform_price_increase_is_compared_with_frozen_baseline(self):
        history = monitor.load_records(self.write([row(day, unit_price="1") for day in range(1, 9)]))
        base = monitor.baseline(history)
        current = monitor.load_records(self.write([row(9, amount="10", unit_price="2")]))
        found = monitor.anomalies(current, base)
        drifts = [item for item in found if item["kind"] == "unit_price_drift"]
        self.assertEqual(1, len(drifts))
        drift = drifts[0]
        self.assertEqual(1.0, drift["baseline_median"])
        self.assertEqual("2", drift["unit_price"])

    def test_csv_retry_false_and_zero_are_not_true(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "usage.csv"
        path.write_text(
            "observed_at,tool,amount,retry\n"
            "2026-11-01T00:00:00Z,search,1,false\n"
            "2026-11-01T00:01:00Z,search,1,0\n"
            "2026-11-01T00:02:00Z,search,1,true\n",
            encoding="utf-8")
        records = monitor.load_records(path)
        self.assertEqual([False, False, True], [row["retry"] for row in records])

    def test_report_keeps_currencies_separate(self):
        rows = [row(1), row(2, currency="EUR", amount="7")]
        records = monitor.load_records(self.write(rows))
        base = monitor.baseline(records)
        payload = monitor.report(records, base, [])
        self.assertEqual({"EUR": "7", "USD": "5"}, payload["totals"])
        self.assertEqual({"EUR", "USD"}, {row["currency"] for row in base["groups"]})
        self.assertEqual("advisory_only", payload["control"])


if __name__ == "__main__":
    unittest.main()
