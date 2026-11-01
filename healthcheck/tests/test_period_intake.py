"""Period intake: bundle -> case + report, with every gap kept as an open item."""
import json
import tempfile
import unittest
from pathlib import Path

from verovo_healthcheck import period_intake, task_economics

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "period-intake"


class PeriodIntakeTests(unittest.TestCase):
    def test_bundle_becomes_a_case_and_a_report(self):
        result = period_intake.intake(FIXTURE)
        self.assertTrue(result["case_written"])
        case = result["case"]
        report = result["report"]
        self.assertEqual("period-2026-10", case["case_id"])
        self.assertEqual("USD", case["currency"])
        self.assertEqual(["t1", "t2"], [task["task_id"] for task in case["tasks"]])
        self.assertEqual(2, len(case["runs"]))
        self.assertEqual(3, len(case["usage"]))
        # ch-1 is the bill-side record of financialized usage u1: reconciled, not double-counted.
        self.assertEqual(["shared-1", "ch-9"], [item["cost_id"] for item in case["cost_items"]])
        self.assertEqual(["ch-1"], report["reconciled_charges"])
        self.assertEqual("partial", report["status"])

        kinds = {item["kind"] for item in report["open_items"]}
        self.assertIn("unallocated_cost", kinds)
        self.assertIn("currency_mismatch", kinds)
        self.assertIn("unverified_outcome", kinds)
        self.assertIn("unfinancialized_cost", kinds)
        self.assertIn("ch-3", [row["row"] for row in report["currency_mismatches"]])

    def test_intake_output_is_a_valid_economics_case(self):
        result = period_intake.intake(FIXTURE)
        evaluated = task_economics.evaluate(result["case"])
        self.assertEqual(1, evaluated["coverage"]["verified_success_tasks"])
        self.assertIsNone(evaluated["totals"]["roi"])
        self.assertIn("unallocated", evaluated["totals"]["roi_reason"])
        self.assertEqual("33.50", evaluated["totals"]["fully_loaded_cost"])
        self.assertTrue(any(item["reason"].startswith("shared cost needs")
                            for item in evaluated["unallocated"]))

    def test_unverified_outcome_never_becomes_success(self):
        result = period_intake.intake(FIXTURE)
        outcomes = {item["task_id"]: item for item in result["case"]["outcomes"]}
        self.assertTrue(outcomes["t1"]["verified"])
        self.assertFalse(outcomes["t2"]["verified"])
        evaluated = task_economics.evaluate(result["case"])
        rows = {row["task_id"]: row for row in evaluated["tasks"]}
        self.assertTrue(rows["t1"]["verified_success"])
        self.assertFalse(rows["t2"]["verified_success"])

    def test_bundle_without_tasks_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            (bundle / "manifest.json").write_text(json.dumps({
                "case_id": "empty", "currency": "USD",
                "period": {"start": "2026-10-01", "end": "2026-10-31"},
                "sources": {"bills": [{"path": "bills.csv", "format": "bills-csv"}]},
            }), encoding="utf-8")
            (bundle / "bills.csv").write_text("charge_id,amount\nx-1,10.00\n", encoding="utf-8")
            result = period_intake.intake(bundle)
            self.assertFalse(result["case_written"])
            self.assertEqual("blocked", result["report"]["status"])
            kinds = {item["kind"] for item in result["report"]["open_items"]}
            self.assertIn("unallocated_cost", kinds)

    def test_manifest_rejects_unknown_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            (bundle / "manifest.json").write_text(json.dumps({
                "case_id": "x", "currency": "USD",
                "period": {"start": "2026-10-01", "end": "2026-10-31"},
                "sources": {"usage": [{"path": "u.parquet", "format": "parquet"}]},
            }), encoding="utf-8")
            with self.assertRaises(period_intake.IntakeError):
                period_intake.intake(bundle)


if __name__ == "__main__":
    unittest.main()
