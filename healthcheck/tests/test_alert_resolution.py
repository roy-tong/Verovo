import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from verovo_healthcheck import alert_resolution, mcp_server

ROOT = Path(__file__).resolve().parents[2]


def alert(kind="orphan_call", alert_id="alert-1"):
    anomaly = {"schema": "verovo.spend-anomaly/1", "kind": kind, "severity": "high",
               "suggestion": "Inspect the anomaly.", "task_id": "task-1"}
    return {"schema": "verovo.spend-alert/1", "alert_id": alert_id,
            "anomaly_id": "sha256:abc", "created_at": "2026-11-25T00:00:00Z",
            "source_last_seen_at": "2026-11-25T00:01:00Z", "anomaly": anomaly,
            "summary": "Observed spend needs review.",
            "notification": {"status": "pending_local_delivery"}}


class AlertResolutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_orphan_call_requests_task_mapping_before_user_input(self):
        result = alert_resolution.from_alert(alert())
        self.assertEqual("ovo.problem-resolution/1", result["schema"])
        self.assertEqual("data_needed", result["requested_resolution"])
        self.assertEqual("authorized-agent-task-registry", result["data"]["source"])
        self.assertFalse(result["data"]["automatic_fetch_attempted"])
        self.assertEqual("none", result["observation"]["external_effect_status"])

    def test_retry_storm_is_a_business_decision_not_external_pause(self):
        result = alert_resolution.from_alert(alert("retry_storm"))
        self.assertEqual("business_decision", result["requested_resolution"])
        self.assertGreaterEqual(len(result["decision"]["options"]), 2)
        self.assertNotIn("execution_applied", result)
        self.assertIn("not applied", result["decision"]["options"][0]["impact"])

    def test_outbox_loader_refuses_duplicate_alert_id(self):
        path = self.root / "alerts.jsonl"
        path.write_text(json.dumps(alert()) + "\n" + json.dumps(alert()) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            alert_resolution.load_alert(path, "alert-1")

    def test_cli_exports_contract_from_outbox(self):
        path = self.root / "alerts.jsonl"
        path.write_text(json.dumps(alert()) + "\n", encoding="utf-8")
        result = subprocess.run([
            sys.executable, str(ROOT / "healthcheck/verovo"), "monitor", "resolution",
            "--file", str(path), "--alert", "alert-1", "--json",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("data_needed", json.loads(result.stdout)["requested_resolution"])

    def test_mcp_exports_same_contract(self):
        path = self.root / "alerts.jsonl"
        path.write_text(json.dumps(alert("retry_storm")) + "\n", encoding="utf-8")
        result = mcp_server.call_tool("verovo_economics_ops", {
            "action": "alert_resolution", "file": str(path), "alert_id": "alert-1",
        })
        self.assertFalse(result.get("isError"), result)
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual("business_decision", payload["requested_resolution"])


if __name__ == "__main__":
    unittest.main()
