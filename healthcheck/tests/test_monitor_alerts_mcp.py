import json
import tempfile
import unittest
from pathlib import Path

import _support

from verovo_healthcheck import mcp_server, monitor_state


def row(day, amount="5"):
    return {
        "observed_at": "2026-11-%02dT10:00:00Z" % day,
        "tool": "search", "model": "m1", "task_id": "t1",
        "quantity": "5", "unit_price": "1", "amount": amount,
        "currency": "USD", "retry": False, "source": "fixture",
    }


class MonitorAlertsMcpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state.json"
        self.outbox = self.root / "alerts.jsonl"
        history = [row(day) for day in range(1, 9)]
        monitor_state.track(history, self.state, freeze=True, now="2026-11-09T00:00:00Z")
        monitor_state.track(history + [row(9, amount="50")], self.state,
                            now="2026-11-09T12:00:00Z")

    def test_manifest_exposes_local_notify_without_external_delivery_actions(self):
        tool = next(row for row in mcp_server.TOOLS if row["name"] == "verovo_economics_ops")
        actions = tool["inputSchema"]["properties"]["action"]["enum"]
        self.assertIn("monitor_notify", actions)
        self.assertFalse(any("email" in action or "webhook" in action or "wake" in action
                             for action in actions))

    def test_mcp_notify_writes_deduplicated_pending_local_alerts(self):
        arguments = {"action": "monitor_notify", "state": str(self.state),
                     "out": str(self.outbox), "severity": "medium"}
        emitted = mcp_server.call_tool("verovo_economics_ops", arguments)
        self.assertFalse(emitted.get("isError"), emitted)
        self.assertEqual(1, json.loads(emitted["content"][0]["text"])["emitted"])
        duplicate = mcp_server.call_tool("verovo_economics_ops", arguments)
        self.assertEqual(0, json.loads(duplicate["content"][0]["text"])["emitted"])
        alert = json.loads(self.outbox.read_text(encoding="utf-8").strip())
        self.assertEqual("pending_local_delivery", alert["notification"]["status"])
        self.assertFalse(alert["notification"]["host_agent_awake"])

    def test_skill_documents_local_outbox_boundary(self):
        text = (Path(__file__).resolve().parents[2] /
                "skills/verovo/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("monitor notify", text)
        self.assertIn("monitor_notify", text)
        self.assertIn("pending_local_delivery", text)


if __name__ == "__main__":
    unittest.main()
