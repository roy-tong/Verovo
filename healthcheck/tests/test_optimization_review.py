import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from verovo_healthcheck import optimization_review, tool_value

ROOT = Path(__file__).resolve().parents[2]


def task(task_id, day, cost, success="true", *, group="research-v1", currency="USD"):
    return {
        "task_id": task_id, "group": "treatment", "success": success,
        "cost": str(cost), "retry_cost": "0", "comparable_group": group,
        "observed_at": "2026-11-%02dT10:00:00Z" % day,
        "currency": currency, "task_type": "research",
    }


def contract(minimum=2, reviewed=True):
    return {
        "schema": "verovo.optimization-review/1",
        "optimization_id": "route-change-1",
        "action_ref": "approved-model-route-change-1",
        "task_type": "research",
        "acceptance_rubric_ref": "research-acceptance-v1",
        "currency": "USD",
        "min_tasks_per_period": minimum,
        "comparability": {
            "same_task_set": True,
            "same_acceptance_rubric": True,
            "same_currency": True,
        },
        "baseline_window": {"start": "2026-11-01T00:00:00Z", "end": "2026-11-10T23:59:59Z"},
        "final_window": {"start": "2026-11-11T00:00:00Z", "end": "2026-11-20T23:59:59Z"},
        "change_effective_at": "2026-11-11T00:00:00Z",
        "quality_guardrail": {"max_success_rate_drop": "0.05"},
        "human_review": ({"reviewed_by": "buyer-owner", "reviewed_at": "2026-11-21T00:00:00Z",
                          "accepted": True} if reviewed else {"accepted": False}),
    }


class OptimizationReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, rows):
        path = Path(self.tmp.name) / name
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        return path

    def evaluate(self, baseline, final, spec=None):
        baseline_path = self.write("baseline.jsonl", baseline)
        final_path = self.write("final.jsonl", final)
        return optimization_review.evaluate(
            tool_value.load_tasks(baseline_path), tool_value.load_tasks(final_path),
            spec or contract())

    def test_lower_cost_with_quality_parity_supports_keep(self):
        baseline = [task("b1", 1, 4), task("b2", 2, 4)]
        final = [task("f1", 11, 3), task("f2", 12, 3)]
        got = self.evaluate(baseline, final)
        self.assertEqual("keep", got["decision"])
        self.assertEqual("1.00", got["observed_change"]["cost_per_task_saving"])
        self.assertEqual("buyer_confirmed_observed_difference", got["saving_verification"])
        self.assertEqual("not_claimed", got["causality"])
        self.assertFalse(got["control"]["rollback_applied"])

    def test_reversed_or_overlapping_windows_are_rejected(self):
        spec = contract()
        spec["baseline_window"] = {"start": "2026-11-11T00:00:00Z", "end": "2026-11-12T23:59:59Z"}
        spec["final_window"] = {"start": "2026-11-01T00:00:00Z", "end": "2026-11-02T23:59:59Z"}
        with self.assertRaisesRegex(ValueError, "final_window must start after baseline_window"):
            self.evaluate([task("b1", 11, 4), task("b2", 12, 4)],
                          [task("f1", 1, 3), task("f2", 2, 3)], spec)
        spec = contract()
        spec["final_window"]["start"] = "2026-11-10T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "final_window must start after baseline_window"):
            self.evaluate([task("b1", 1, 4), task("b2", 2, 4)],
                          [task("f1", 11, 3), task("f2", 12, 3)], spec)

    def test_missing_change_effective_at_cannot_support_keep(self):
        spec = contract()
        del spec["change_effective_at"]
        got = self.evaluate([task("b1", 1, 4), task("b2", 2, 4)],
                            [task("f1", 11, 3), task("f2", 12, 3)], spec)
        self.assertEqual("inconclusive", got["decision"])
        self.assertIn("CHANGE_EFFECTIVE_AT_UNKNOWN", got["reason_codes"])
        self.assertEqual("not_verified", got["saving_verification"])

    def test_quality_drop_beyond_guardrail_recommends_rollback(self):
        baseline = [task("b1", 1, 4), task("b2", 2, 4)]
        final = [task("f1", 11, 2, "false"), task("f2", 12, 2)]
        got = self.evaluate(baseline, final)
        self.assertEqual("rollback", got["decision"])
        self.assertIn("QUALITY_GUARDRAIL_BREACHED", got["reason_codes"])
        self.assertEqual("not_verified", got["saving_verification"])

    def test_insufficient_sample_is_inconclusive(self):
        baseline = [task("b1", 1, 4), task("b2", 2, 4)]
        final = [task("f1", 11, 3)]
        got = self.evaluate(baseline, final)
        self.assertEqual("inconclusive", got["decision"])
        self.assertIn("INSUFFICIENT_TASKS", got["reason_codes"])

    def test_no_verified_success_cannot_produce_efficiency_keep(self):
        baseline = [task("b1", 1, 4), task("b2", 2, 4)]
        final = [task("f1", 11, 2, "false"), task("f2", 12, 2, "false")]
        spec = contract()
        spec["quality_guardrail"]["max_success_rate_drop"] = "1"
        got = self.evaluate(baseline, final, spec)
        self.assertEqual("inconclusive", got["decision"])
        self.assertIn("COST_PER_SUCCESS_UNKNOWN", got["reason_codes"])

    def test_cli_review_is_read_only(self):
        baseline_path = self.write("baseline.jsonl", [task("b1", 1, 4), task("b2", 2, 4)])
        final_path = self.write("final.jsonl", [task("f1", 11, 3), task("f2", 12, 3)])
        contract_path = Path(self.tmp.name) / "contract.json"
        contract_path.write_text(json.dumps(contract()), encoding="utf-8")
        result = subprocess.run([
            sys.executable, str(ROOT / "healthcheck/verovo"), "optimize-verify",
            "--baseline", str(baseline_path), "--final", str(final_path),
            "--contract", str(contract_path), "--json",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual("keep", payload["decision"])
        self.assertEqual("review_only", payload["control"]["mode"])
        self.assertFalse(payload["control"]["external_runtime_contacted"])


if __name__ == "__main__":
    unittest.main()
