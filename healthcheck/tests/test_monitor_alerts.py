import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from verovo_healthcheck import monitor_alerts, monitor_state

ROOT = Path(__file__).resolve().parents[2]


def row(day, amount="5", task="t1", observed_hour=10):
    return {
        "observed_at": "2026-11-%02dT%02d:00:00Z" % (day, observed_hour),
        "tool": "search", "model": "m1", "task_id": task,
        "quantity": "5", "unit_price": "1", "amount": amount,
        "currency": "USD", "retry": False, "source": "fixture",
    }


class MonitorAlertsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state.json"
        self.outbox = self.root / "alerts.jsonl"
        self.records = [row(day) for day in range(1, 9)] + [row(9, amount="50")]
        monitor_state.track(self.records[:-1], self.state, freeze=True,
                            now="2026-11-09T00:00:00Z")
        monitor_state.track(self.records, self.state, now="2026-11-09T12:00:00Z")

    def lines(self):
        return self.outbox.read_text(encoding="utf-8").splitlines()

    def test_emit_is_deduplicated_and_external_delivery_stays_unconfirmed(self):
        emitted = monitor_alerts.emit(self.state, self.outbox, now="2026-11-09T12:01:00Z")
        self.assertEqual("emitted", emitted["status"])
        self.assertEqual(1, emitted["emitted"])
        duplicate = monitor_alerts.emit(self.state, self.outbox, now="2026-11-09T12:02:00Z")
        self.assertEqual("no_new_alerts", duplicate["status"])
        self.assertEqual(1, len(self.lines()))
        alert = json.loads(self.lines()[0])
        self.assertEqual("verovo.spend-alert/1", alert["schema"])
        self.assertEqual("pending_local_delivery", alert["notification"]["status"])
        self.assertFalse(alert["notification"]["external_delivery_attempted"])
        self.assertFalse(alert["notification"]["external_delivery_confirmed"])
        self.assertFalse(alert["notification"]["host_agent_awake"])

    def test_repeated_track_does_not_spam_the_same_active_anomaly(self):
        monitor_alerts.emit(self.state, self.outbox, now="2026-11-09T12:01:00Z")
        monitor_state.track(self.records, self.state, now="2026-11-09T13:00:00Z")
        result = monitor_alerts.emit(self.state, self.outbox, now="2026-11-09T13:01:00Z")
        self.assertEqual(0, result["emitted"])
        self.assertEqual(1, len(self.lines()))

    def test_inactive_anomaly_does_not_create_an_alert(self):
        monitor_state.track(self.records[:-1], self.state, freeze=True,
                            now="2026-11-10T00:00:00Z")
        result = monitor_alerts.emit(self.state, self.outbox, now="2026-11-10T00:01:00Z")
        self.assertEqual(0, result["active_anomalies"])
        self.assertEqual("no_new_alerts", result["status"])
        self.assertFalse(self.outbox.exists())

    def test_cli_notify_writes_local_outbox(self):
        command = [sys.executable, str(ROOT / "healthcheck/verovo"), "monitor", "notify",
                   "--state", str(self.state), "--out", str(self.outbox), "--json"]
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(1, json.loads(result.stdout)["emitted"])
        self.assertEqual(1, len(self.lines()))


if __name__ == "__main__":
    unittest.main()
