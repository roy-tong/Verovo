"""Cost drivers, scenario simulation and ROI-driven proposals."""
import copy
import unittest

from verovo_healthcheck import cost_drivers, optimization, task_economics

CASE = {
    "schema_version": "1.0",
    "case_id": "driver-case",
    "currency": "USD",
    "period": {"start": "2026-10-01", "end": "2026-10-31"},
    "allocation_rules": [{"id": "equal", "version": "1", "method": "equal"}],
    "tasks": [{"task_id": "t1", "type": "support"}, {"task_id": "t2", "type": "sales"}],
    "runs": [{"run_id": "r1", "task_id": "t1"}, {"run_id": "r2", "task_id": "t2"}],
    "usage": [{"usage_id": "u1", "run_id": "r1", "cost": "5.00", "currency": "USD",
               "financialized": True, "source_ref": "trace:u1", "model": "m1"}],
    "documents": [],
    "cost_items": [
        {"cost_id": "c1", "task_id": "t1", "amount": "30.00", "currency": "USD",
         "source_ref": "bill:1", "vendor": "A"},
        {"cost_id": "c2", "task_id": "t2", "amount": "10.00", "currency": "USD",
         "source_ref": "bill:2", "vendor": "B"},
    ],
    "outcomes": [
        {"task_id": "t1", "status": "success", "verified": True,
         "source_ref": "outcome:1", "value": "100.00", "value_verified": True, "currency": "USD"},
    ],
}


class CostDriverTests(unittest.TestCase):
    def test_buckets_and_shares(self):
        payload = cost_drivers.analyze(CASE)
        self.assertEqual("45.00", payload["total_recorded_cost"])
        vendors = {item["key"]: item for item in payload["drivers"] if item["dimension"] == "vendor"}
        self.assertEqual("30.00", vendors["A"]["cost"])
        self.assertAlmostEqual(30 / 45, vendors["A"]["share"], places=4)
        models = {item["key"]: item for item in payload["drivers"] if item["dimension"] == "model"}
        self.assertIn("m1", models)
        self.assertIn("not-recorded", models)

    def test_unallocated_cost_is_not_silently_spread(self):
        case = copy.deepcopy(CASE)
        case["cost_items"].append({"cost_id": "c3", "amount": "9.00", "currency": "USD",
                                   "source_ref": "bill:3", "vendor": "C"})
        payload = cost_drivers.analyze(case)
        self.assertEqual("54.00", payload["total_recorded_cost"])
        self.assertEqual("9.00", payload["unattributed_cost"])
        self.assertEqual("45.00", payload["attributable_cost"])

    def test_retry_cost_is_reported_when_it_cannot_be_separated(self):
        payload = cost_drivers.analyze(CASE)
        retry = {item["key"]: item for item in payload["drivers"] if item["dimension"] == "retry"}
        self.assertIn("not-separable", retry)
        self.assertTrue(any(risk["id"] == "retry_unobserved" for risk in payload["risks"]))


class ScenarioTests(unittest.TestCase):
    def test_scale_cost_recomputes_economics(self):
        result = optimization.simulate(CASE, {
            "id": "halve-a", "description": "假设 A 成本减半",
            "rules": [{"match": {"vendor": "A"}, "action": "scale_cost", "factor": 0.5}],
        })
        self.assertEqual("computed", result["status"])
        self.assertEqual("45.00", result["baseline"]["fully_loaded_cost"])
        self.assertEqual("30.00", result["scenario"]["fully_loaded_cost"])
        self.assertEqual("-15.00", result["delta"]["fully_loaded_cost"])
        self.assertIn("不是预测", result["boundary"])

    def test_drop_removes_a_bucket(self):
        result = optimization.simulate(CASE, {
            "id": "drop-b", "rules": [{"match": {"vendor": "B"}, "action": "drop"}],
        })
        self.assertEqual("35.00", result["scenario"]["fully_loaded_cost"])

    def test_invalid_scenario_is_reported_not_guessed(self):
        result = optimization.simulate(CASE, {"id": "bad", "rules": [{"action": "teleport"}]})
        self.assertEqual("invalid", result["status"])

    def test_proposals_carry_evidence_and_human_gate(self):
        payload = optimization.propose(CASE, scenarios={"scenarios": [{
            "id": "halve-a",
            "rules": [{"match": {"vendor": "A"}, "action": "scale_cost", "factor": 0.5}],
        }]})
        self.assertEqual("ok", payload["status"])
        ids = {item["id"] for item in payload["proposals"]}
        self.assertIn("scenario:halve-a", ids)
        self.assertTrue(any(item["id"].startswith("vendor-concentration") for item in payload["proposals"]))
        for item in payload["proposals"]:
            self.assertTrue(item["requires_human"])
            self.assertFalse(item["automatic_apply"])
            self.assertTrue(item["verification_plan"])

    def test_unallocated_blocks_roi_and_is_the_first_advice(self):
        case = copy.deepcopy(CASE)
        case["cost_items"].append({"cost_id": "c3", "amount": "9.00", "currency": "USD",
                                   "source_ref": "bill:3", "vendor": "C"})
        payload = optimization.propose(case)
        self.assertEqual("reconcile-unallocated", payload["proposals"][0]["id"])
        self.assertIsNone(task_economics.evaluate(case)["totals"]["roi"])


if __name__ == "__main__":
    unittest.main()
