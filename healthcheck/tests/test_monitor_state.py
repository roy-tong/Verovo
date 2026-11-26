import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _support

from verovo_healthcheck import monitor, monitor_state

ROOT = Path(__file__).resolve().parents[2]


def row(day, amount="5", task="t1", observed_hour=10):
    return {
        "observed_at": "2026-11-%02dT%02d:00:00Z" % (day, observed_hour),
        "tool": "search", "model": "m1", "task_id": task,
        "quantity": "5", "unit_price": "1", "amount": amount,
        "currency": "USD", "retry": False, "source": "fixture",
    }


class MonitorStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "monitor-state.json"

    def test_first_track_requires_frozen_baseline_and_deduplicates(self):
        records = [row(1), row(2)]
        first = monitor_state.track(records, self.state, now="2026-11-02T12:00:00Z")
        self.assertEqual("baseline_not_frozen", first["status"])
        self.assertEqual(2, first["new_records"])
        second = monitor_state.track(records, self.state, now="2026-11-02T13:00:00Z")
        self.assertEqual(0, second["new_records"])
        self.assertEqual(2, second["duplicate_records"])
        self.assertEqual(2, second["state_records"])

    def test_frozen_baseline_does_not_include_later_spike(self):
        history = [row(day) for day in range(1, 9)]
        frozen = monitor_state.track(
            history, self.state, freeze=True, now="2026-11-09T00:00:00Z")
        self.assertEqual("baseline_frozen", frozen["status"])
        result = monitor_state.track(
            history + [row(9, amount="50")], self.state, now="2026-11-09T12:00:00Z")
        self.assertEqual(1, result["new_records"])
        self.assertEqual(5.0, result["baseline"]["groups"][0]["daily_median"])
        self.assertIn("spend_spike", result["anomaly_counts"])
        self.assertEqual(1, len(result["new_anomalies"]))

    def test_repeated_track_keeps_anomaly_active_but_not_new(self):
        history = [row(day) for day in range(1, 9)]
        monitor_state.track(history, self.state, freeze=True, now="2026-11-09T00:00:00Z")
        records = history + [row(9, amount="50")]
        first = monitor_state.track(records, self.state, now="2026-11-09T12:00:00Z")
        second = monitor_state.track(records, self.state, now="2026-11-09T13:00:00Z")
        self.assertEqual(1, len(first["new_anomalies"]))
        self.assertEqual([], second["new_anomalies"])
        self.assertEqual(1, second["anomaly_counts"]["spend_spike"])

    def test_late_record_is_not_swallowed_by_baseline_cutoff(self):
        history = [row(day) for day in range(1, 9)]
        monitor_state.track(history, self.state, freeze=True, now="2026-11-09T00:00:00Z")
        late = row(8, amount="80", task="late-task", observed_hour=9)
        result = monitor_state.track(
            history + [late], self.state, now="2026-11-09T13:00:00Z")
        self.assertEqual(1, result["late_records"])
        self.assertIn("spend_spike", result["anomaly_counts"])

    def test_event_identity_distinguishes_equal_calls_and_applies_revisions(self):
        source = Path(self.tmp.name) / "identified.jsonl"
        first = dict(row(9, amount="1"), event_id="event-a", revision="1")
        second = dict(row(9, amount="1"), event_id="event-b", revision="1")
        source.write_text("\n".join(json.dumps(item) for item in (first, second)) + "\n",
                          encoding="utf-8")
        initial = monitor_state.track(monitor.load_records(source), self.state)
        self.assertEqual(2, initial["new_records"])
        self.assertEqual(2, initial["state_records"])
        revised = dict(first, amount="2", revision="2")
        source.write_text("\n".join(json.dumps(item) for item in (revised, second)) + "\n",
                          encoding="utf-8")
        result = monitor_state.track(monitor.load_records(source), self.state)
        self.assertEqual(1, result["revised_records"])
        self.assertEqual(2, result["state_records"])
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(["1", "2"], sorted(entry["record"]["amount"] for entry in saved["records"]))

    def test_corrupt_state_fails_without_replacing_bytes(self):
        self.state.write_text("not-json", encoding="utf-8")
        with self.assertRaises(ValueError):
            monitor_state.track([row(1)], self.state, now="2026-11-02T00:00:00Z")
        self.assertEqual("not-json", self.state.read_text(encoding="utf-8"))

    def test_cli_freeze_and_track(self):
        source = Path(self.tmp.name) / "usage.jsonl"
        history = [row(day) for day in range(1, 9)]
        source.write_text("\n".join(json.dumps(item) for item in history) + "\n",
                          encoding="utf-8")
        base_argv = [sys.executable, str(ROOT / "healthcheck/verovo"), "monitor"]
        frozen = subprocess.run(base_argv + [
            "freeze-baseline", "--file", str(source), "--state", str(self.state), "--json",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, frozen.returncode, frozen.stderr + frozen.stdout)
        source.write_text("\n".join(json.dumps(item) for item in history + [row(9, amount="50")]) + "\n",
                          encoding="utf-8")
        tracked = subprocess.run(base_argv + [
            "track", "--file", str(source), "--state", str(self.state), "--json",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, tracked.returncode, tracked.stderr + tracked.stdout)
        payload = json.loads(tracked.stdout)
        self.assertIn("spend_spike", payload["anomaly_counts"])


if __name__ == "__main__":
    unittest.main()
