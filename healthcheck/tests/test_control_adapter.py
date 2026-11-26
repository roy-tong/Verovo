import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ControlAdapterTest(unittest.TestCase):
    def spec(self):
        return {
            "schema": "verovo.spend-control-adapter/1",
            "adapter_id": "local-runtime-1",
            "action": "hold",
            "budget_id": "b1",
            "scope": {"task_id": "t1"},
            "authorization": {"authorized_by": "owner", "authorization_ref": "approval-1"},
            "argv": ["runtime-hold"],
            "readback_argv": ["runtime-status"],
            "timeout_seconds": 5,
            "external_runtime": False,
        }

    def decision(self):
        return {"decision": "hold", "budget_id": "b1", "scope": {"task_id": "t1"}}

    def test_hold_executes_only_with_matching_decision_and_reads_back(self):
        self.assertIsNotNone(importlib.util.find_spec("verovo_healthcheck.control_adapter"))
        from verovo_healthcheck import control_adapter
        calls = []
        def runner(**kwargs):
            calls.append(kwargs)
            if kwargs["argv"] == ["runtime-status"]:
                return {"exit_code": 0, "error": "", "stdout":
                        b'{"status":"stopped","observed_at":"2026-11-26T00:00:00Z","source_ref":"runtime-1"}',
                        "stderr": b""}
            return {"exit_code": 0, "error": "", "stdout": b"held", "stderr": b""}
        result = control_adapter.apply(self.spec(), self.decision(), process_runner=runner)
        self.assertEqual("APPLIED", result["status"])
        self.assertEqual("stopped", result["receipt"]["readback"]["status"])
        self.assertEqual(2, len(calls))
        self.assertTrue(result["adapter_executed"])
        self.assertFalse(result["external_runtime_contacted"])

    def test_cli_applies_local_fake_runtime_and_writes_receipt(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        runtime = root / "runtime.py"
        runtime.write_text(
            "import json,sys\n"
            "state=sys.argv[2]\n"
            "if sys.argv[1]=='hold':\n"
            " open(state,'w').write(json.dumps({'status':'stopped','observed_at':'2026-11-26T00:00:00Z','source_ref':'local-runtime'}))\n"
            "else:\n print(open(state).read())\n", encoding="utf-8")
        state = root / "state.json"
        spec = self.spec()
        spec["argv"] = [sys.executable, str(runtime), "hold", str(state)]
        spec["readback_argv"] = [sys.executable, str(runtime), "status", str(state)]
        adapter = root / "adapter.json"
        adapter.write_text(json.dumps(spec), encoding="utf-8")
        decision = root / "decision.json"
        decision.write_text(json.dumps(self.decision()), encoding="utf-8")
        receipt = root / "receipt.json"
        result = subprocess.run([
            sys.executable, str(ROOT / "healthcheck/verovo"), "budget", "apply",
            "--adapter", str(adapter), "--decision", str(decision),
            "--receipt-out", str(receipt), "--json",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual("APPLIED", json.loads(result.stdout)["status"])
        self.assertEqual("stopped", json.loads(receipt.read_text())["readback"]["status"])

    def test_allow_decision_cannot_invoke_adapter(self):
        from verovo_healthcheck import control_adapter
        called = []
        with self.assertRaisesRegex(ValueError, "hold"):
            control_adapter.apply(self.spec(), dict(self.decision(), decision="allow"),
                                  process_runner=lambda **kwargs: called.append(kwargs))
        self.assertEqual([], called)


if __name__ == "__main__":
    unittest.main()
