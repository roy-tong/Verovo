import json
import tempfile
import unittest
from pathlib import Path

from verovo_healthcheck import mcp_server


class EconomicsOpsMcpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_manifest_exposes_local_economics_operations(self):
        tool = next(row for row in mcp_server.TOOLS if row["name"] == "verovo_economics_ops")
        actions = tool["inputSchema"]["properties"]["action"]["enum"]
        for action in ("monitor_track", "monitor_freeze_baseline", "monitor_series", "budget_check", "budget_verify", "budget_resume",
                       "optimize_verify", "human_baseline", "experiment_run", "tool_value_export"):
            self.assertIn(action, actions)
        self.assertFalse(any("enforce" in action or "rollback" in action for action in actions))

    def test_mcp_monitor_and_budget_check(self):
        usage = self.root / "usage.jsonl"
        usage.write_text(json.dumps({
            "observed_at": "2026-11-01T10:00:00Z", "tool": "search", "model": "m1",
            "task_id": "task-1", "amount": "50", "currency": "USD",
        }) + "\n", encoding="utf-8")
        state = self.root / "state.json"
        tracked = mcp_server.call_tool("verovo_economics_ops", {
            "action": "monitor_track", "file": str(usage), "state": str(state),
        })
        self.assertFalse(tracked.get("isError"), tracked)
        budget = self.root / "budget.json"
        budget.write_text(json.dumps({
            "schema": "verovo.spend-budget/1", "budget_id": "b1", "currency": "USD",
            "limit": "100", "warn_at": "0.8", "hold_at": "1",
            "period": {"start": "2026-11-01T00:00:00Z", "end": "2026-12-01T00:00:00Z"},
            "scope": {"task_id": "task-1"}, "review_on_severity": "none",
        }), encoding="utf-8")
        decision = mcp_server.call_tool("verovo_economics_ops", {
            "action": "budget_check", "state": str(state), "budget": str(budget),
        })
        text = decision["content"][0]["text"]
        self.assertEqual("allow", json.loads(text)["decision"])

    def test_mcp_monitor_series_uses_the_same_persistent_state(self):
        usage = self.root / "series-usage.jsonl"
        usage.write_text(json.dumps({
            "observed_at": "2026-11-01T10:00:00Z", "tool": "search", "model": "m1",
            "task_id": "task-1", "amount": "2.50", "currency": "USD",
        }) + "\n", encoding="utf-8")
        state = self.root / "series-state.json"
        mcp_server.call_tool("verovo_economics_ops", {
            "action": "monitor_track", "file": str(usage), "state": str(state),
        })
        result = mcp_server.call_tool("verovo_economics_ops", {
            "action": "monitor_series", "state": str(state), "granularity": "hour",
            "scope": {"task_id": "task-1"},
        })
        self.assertFalse(result.get("isError"), result)
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual("verovo.spend-time-series/1", payload["schema"])
        self.assertEqual("2.50", payload["series"][0]["amount"])

    def test_mcp_budget_verify_matches_stop_receipt_and_monitor_state(self):
        usage = self.root / "control-usage.jsonl"
        usage.write_text(json.dumps({
            "observed_at": "2026-11-01T10:00:00Z", "tool": "search", "model": "m1",
            "task_id": "task-1", "amount": "100", "currency": "USD",
        }) + "\n", encoding="utf-8")
        state = self.root / "control-state.json"
        tracked = mcp_server.call_tool("verovo_economics_ops", {
            "action": "monitor_track", "file": str(usage), "state": str(state),
        })
        self.assertFalse(tracked.get("isError"), tracked)
        budget = self.root / "control-budget.json"
        budget.write_text(json.dumps({
            "schema": "verovo.spend-budget/1", "budget_id": "b1", "currency": "USD",
            "limit": "100", "warn_at": "0.8", "hold_at": "1",
            "period": {"start": "2026-11-01T00:00:00Z", "end": "2026-12-01T00:00:00Z"},
            "scope": {"task_id": "task-1"}, "review_on_severity": "none",
        }), encoding="utf-8")
        decision = json.loads(mcp_server.call_tool("verovo_economics_ops", {
            "action": "budget_check", "state": str(state), "budget": str(budget),
        })["content"][0]["text"])
        decision["evaluated_at"] = "2026-11-01T12:00:00Z"
        decision_path = self.root / "control-decision.json"
        decision_path.write_text(json.dumps(decision), encoding="utf-8")
        receipt = self.root / "control-receipt.json"
        receipt.write_text(json.dumps({
            "schema": "verovo.spend-control-receipt/1", "receipt_id": "r1", "budget_id": "b1",
            "requested_action": "hold", "scope": {"task_id": "task-1"},
            "readback": {"status": "stopped", "observed_at": "2026-11-01T12:05:00Z",
                         "source_type": "runtime_readback", "source_ref": "runtime-7"},
            "coverage": {"start": "2026-11-01T12:05:00Z", "end": "2026-11-01T13:00:00Z",
                         "complete": True, "source_ref": "usage-9"},
        }), encoding="utf-8")
        state.write_text(json.dumps(dict(json.loads(state.read_text()),
                                         last_track_at="2026-11-01T13:00:00Z")), encoding="utf-8")
        result = mcp_server.call_tool("verovo_economics_ops", {
            "action": "budget_verify", "state": str(state),
            "decision": str(decision_path), "receipt": str(receipt),
        })
        self.assertFalse(result.get("isError"), result)
        self.assertEqual("PASS", json.loads(result["content"][0]["text"])["status"])

    def test_mcp_budget_resume_preserves_observed_spend(self):
        usage = self.root / "recovery-usage.jsonl"
        usage.write_text(json.dumps({
            "observed_at": "2026-11-01T10:00:00Z", "tool": "search", "model": "m1",
            "task_id": "task-1", "amount": "50", "currency": "USD",
        }) + "\n", encoding="utf-8")
        state = self.root / "recovery-state.json"
        mcp_server.call_tool("verovo_economics_ops", {
            "action": "monitor_track", "file": str(usage), "state": str(state),
        })
        budget = self.root / "recovery-budget.json"
        budget.write_text(json.dumps({
            "schema": "verovo.spend-budget/1", "budget_id": "b1", "currency": "USD",
            "limit": "100", "warn_at": "0.8", "hold_at": "1",
            "period": {"start": "2026-11-01T00:00:00Z", "end": "2026-12-01T00:00:00Z"},
            "scope": {"task_id": "task-1"}, "review_on_severity": "none",
        }), encoding="utf-8")
        recovery = self.root / "budget-recovery.json"
        recovery.write_text(json.dumps({
            "schema": "verovo.spend-budget-recovery/1", "recovery_id": "r1", "budget_id": "b1",
            "scope": {"task_id": "task-1"}, "confirmed_by": "owner",
            "confirmed_at": "2026-11-01T12:00:00Z", "confirmation_ref": "approval-1",
            "reason": "Retry issue fixed",
            "readback": {"status": "running", "observed_at": "2026-11-01T12:01:00Z",
                         "source_type": "runtime_readback", "source_ref": "runtime-1"},
        }), encoding="utf-8")
        out = self.root / "recovery-history.jsonl"
        result = mcp_server.call_tool("verovo_economics_ops", {
            "action": "budget_resume", "state": str(state), "budget": str(budget),
            "recovery": str(recovery), "out": str(out),
        })
        self.assertFalse(result.get("isError"), result)
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual("PASS", payload["status"])
        self.assertEqual("50", payload["observed_spend_preserved"])
        self.assertTrue(out.is_file())

    def test_mcp_human_baseline_includes_human_minutes(self):
        def write(name, group, cost, minutes):
            path = self.root / name
            rows = [{"task_id": "%s-%d" % (group, index), "group": group,
                     "success": "true", "cost": str(cost), "human_minutes": str(minutes),
                     "comparable_group": "research-v1", "observed_at": "2026-11-01T10:00:00Z",
                     "currency": "USD", "task_type": "research"} for index in (1, 2)]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            return path
        human = write("human-baseline.jsonl", "human", 0, 60)
        agent = write("agent-baseline.jsonl", "agent", 1, 6)
        contract = self.root / "human-contract.json"
        contract.write_text(json.dumps({
            "schema": "verovo.human-baseline/1", "comparison_id": "human-agent-1",
            "task_type": "research", "acceptance_rubric_ref": "rubric-1", "currency": "USD",
            "min_tasks_per_arm": 2, "human_hourly_cost": "60",
            "human_cost_basis": {"source_ref": "payroll-1", "method": "loaded_hourly_cost",
                                 "includes_loaded_labor": True},
            "comparability": {"same_task_set": True, "same_acceptance_rubric": True,
                              "same_currency": True, "same_quality_requirements": True},
            "quality_guardrail": {"max_success_rate_drop": "0.05"},
        }), encoding="utf-8")
        result = mcp_server.call_tool("verovo_economics_ops", {
            "action": "human_baseline", "human": str(human),
            "agent": str(agent), "contract": str(contract),
        })
        self.assertFalse(result.get("isError"), result)
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual("lower_observed_cost", payload["decision"])
        self.assertEqual("7.00", payload["agent"]["fully_loaded_cost_per_task"])

    def test_mcp_controlled_task_run_is_bounded_and_local(self):
        import sys
        script = self.root / "controlled-task.py"
        script.write_text("import os\nprint('ok-'+os.environ['OVO_EXPERIMENT_TASK_ID'])\n",
                          encoding="utf-8")
        contract = self.root / "controlled-run.json"
        contract.write_text(json.dumps({
            "schema": "verovo.controlled-task-run/1", "experiment_id": "mcp-exp",
            "task_type": "research", "task_ids": ["t1", "t2"],
            "authorization": {"authorized_by": "owner", "local_only": True},
            "limits": {"timeout_seconds": 5, "max_output_bytes": 4096},
            "success": {"exit_code": 0, "stdout_contains": ["ok-"]},
            "arms": [{"arm_id": "control", "role": "control", "argv": [sys.executable, str(script)]},
                     {"arm_id": "treatment", "role": "treatment", "argv": [sys.executable, str(script)]}],
        }), encoding="utf-8")
        out = self.root / "controlled-out"
        result = mcp_server.call_tool("verovo_economics_ops", {
            "action": "experiment_run", "contract": str(contract), "out": str(out),
        })
        self.assertFalse(result.get("isError"), result)
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual("PASS", payload["status"])
        self.assertEqual(4, payload["summary"]["observations"])
        self.assertTrue(all(row["cost"] is None for row in payload["observations"]))

    def test_mcp_optimization_review(self):
        def write(name, rows):
            path = self.root / name
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            return path
        def row(task_id, day, cost):
            return {"task_id": task_id, "group": "treatment", "success": "true",
                    "cost": str(cost), "retry_cost": "0", "comparable_group": "g1",
                    "observed_at": "2026-11-%02dT10:00:00Z" % day,
                    "currency": "USD", "task_type": "research"}
        baseline = write("baseline.jsonl", [row("b1", 1, 4), row("b2", 2, 4)])
        final = write("final.jsonl", [row("f1", 11, 3), row("f2", 12, 3)])
        contract = self.root / "review.json"
        contract.write_text(json.dumps({
            "schema": "verovo.optimization-review/1", "optimization_id": "o1", "action_ref": "a1",
            "task_type": "research", "acceptance_rubric_ref": "r1", "currency": "USD",
            "min_tasks_per_period": 2,
            "comparability": {"same_task_set": True, "same_acceptance_rubric": True, "same_currency": True},
            "baseline_window": {"start": "2026-11-01T00:00:00Z", "end": "2026-11-10T23:59:59Z"},
            "final_window": {"start": "2026-11-11T00:00:00Z", "end": "2026-11-20T23:59:59Z"},
            "change_effective_at": "2026-11-11T00:00:00Z",
            "quality_guardrail": {"max_success_rate_drop": "0.05"},
        }), encoding="utf-8")
        result = mcp_server.call_tool("verovo_economics_ops", {
            "action": "optimize_verify", "baseline": str(baseline),
            "final": str(final), "contract": str(contract),
        })
        self.assertEqual("keep", json.loads(result["content"][0]["text"])["decision"])

    def test_mcp_tool_value_export_writes_new_shared_contract(self):
        tasks = self.root / "tasks.jsonl"
        tasks.write_text(json.dumps({
            "task_id": "t1", "group": "treatment", "success": "true", "cost": "2.50",
            "retry_cost": "0", "comparable_group": "research-v1",
            "observed_at": "2026-11-21T10:00:00Z", "currency": "USD", "task_type": "research",
        }) + "\n", encoding="utf-8")
        design = self.root / "design.json"
        design.write_text(json.dumps({
            "experiment_id": "e1", "task_id": "procurement-1", "title": "Synthetic comparison",
            "hypothesis": {"statement": "Tool B lowers cost per verified success.",
                           "falsification_condition": "It does not."},
            "design": {"comparability": {"same_task_set": True}},
            "window": {"start": "2026-11-20T00:00:00Z", "end": "2026-11-25T23:59:59Z"},
            "stop_conditions": ["Stop after the registered tasks."],
            "external_actions": False, "authorization_refs": [],
            "registered_by": "buyer", "registered_at": "2026-11-19T00:00:00Z",
            "evidence_grade": "observed", "eligible": True,
        }), encoding="utf-8")
        out = self.root / "experiment.json"
        result = mcp_server.call_tool("verovo_economics_ops", {
            "action": "tool_value_export", "file": str(tasks),
            "design": str(design), "out": str(out),
        })
        self.assertFalse(result.get("isError"), result)
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual("exported", payload["status"])
        self.assertEqual(2, payload["observations"])
        self.assertEqual("ovo.experiment-contract/1", json.loads(out.read_text())["schema"])

    def test_skill_contains_new_economics_paths(self):
        text = (Path(__file__).resolve().parents[2] /
                "skills/verovo/SKILL.md").read_text(encoding="utf-8")
        for phrase in ("monitor track", "monitor series", "budget check", "budget verify", "budget resume", "optimize-verify", "human-baseline", "experiment run", "tool-value-export"):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
