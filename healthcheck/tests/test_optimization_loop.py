import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from verovo_healthcheck import mcp_server, optimization_loop

REPO = Path(__file__).resolve().parents[2]


def task(task_id, day, cost, success="true"):
    return {
        "task_id": task_id, "group": "treatment", "success": success,
        "cost": str(cost), "retry_cost": "0", "comparable_group": "research-v1",
        "observed_at": "2026-11-%02dT10:00:00Z" % day,
        "currency": "USD", "task_type": "research",
    }


class OptimizationLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.baseline = self.write("baseline.jsonl", [task("b1", 1, 4), task("b2", 2, 4)])
        self.final = self.write("final.jsonl", [task("f1", 11, 3), task("f2", 12, 3)])
        self.directory = self.root / "loop"

    def write(self, name, rows):
        path = self.root / name
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return path

    def spec(self):
        return {
            "schema": "verovo.optimization-loop/1", "loop_id": "loop-1",
            "owner": "buyer-owner", "registered_at": "2026-11-21T00:00:00Z",
            "interval_hours": 24, "baseline": str(self.baseline), "final": str(self.final),
            "authorization": {"authorized_by": "buyer-owner", "local_only": True},
            "review": {
                "schema": "verovo.optimization-review/1", "optimization_id": "route-change-1",
                "action_ref": "approved-route-change-1", "task_type": "research",
                "acceptance_rubric_ref": "rubric-1", "currency": "USD", "min_tasks_per_period": 2,
                "comparability": {"same_task_set": True, "same_acceptance_rubric": True,
                                  "same_currency": True},
                "baseline_window": {"start": "2026-11-01T00:00:00Z", "end": "2026-11-10T23:59:59Z"},
                "final_window": {"start": "2026-11-11T00:00:00Z", "end": "2026-11-20T23:59:59Z"},
                "change_effective_at": "2026-11-11T00:00:00Z",
                "quality_guardrail": {"max_success_rate_drop": "0.05"},
                "human_review": {"accepted": False},
            },
        }

    def test_due_ticks_persist_results_and_source_changes(self):
        optimization_loop.create(self.spec(), self.directory)
        first = optimization_loop.tick(self.directory, now="2026-11-21T00:00:00Z")
        self.assertEqual("completed", first["status"])
        self.assertEqual("keep", first["runs"][0]["review"]["decision"])
        no_due = optimization_loop.tick(self.directory, now="2026-11-21T00:00:00Z")
        self.assertEqual("no_due_loop", no_due["status"])
        self.write("final.jsonl", [task("f1", 11, 2), task("f2", 12, 2)])
        second = optimization_loop.tick(self.directory, now="2026-11-22T00:00:00Z")
        self.assertTrue(second["runs"][0]["source_changed"])
        self.assertEqual("2.00", second["runs"][0]["review"]["observed_change"]["cost_per_task_saving"])
        report = optimization_loop.status(self.directory, now="2026-11-22T00:00:00Z")
        self.assertEqual(2, report["run_count"])
        self.assertFalse(report["due"])
        self.assertEqual("external_tick_required", report["scheduler"])

    def test_quality_regression_requires_but_never_applies_rollback(self):
        optimization_loop.create(self.spec(), self.directory)
        self.write("final.jsonl", [task("f1", 11, 2, "false"), task("f2", 12, 2)])
        result = optimization_loop.tick(self.directory, now="2026-11-21T00:00:00Z")
        run = result["runs"][0]
        self.assertEqual("rollback", run["review"]["decision"])
        self.assertTrue(run["rollback_required"])
        self.assertFalse(run["rollback_applied"])
        self.assertFalse(run["external_runtime_contacted"])
        self.assertEqual("rollback_required", optimization_loop.status(self.directory)["state"])

    def test_pause_resume_and_registration_drift_are_enforced(self):
        optimization_loop.create(self.spec(), self.directory)
        optimization_loop.set_paused(self.directory, True, now="2026-11-21T00:00:00Z")
        paused = optimization_loop.tick(self.directory, now="2026-11-22T00:00:00Z")
        self.assertEqual("paused", paused["status"])
        optimization_loop.set_paused(self.directory, False, now="2026-11-22T00:00:00Z")
        resumed = optimization_loop.tick(self.directory, now="2026-11-22T00:00:00Z")
        self.assertEqual(1, len(resumed["runs"]))
        path = self.directory / optimization_loop.SPEC_NAME
        changed = json.loads(path.read_text(encoding="utf-8"))
        changed["interval_hours"] = 1
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            optimization_loop.status(self.directory)

    def test_cli_create_tick_and_status(self):
        contract = self.root / "loop.json"
        contract.write_text(json.dumps(self.spec()), encoding="utf-8")
        commands = [
            ["create", "--contract", str(contract), "--out", str(self.directory)],
            ["tick", "--dir", str(self.directory), "--at", "2026-11-21T00:00:00Z"],
            ["status", "--dir", str(self.directory), "--at", "2026-11-21T00:00:00Z"],
        ]
        outputs = []
        for action in commands:
            result = subprocess.run([
                sys.executable, str(REPO / "healthcheck/verovo"), "optimize-loop", action[0], *action[1:],
            ], cwd=REPO, text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr + result.stdout)
            outputs.append(json.loads(result.stdout))
        self.assertEqual("completed", outputs[1]["status"])
        self.assertEqual(1, outputs[2]["run_count"])

    def test_mcp_exposes_explicit_local_loop_ticks(self):
        contract = self.root / "loop.json"
        contract.write_text(json.dumps(self.spec()), encoding="utf-8")
        created = mcp_server.call_tool("verovo_economics_ops", {
            "action": "optimization_loop_create", "contract": str(contract), "out": str(self.directory),
        })
        self.assertFalse(created.get("isError"), created)
        ticked = mcp_server.call_tool("verovo_economics_ops", {
            "action": "optimization_loop_tick", "dir": str(self.directory),
            "at": "2026-11-21T00:00:00Z",
        })
        self.assertFalse(ticked.get("isError"), ticked)
        payload = json.loads(ticked["content"][0]["text"])
        self.assertEqual("keep", payload["runs"][0]["review"]["decision"])
        status = mcp_server.call_tool("verovo_economics_ops", {
            "action": "optimization_loop_status", "dir": str(self.directory),
        })
        self.assertEqual(1, json.loads(status["content"][0]["text"])["run_count"])


if __name__ == "__main__":
    unittest.main()
