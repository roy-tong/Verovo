"""Canonical finance P0/P1 regressions on a complex synthetic period."""
import csv
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

HEALTHCHECK_ROOT = Path(__file__).resolve().parents[1]
if str(HEALTHCHECK_ROOT) not in sys.path:
    sys.path.insert(0, str(HEALTHCHECK_ROOT))

from verovo_healthcheck import canonical_finance, period_intake

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "canonical-finance"


class CanonicalFinanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bundle = Path(self.temp.name) / "bundle"
        shutil.copytree(FIXTURE, self.bundle)

    def json(self, name):
        return json.loads((self.bundle / name).read_text(encoding="utf-8"))

    def write_json(self, name, value):
        (self.bundle / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def rows(self, name):
        with (self.bundle / name).open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            return reader.fieldnames, list(reader)

    def write_rows(self, name, fields, rows):
        with (self.bundle / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def bill(self, charge_id, **changes):
        fields, rows = self.rows("bills.csv")
        fields += [key for key in changes if key not in fields]
        for row in rows:
            if row["charge_id"] == charge_id:
                row.update(changes)
        self.write_rows("bills.csv", fields, rows)

    def outcome(self, outcome_id, **changes):
        fields, rows = self.rows("outcomes.csv")
        fields += [key for key in changes if key not in fields]
        for row in rows:
            if row["outcome_id"] == outcome_id:
                row.update(changes)
        self.write_rows("outcomes.csv", fields, rows)

    def usage(self, usage_id, **changes):
        path = self.bundle / "usage.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        for row in rows:
            if row["usage_id"] == usage_id:
                row.update(changes)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def conclusion(self, result, name, row_id):
        return next(row for row in result["evidence_conclusions"][name] if row["id"] == row_id)

    def test_shared_key_seats_delivery_and_verified_success_cost(self):
        result = canonical_finance.run(self.bundle)
        totals = result["economics"]["totals"]
        self.assertTrue(result["synthetic"])
        self.assertEqual("86.00", totals["fully_loaded_cost"])
        self.assertEqual("86.00", totals["cost_per_verified_success"])
        self.assertEqual("100.00", totals["verified_business_value"])
        self.assertIsNone(totals["roi"], "the vendor's extra claimed value keeps ROI open")
        tasks = {row["task_id"]: row for row in result["economics"]["tasks"]}
        self.assertEqual("58.00", tasks["t1"]["fully_loaded_cost"])
        self.assertEqual("28.00", tasks["t2"]["fully_loaded_cost"])
        self.assertIs(tasks["t1"]["verified_success"], True)
        self.assertIs(tasks["t2"]["verified_success"], False)
        coverage = result["economics"]["allocation_coverage"][0]
        self.assertEqual("unknown", result["intake_report"]["reconciliation"]["status"])
        self.assertNotIn("seat-fee", result["intake_report"]["reconciliation"]["reconciled_charges"],
                         "a bill-only rule recalculation is not cross-source reconciliation")
        self.assertNotIn("shared-key", result["intake_report"]["reconciliation"]["reconciled_charges"])
        self.assertEqual("PASS", self.conclusion(result, "Rule Repriced", "seat-fee")["status"])
        self.assertEqual("UNPROVABLE", self.conclusion(result, "Invoice Reconciled", "seat-fee")["status"])
        self.assertEqual("complete", coverage["status"],
                         "shared-key allocation is complete even though its invoice has no pricing recount")
        self.assertEqual(
            Decimal(coverage["allocatable_amount"]),
            Decimal(coverage["allocated_amount"])
            + Decimal(coverage["unallocated_amount"])
            + Decimal(coverage["non_allocatable_amount"]),
        )
        self.assertEqual("86.00", coverage["allocated_amount"])
        self.assertEqual("0.00", coverage["unallocated_amount"])
        self.assertEqual(
            {"shared-key-usage", "seat-assignments"},
            {row["driver"]["id"] for row in result["economics"]["allocation"]},
        )
        self.assertEqual("PASS", self.conclusion(result, "Delivery Accepted", "buyer-accept-t1")["status"])
        self.assertEqual("FAIL", self.conclusion(result, "Delivery Accepted", "buyer-reject-t2")["status"])

    def test_two_vendor_exports_never_become_buyer_verified(self):
        result = canonical_finance.run(self.bundle)
        vendor = self.conclusion(result, "Vendor Reconciled", "ch-api-1")
        buyer = self.conclusion(result, "Buyer Verified", "ch-api-1")
        self.assertEqual("PASS", vendor["status"])
        self.assertEqual("same_vendor_control_domain", vendor["independence"])
        self.assertEqual("UNPROVABLE", buyer["status"])
        self.assertIn("repeated vendor exports do not qualify", buyer["reason"])
        independent = self.conclusion(result, "Buyer Verified", "ch-buyer")
        self.assertEqual("PASS", independent["status"])
        self.assertEqual("buyer_independent", independent["independence"])

    def test_finance_only_period_is_complete_without_business_outcomes(self):
        manifest = self.json("manifest.json")
        del manifest["sources"]["outcomes"]
        self.write_json("manifest.json", manifest)
        result = canonical_finance.run(self.bundle)
        self.assertEqual("complete", result["finance_status"])
        self.assertEqual("not_applicable", result["verification_status"])
        self.assertEqual("not_applicable", result["delivery_status"])
        self.assertEqual("not_applicable", result["economics_status"])
        self.assertEqual("complete", result["status"])
        self.assertEqual("86.00", result["economics"]["totals"]["fully_loaded_cost"])
        self.assertEqual("unknown", result["economics"]["totals"]["success_denominator_status"])
        self.assertIsNone(result["economics"]["totals"]["cost_per_verified_success"])

    def test_decisive_delivery_failure_is_complete_not_partial(self):
        fields, rows = self.rows("outcomes.csv")
        self.write_rows("outcomes.csv", fields,
                        [row for row in rows if row["outcome_id"] != "vendor-claim-t2"])
        result = canonical_finance.run(self.bundle)
        self.assertEqual("FAIL", self.conclusion(result, "Delivery Accepted", "buyer-reject-t2")["status"])
        self.assertEqual("complete", result["finance_status"])
        self.assertEqual("complete", result["verification_status"])
        self.assertEqual("complete", result["delivery_status"])
        self.assertEqual("complete", result["economics_status"])
        self.assertEqual("complete", result["status"])

    def test_present_but_empty_outcomes_source_does_not_hide_economics_gap(self):
        fields, _ = self.rows("outcomes.csv")
        self.write_rows("outcomes.csv", fields, [])
        result = canonical_finance.run(self.bundle)
        self.assertEqual("complete", result["finance_status"])
        self.assertEqual("partial", result["verification_status"])
        self.assertEqual("partial", result["delivery_status"])
        self.assertEqual("partial", result["economics_status"])
        self.assertEqual("partial", result["status"])

    def test_vendor_control_cannot_be_relabelled_as_buyer_independence(self):
        valid_buyer = {
            "source_party": "buyer", "source_affiliation": "buyer",
            "control_domain": "buyer", "collector_party": "buyer",
            "collection_method": "buyer_jsonl", "independence": "buyer_independent",
            "confirmation_process": "buyer_recomputed", "source_ref": "buyer-usage:u1",
        }
        attacks = {
            "known_vendor_path": {"source_ref": "vendor_export:u1"},
            "known_vendor_collection": {"collection_method": "vendor_export"},
            "control_domain_conflict": {"control_domain": "vendor"},
            "collector_conflict": {"collector_party": "vendor"},
            "vendor_confirmation": {"confirmation_process": "vendor_export"},
            "self_declared_process": {"confirmation_process": "self_declared"},
            "missing_affiliation": {"source_affiliation": "unknown"},
        }
        for label, attack in attacks.items():
            with self.subTest(label=label):
                self.usage("u1", **dict(valid_buyer, **attack))
                result = canonical_finance.run(self.bundle)
                buyer = self.conclusion(result, "Buyer Verified", "ch-api-1")
                self.assertEqual("UNPROVABLE", buyer["status"])
                self.assertEqual("buyer_independent", buyer["independence_assertion"])
                self.assertEqual("not_established", buyer["independence_basis"])

    def test_manifest_vendor_path_fails_closed_despite_buyer_row_claims(self):
        self.usage("u1", source_ref="buyer-usage:u1", source_party="buyer",
                   source_affiliation="buyer", control_domain="buyer", collector_party="buyer",
                   collection_method="buyer_jsonl", independence="buyer_independent",
                   confirmation_process="buyer_recomputed")
        (self.bundle / "usage.jsonl").rename(self.bundle / "vendor_export.jsonl")
        manifest = self.json("manifest.json")
        manifest["sources"]["usage"][0]["path"] = "vendor_export.jsonl"
        self.write_json("manifest.json", manifest)
        result = canonical_finance.run(self.bundle)
        buyer = self.conclusion(result, "Buyer Verified", "ch-api-1")
        self.assertEqual("UNPROVABLE", buyer["status"])
        self.assertEqual("vendor_export.jsonl", buyer["source_path"])
        self.assertEqual("not_established", buyer["independence_basis"])

    def test_vendor_path_cannot_forge_delivery_acceptance(self):
        self.outcome("buyer-accept-t1", source_ref="vendor_export:accept-t1")
        result = canonical_finance.run(self.bundle)
        self.assertEqual(
            "UNPROVABLE", self.conclusion(result, "Delivery Accepted", "buyer-accept-t1")["status"])
        self.assertEqual("complete", result["finance_status"])
        self.assertEqual("partial", result["verification_status"])
        self.assertEqual("partial", result["delivery_status"])
        self.assertIsNone(result["economics"]["totals"]["cost_per_verified_success"])

    def test_buyer_repricing_pass_does_not_hide_vendor_invoice_difference(self):
        self.bill("ch-buyer", amount="3.00")
        result = canonical_finance.run(self.bundle)
        row = next(row for row in result["intake_report"]["reconciliation_rows"]
                   if row["charge_id"] == "ch-buyer")
        self.assertEqual("matched", row["recalculation"]["usage"]["status"])
        self.assertEqual("difference", row["recalculation"]["bill"]["status"])
        self.assertEqual("FAIL", self.conclusion(result, "Rule Repriced", "ch-buyer")["status"])
        reconciled = self.conclusion(result, "Invoice Reconciled", "ch-buyer")
        self.assertEqual("FAIL", reconciled["status"])
        self.assertEqual("distinct_source_ledger_comparison", reconciled["basis"])
        legacy = self.conclusion(result, "Invoice Matched", "ch-buyer")
        self.assertEqual("FAIL", legacy["status"])
        self.assertEqual("Rule Repriced", legacy["alias_of"])
        self.assertEqual("approved_rule_recalculation", legacy["basis"])
        self.assertEqual("PASS", self.conclusion(result, "Buyer Verified", "ch-buyer")["status"])
        self.assertEqual("partial", result["finance_status"])
        self.assertEqual("partial", result["status"])
        self.assertIsNone(result["economics"]["totals"]["fully_loaded_cost"])

    def test_invoice_reconciliation_pass_does_not_hide_rule_difference(self):
        self.bill("ch-api-1", amount="21.00")
        self.usage("u1", cost="21.00")
        result = canonical_finance.run(self.bundle)
        self.assertEqual("FAIL", self.conclusion(result, "Rule Repriced", "ch-api-1")["status"])
        self.assertEqual("PASS", self.conclusion(result, "Invoice Reconciled", "ch-api-1")["status"])
        self.assertEqual("PASS", self.conclusion(result, "Vendor Reconciled", "ch-api-1")["status"])
        self.assertEqual("partial", result["finance_status"])
        self.assertIsNone(result["economics"]["totals"]["fully_loaded_cost"])

    def test_usage_lane_difference_blocks_complete_when_bill_lane_is_unknown(self):
        # Frozen failure sample (review 2026-11-04): a bill row that cannot be
        # repriced (no mapped pricing rule) used to hide a usage-lane rule
        # difference — the known-wrong amount stayed inside a finance-complete
        # total with no open item.  A known difference on any lane must block
        # completion and keep the disputed amount out of confirmed totals.
        self.bill("ch-api-1", amount="21.00", rate_rule="")
        self.usage("u1", cost="21.00")
        result = canonical_finance.run(self.bundle)
        row = next(row for row in result["intake_report"]["reconciliation_rows"]
                   if row["charge_id"] == "ch-api-1")
        self.assertEqual("unknown", row["recalculation"]["bill"]["status"])
        self.assertEqual("difference", row["recalculation"]["usage"]["status"])
        self.assertEqual("FAIL", self.conclusion(result, "Rule Repriced", "ch-api-1")["status"])
        usage = next(item for item in result["case"]["usage"] if item["usage_id"] == "u1")
        self.assertEqual("difference", usage["financial_status"])
        mismatch = next(item for item in result["intake_report"]["charge_amount_mismatches"]
                        if item.get("kind") == "approved_rule_recalculation"
                        and item["charge_id"] == "ch-api-1")
        self.assertEqual("usage", mismatch["lane"])
        self.assertEqual("21.00", mismatch["bill"])
        self.assertEqual("20.00", mismatch["expected"])
        self.assertTrue(any(item["kind"] == "charge_amount_mismatch"
                            and item.get("record_id") == "ch-api-1"
                            and item.get("status") != "resolved"
                            for item in result["case"]["open_items"]))
        self.assertEqual("partial", result["finance_status"])
        self.assertEqual("partial", result["status"])
        self.assertIsNone(result["economics"]["totals"]["fully_loaded_cost"])

    def test_missing_bill_amount_is_open_and_blocks_complete_totals(self):
        self.bill("ch-api-1", amount="")
        result = canonical_finance.run(self.bundle)
        self.assertIsNone(result["intake_report"]["reconciliation"]["billed_total"])
        self.assertIsNone(result["economics"]["totals"]["fully_loaded_cost"])
        self.assertIsNone(result["economics"]["totals"]["cost_per_verified_success"])
        self.assertEqual("UNKNOWN", self.conclusion(result, "Invoice Matched", "ch-api-1")["status"])
        self.assertTrue(any(item["kind"] == "missing_amount" and item.get("record_id") == "ch-api-1"
                            for item in result["case"]["open_items"]))

    def test_missing_bill_currency_is_not_assumed_from_case(self):
        self.bill("ch-api-1", currency="")
        result = canonical_finance.run(self.bundle)
        self.assertIsNone(result["intake_report"]["reconciliation"]["billed_total"])
        self.assertIsNone(result["economics"]["totals"]["fully_loaded_cost"])
        row = next(row for row in result["intake_report"]["reconciliation_rows"]
                   if row["charge_id"] == "ch-api-1")
        self.assertIsNone(row["bill_currency"])
        self.assertTrue(any(item["kind"] == "missing_currency"
                            for item in result["case"]["open_items"]))

    def test_missing_quantity_blocks_recalculation_not_turns_into_zero(self):
        self.bill("ch-api-1", quantity="")
        result = canonical_finance.run(self.bundle)
        row = next(row for row in result["intake_report"]["reconciliation_rows"]
                   if row["charge_id"] == "ch-api-1")
        recalculation = row["recalculation"]["bill"]
        self.assertEqual("unknown", recalculation["status"])
        self.assertIsNone(recalculation["expected_amount"])
        self.assertIsNone(recalculation["difference"])
        self.assertIn("not zero", recalculation["reason"])
        self.assertEqual("UNKNOWN", self.conclusion(result, "Invoice Matched", "ch-api-1")["status"])
        # The actual invoice/bill amount is still known; only rule verification is open.
        self.assertEqual("86.00", result["economics"]["totals"]["fully_loaded_cost"])

    def test_amount_dispute_blocks_cost_without_changing_allocation_axis(self):
        self.bill("ch-api-1", amount="21.00")
        result = canonical_finance.run(self.bundle)
        self.assertEqual("difference", result["intake_report"]["reconciliation"]["status"])
        self.assertIsNone(result["economics"]["totals"]["fully_loaded_cost"])
        self.assertIsNone(result["economics"]["totals"]["cost_per_verified_success"])
        self.assertEqual("FAIL", self.conclusion(result, "Invoice Matched", "ch-api-1")["status"])
        self.assertEqual("FAIL", self.conclusion(result, "Vendor Reconciled", "ch-api-1")["status"])
        self.assertEqual("open", result["economics"]["allocation_coverage"][0]["status"])

    def test_outcome_value_gap_blocks_roi_not_complete_cost_or_success_cost(self):
        self.outcome("buyer-accept-t1", currency="")
        result = canonical_finance.run(self.bundle)
        totals = result["economics"]["totals"]
        self.assertEqual("complete", result["case"]["financial_status"])
        self.assertEqual("86.00", totals["fully_loaded_cost"])
        self.assertEqual("86.00", totals["cost_per_verified_success"])
        self.assertIsNone(totals["verified_business_value"])
        self.assertIsNone(totals["roi"])
        open_item = next(item for item in result["case"]["open_items"]
                         if item.get("record_id") == "buyer-accept-t1"
                         and item["kind"] == "missing_currency")
        self.assertEqual("outcome", open_item["domain"])

    def test_unverified_delivery_keeps_denominator_unknown_not_false(self):
        self.outcome("buyer-accept-t1", source_party="vendor",
                     collection_method="vendor_export", independence="same_vendor_control_domain")
        result = canonical_finance.run(self.bundle)
        totals = result["economics"]["totals"]
        self.assertEqual("86.00", totals["fully_loaded_cost"])
        self.assertEqual("unknown", totals["success_denominator_status"])
        self.assertIsNone(totals["verified_success_tasks"])
        self.assertIsNone(totals["cost_per_verified_success"])
        task = next(row for row in result["economics"]["tasks"] if row["task_id"] == "t1")
        self.assertIsNone(task["verified_success"])
        self.assertEqual("unknown", task["success_status"])

    def test_shared_rule_without_versioned_driver_fails_closed(self):
        rule_map = self.json("rule-map.json")
        for rule in rule_map["rules"]:
            if rule["rule_id"] == "shared-key-driver":
                rule.pop("driver")
        self.write_json("rule-map.json", rule_map)
        with self.assertRaisesRegex(canonical_finance.CanonicalFinanceError, "driver"):
            canonical_finance.run(self.bundle)

    def test_driver_csv_and_approved_weights_cannot_drift(self):
        fields, rows = self.rows("drivers.csv")
        for row in rows:
            if row["driver_id"] == "shared-key-usage" and row["task_id"] == "t1":
                row["weight"] = "999"
        self.write_rows("drivers.csv", fields, rows)
        with self.assertRaisesRegex(canonical_finance.CanonicalFinanceError, "does not match"):
            canonical_finance.run(self.bundle)

    def test_unsupported_format_and_cross_account_scope_fail_closed(self):
        manifest = self.json("manifest.json")
        manifest["sources"]["usage"][0]["format"] = "parquet"
        self.write_json("manifest.json", manifest)
        with self.assertRaises(period_intake.IntakeError):
            canonical_finance.run(self.bundle)

        manifest = self.json("manifest.json")
        manifest["sources"]["usage"][0]["format"] = "usage-jsonl"
        manifest["sources"]["usage"][0]["account_ref"] = "another-account"
        self.write_json("manifest.json", manifest)
        with self.assertRaisesRegex(canonical_finance.CanonicalFinanceError, "account_ref"):
            canonical_finance.run(self.bundle)

    def test_canonical_cli_writes_json_without_touching_inputs(self):
        output = Path(self.temp.name) / "result.json"
        script = Path(__file__).resolve().parents[2] / "scripts" / "run_canonical_finance.py"
        before = (self.bundle / "manifest.json").read_bytes()
        completed = subprocess.run(
            [sys.executable, str(script), str(self.bundle), "--out", str(output)],
            text=True, capture_output=True, timeout=120)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(before, (self.bundle / "manifest.json").read_bytes())
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual("verovo.canonical-finance", payload["kind"])
        self.assertEqual("86.00", payload["economics"]["totals"]["cost_per_verified_success"])

    def test_matched_bill_cannot_bypass_single_vendor_scope(self):
        self.bill("ch-api-1", vendor="another-vendor")
        with self.assertRaisesRegex(canonical_finance.CanonicalFinanceError, "vendor/account"):
            canonical_finance.run(self.bundle)

    def test_invoice_extraction_is_unreviewed_unbooked_and_blocks_full_period_cost(self):
        manifest = self.json("manifest.json")
        manifest["sources"]["invoices"] = [{"path": "invoice.txt", "format": "invoice2data"}]
        self.write_json("manifest.json", manifest)
        (self.bundle / "invoice.txt").write_text("synthetic OCR candidate", encoding="utf-8")
        draft = {
            "source_sha256": "a" * 64, "gross_amount": "1000.00", "currency": "USD",
        }
        with mock.patch.object(period_intake.invoice_intake, "extract_invoice", return_value=draft):
            result = canonical_finance.run(self.bundle)
        document = next(row for row in result["case"]["documents"] if row["type"] == "invoice")
        self.assertEqual("unreviewed", document["review_status"])
        self.assertEqual("unbooked", document["booking_status"])
        self.assertEqual("1000.00", document["total"])
        self.assertEqual({"shared-key", "seat-fee"},
                         {row["cost_id"] for row in result["case"]["cost_items"]})
        self.assertIsNone(result["economics"]["totals"]["fully_loaded_cost"])
        self.assertEqual("86.00", result["economics"]["totals"]["attributed_cost_subtotal"])


if __name__ == "__main__":
    unittest.main()
