"""The golden audit case must keep every expected finding, including UNPROVABLE."""
from __future__ import annotations

import csv
import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

from _root_support import load_json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_audit_case as audit  # noqa: E402

BUNDLE = ROOT / "examples" / "audit-golden-case"
CASE_PATH = BUNDLE / "audit-case.json"
MANIFEST_PATH = BUNDLE / "manifest.json"
EXPECTED_PATH = BUNDLE / "expected-findings.json"
RULES_PATH = BUNDLE / "contract-rules.json"




class TestAuditGoldenCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = load_json(CASE_PATH)
        cls.manifest = load_json(MANIFEST_PATH)
        cls.expected = load_json(EXPECTED_PATH)
        cls.rules = load_json(RULES_PATH)
        cls.findings = {
            finding["finding_id"]: finding for finding in cls.case["findings"]
        }

    def test_manifest_metadata_matches_the_case(self):
        for field in (
            "case_id",
            "buyer",
            "vendor",
            "period",
            "contract_version",
            "scope",
        ):
            self.assertEqual(self.manifest[field], self.case[field], field)
        self.assertEqual(self.manifest["currency"], self.case["coverage"]["currency"])

    def test_manifest_sources_exist_inside_the_bundle(self):
        for source_name, entries in self.manifest["sources"].items():
            self.assertTrue(entries, source_name)
            for entry in entries:
                path = (BUNDLE / entry).resolve()
                self.assertTrue(path.is_file(), "%s: %s" % (source_name, entry))
                path.relative_to(BUNDLE.resolve())

    def test_case_validates(self):
        self.assertEqual(audit.validate_audit_case(self.case, BUNDLE), [])

    def test_every_expected_finding_is_present_and_correctly_classified(self):
        expected_ids = [item["finding_id"] for item in self.expected["findings"]]
        self.assertEqual(set(expected_ids), set(self.findings))
        self.assertEqual(len(expected_ids), len(set(expected_ids)))

        for expected in self.expected["findings"]:
            actual = self.findings[expected["finding_id"]]
            for field in ("classification", "severity", "status", "provable"):
                self.assertEqual(actual[field], expected[field], expected["finding_id"])

            for field, value in expected["amount"].items():
                self.assertEqual(
                    actual["amount"][field],
                    value,
                    "%s.amount.%s" % (expected["finding_id"], field),
                )
            for field, value in expected["rule_ref"].items():
                self.assertEqual(
                    actual["rule_ref"][field],
                    value,
                    "%s.rule_ref.%s" % (expected["finding_id"], field),
                )
            self.assertEqual(
                actual["evidence_refs"],
                expected["evidence_refs"],
                expected["finding_id"],
            )
            if "settlement_ref" in expected:
                self.assertEqual(
                    actual["settlement_ref"], expected["settlement_ref"]
                )
            if "reason" in expected:
                self.assertEqual(actual["reason"], expected["reason"])

    def test_evidence_refs_support_an_explicit_id_field(self):
        record, error = audit.resolve_evidence_ref(
            BUNDLE,
            {
                "path": "outcomes.csv",
                "record_id": "outcome-003",
                "id_field": "outcome_id",
            },
        )
        self.assertIsNone(error)
        self.assertEqual(record["verified"], "false")

    def test_every_rule_ref_resolves_to_the_confirmed_contract_version(self):
        rules = {rule["rule_id"]: rule for rule in self.rules["rules"]}
        self.assertEqual(self.rules["contract_version"], self.case["contract_version"])
        for finding in self.case["findings"]:
            rule_ref = finding["rule_ref"]
            rule = rules[rule_ref["rule_id"]]
            for field in ("version", "confirmed_by", "confirmed_at"):
                self.assertEqual(
                    rule_ref[field],
                    rule[field],
                    "%s %s" % (finding["finding_id"], field),
                )
            for evidence in finding["evidence_refs"]:
                record, error = audit.resolve_evidence_ref(BUNDLE, evidence)
                self.assertIsNone(error, evidence)
                self.assertIsNotNone(record)

    def test_all_required_anomalies_are_present(self):
        classifications = {finding["classification"] for finding in self.case["findings"]}
        self.assertEqual(
            classifications,
            {
                "duplicate_charge",
                "rate_mismatch",
                "missing_evidence",
                "currency_mismatch",
                "discount_not_applied",
                "refund_missing",
                "shared_cost_allocation",
                "unverified_outcome",
            },
        )

    def test_missing_task_id_is_unprovable_and_never_zero(self):
        finding = self.findings["F-002"]
        self.assertEqual(finding["classification"], "missing_evidence")
        self.assertFalse(finding["provable"])
        self.assertIsNone(finding["amount"]["value"])
        self.assertNotEqual(finding["amount"]["value"], 0)
        self.assertIn("no task_id", finding["reason"])

        # The invariant is case-wide: no UNPROVABLE finding may carry a number.
        for current in self.case["findings"]:
            if current["provable"] is False:
                self.assertIsNone(
                    current["amount"]["value"],
                    current["finding_id"],
                )
                self.assertTrue(current["reason"].strip(), current["finding_id"])

    def test_cross_currency_and_unverified_outcome_are_explicit(self):
        currency_finding = self.findings["F-003"]
        self.assertEqual(currency_finding["classification"], "currency_mismatch")
        self.assertEqual(currency_finding["amount"]["currency"], "EUR")
        self.assertEqual(currency_finding["amount"]["value"], "90.00")
        self.assertTrue(currency_finding["provable"])

        outcome_finding = self.findings["F-007"]
        self.assertEqual(outcome_finding["classification"], "unverified_outcome")
        self.assertFalse(outcome_finding["provable"])
        self.assertIsNone(outcome_finding["amount"]["value"])
        self.assertIn("verified=false", outcome_finding["reason"])

        with (BUNDLE / "outcomes.csv").open(encoding="utf-8-sig", newline="") as handle:
            outcomes = {row["outcome_id"]: row for row in csv.DictReader(handle)}
        self.assertEqual(outcomes["outcome-003"]["verified"], "false")

    def test_task_links_are_complete_except_the_missing_task_anomaly(self):
        with (BUNDLE / "tasks.csv").open(encoding="utf-8-sig", newline="") as handle:
            tasks = {row["task_id"] for row in csv.DictReader(handle)}
        missing = []
        for name in ("invoice-2026-10.json", "invoice-2026-10-eur.json"):
            invoice = load_json(BUNDLE / "invoices" / name)
            for line in invoice["lines"]:
                if line["task_id"]:
                    self.assertIn(line["task_id"], tasks, line["line_id"])
                else:
                    missing.append(line["line_id"])
        self.assertEqual(missing, ["line-007"])

        with (BUNDLE / "outcomes.csv").open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                self.assertIn(row["task_id"], tasks, row["outcome_id"])

    def test_realized_finding_has_a_settlement_and_the_credit_exists(self):
        finding = self.findings["F-001"]
        self.assertEqual(finding["status"], "realized")
        settlement_ref = finding["settlement_ref"]
        self.assertTrue(settlement_ref.startswith("credit-note:"))
        credit_id = settlement_ref.split(":", 1)[1]

        register = load_json(BUNDLE / "invoices" / "credit-register-2026-11.json")
        credits = {credit["credit_id"]: credit for credit in register["credits"]}
        self.assertIn(credit_id, credits)
        self.assertEqual(credits[credit_id]["amount"], finding["amount"]["value"])
        self.assertEqual(credits[credit_id]["charge_id"], "chg-1002")
        # Settlement is evidenced by application, not by issuance alone.
        self.assertTrue(credits[credit_id]["applied"])
        self.assertTrue(credits[credit_id]["applied_at"])

    def test_clean_charges_are_not_flagged(self):
        clean_lines = set(self.manifest["clean_charge_lines"])
        referenced_records = {
            ref["record_id"]
            for finding in self.case["findings"]
            for ref in finding["evidence_refs"]
        }
        self.assertFalse(clean_lines & referenced_records)
        self.assertEqual(clean_lines, {"line-004", "line-009"})

    def test_usd_coverage_reconciles_to_invoice_minus_settled_duplicate(self):
        invoice = load_json(BUNDLE / "invoices" / "invoice-2026-10.json")
        total = sum(Decimal(str(line["amount"])) for line in invoice["lines"])
        self.assertEqual(invoice["currency"], "USD")
        self.assertEqual(total, Decimal("2200.00"))

        coverage = self.case["coverage"]
        register = load_json(BUNDLE / "invoices" / "credit-register-2026-11.json")
        settled = sum(
            Decimal(str(credit["amount"])) for credit in register["credits"]
        )
        self.assertEqual(settled, Decimal("120.00"))
        self.assertEqual(coverage["currency"], "USD")
        self.assertEqual(coverage["verified_amount"], "1686.00")
        self.assertEqual(coverage["unverified_amount"], "394.00")
        self.assertEqual(
            Decimal(coverage["verified_amount"])
            + Decimal(coverage["unverified_amount"])
            + settled,
            total,
        )

        eur_invoice = load_json(BUNDLE / "invoices" / "invoice-2026-10-eur.json")
        self.assertEqual(eur_invoice["currency"], "EUR")
        self.assertEqual(
            sum(Decimal(str(line["amount"])) for line in eur_invoice["lines"]),
            Decimal("90.00"),
        )


if __name__ == "__main__":
    unittest.main()
