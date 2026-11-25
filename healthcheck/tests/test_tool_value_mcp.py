import json
import tempfile
import unittest
from pathlib import Path

import _support

from verovo_healthcheck import mcp_server


class ToolValueMcpTest(unittest.TestCase):
    def test_manifest_and_skill_expose_direct_tool_value_analysis(self):
        tool = next(row for row in mcp_server.TOOLS if row["name"] == "verovo_economics_ops")
        self.assertIn("tool_value_analyze", tool["inputSchema"]["properties"]["action"]["enum"])
        text = (Path(__file__).resolve().parents[2] /
                "skills/verovo/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("verovo tool-value --file TASKS --json", text)
        self.assertIn("tool_value_analyze", text)

    def test_mcp_analysis_returns_cost_quality_and_uncertainty(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "tasks.jsonl"
        rows = [
            {"task_id": "t1", "group": "treatment", "success": "true", "cost": "2", "retry_cost": "0"},
            {"task_id": "c1", "group": "control", "success": "false", "cost": "1", "retry_cost": "0"},
        ]
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        result = mcp_server.call_tool("verovo_economics_ops", {
            "action": "tool_value_analyze", "file": str(path),
        })
        self.assertFalse(result.get("isError"), result)
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual("insufficient_evidence", payload["status"])
        self.assertIn("success_rate_ci95_newcombe", payload["difference"])
        self.assertEqual("no_automatic_winner", payload["difference"]["interpretation"])


if __name__ == "__main__":
    unittest.main()
