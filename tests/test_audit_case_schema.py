"""Audit Case / Audit Finding schema and validator contract tests."""
from __future__ import annotations

import copy
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_audit_case as audit  # noqa: E402

BUNDLE = ROOT / "examples" / "audit-golden-case"
CASE_PATH = BUNDLE / "audit-case.json"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class TestAuditSchemas(unittest.TestCase):
    def test_both_schemas_are_draft_2020_12(self):
        for name in ("audit-finding.schema.json", "audit-case.schema.json"):
            schema = load_json(ROOT / "schemas" / name)
            self.assertEqual(
                schema["$schema"],
                "https://json-schema.org/draft/2020-12/schema",
                name,
            )

    def test_finding_schema_contains_the_required_vocabularies(self):
        schema = load_json(ROOT / "schemas" / "audit-finding.schema.json")
        required = schema["required"]
        for field in (
            "finding_id",
            "case_id",
            "classification",
            "severity",
            "amount",
            "status",
            "evidence_refs",
            "rule_ref",
            "provable",
        ):
            self.assertIn(field, required)

        classifications = schema["properties"]["classification"]["enum"]
        self.assertEqual(tuple(classifications), audit.CLASSIFICATIONS)
        for required_classification in (
            "duplicate_charge",
            "rate_mismatch",
            "missing_evidence",
            "currency_mismatch",
            "discount_not_applied",
            "refund_missing",
            "shared_cost_allocation",
            "unverified_outcome",
        ):
            self.assertIn(required_classification, classifications)

        self.assertEqual(
            tuple(schema["properties"]["status"]["enum"]),
            audit.FINDING_STATUSES,
        )
        self.assertEqual(
            set(schema["properties"]["status"]["enum"]),
            {"identified", "validated", "accepted", "realized", "dismissed"},
        )
        self.assertEqual(
            tuple(schema["properties"]["severity"]["enum"]),
            audit.SEVERITIES,
        )
        self.assertEqual(
            schema["properties"]["amount"]["properties"]["value"]["type"],
            ["number", "null"],
        )

    def test_case_schema_mirrors_finding_vocabularies_and_fields(self):
        finding_schema = load_json(ROOT / "schemas" / "audit-finding.schema.json")
        case_schema = load_json(ROOT / "schemas" / "audit-case.schema.json")
        mirrored = case_schema["$defs"]["finding"]["properties"]
        for name in ("classification", "severity", "status"):
            self.assertEqual(
                finding_schema["properties"][name]["enum"],
                mirrored[name]["enum"],
                name,
            )
        self.assertEqual(
            finding_schema["properties"]["provable"]["type"],
            mirrored["provable"]["type"],
        )
        self.assertEqual(
            finding_schema["properties"]["amount"],
            mirrored["amount"],
        )
        for name in ("evidenceRef", "ruleRef"):
            self.assertEqual(
                finding_schema["$defs"][name],
                case_schema["$defs"][name],
                name,
            )
        for field in finding_schema["required"]:
            self.assertIn(field, case_schema["$defs"]["finding"]["required"])

    def test_case_schema_requires_the_case_contract_fields(self):
        schema = load_json(ROOT / "schemas" / "audit-case.schema.json")
        for field in (
            "case_id",
            "buyer",
            "vendor",
            "period",
            "contract_version",
            "scope",
            "coverage",
            "findings",
            "status",
        ):
            self.assertIn(field, schema["required"])
        for field in ("verified_amount", "unverified_amount", "currency"):
            self.assertIn(field, schema["properties"]["coverage"]["required"])
        self.assertEqual(
            tuple(schema["properties"]["status"]["enum"]),
            audit.CASE_STATUSES,
        )


class TestAuditCaseValidator(unittest.TestCase):
    def setUp(self):
        self.case = load_json(CASE_PATH)

    def errors(self, case):
        return audit.validate_audit_case(case, BUNDLE)

    def finding(self, case, finding_id):
        return next(item for item in case["findings"] if item["finding_id"] == finding_id)

    def test_valid_golden_case(self):
        self.assertEqual(self.errors(self.case), [])

    def test_missing_required_case_field(self):
        case = copy.deepcopy(self.case)
        del case["vendor"]
        errors = self.errors(case)
        self.assertTrue(
            any("vendor" in error and "missing required" in error for error in errors),
            errors,
        )

    def test_missing_required_finding_field(self):
        case = copy.deepcopy(self.case)
        del self.finding(case, "F-004")["rule_ref"]
        errors = self.errors(case)
        self.assertTrue(
            any("rule_ref" in error and "missing required" in error for error in errors),
            errors,
        )

    def test_unknown_classification_enum(self):
        case = copy.deepcopy(self.case)
        self.finding(case, "F-002")["classification"] = "not_a_classification"
        errors = self.errors(case)
        self.assertTrue(
            any("classification" in error and "unknown value" in error for error in errors),
            errors,
        )

    def test_unknown_status_enum(self):
        case = copy.deepcopy(self.case)
        self.finding(case, "F-004")["status"] = "paid"
        errors = self.errors(case)
        self.assertTrue(
            any("status" in error and "unknown value" in error for error in errors),
            errors,
        )

    def test_unprovable_requires_reason_and_null_never_zero(self):
        case = copy.deepcopy(self.case)
        finding = self.finding(case, "F-002")
        self.assertFalse(finding["provable"])
        self.assertIsNone(finding["amount"]["value"])
        self.assertNotEqual(0, finding["amount"]["value"])
        self.assertTrue(finding["reason"].strip())

        del finding["reason"]
        errors = self.errors(case)
        self.assertTrue(
            any("UNPROVABLE" in error and "reason" in error for error in errors),
            errors,
        )

        case = copy.deepcopy(self.case)
        finding = self.finding(case, "F-002")
        finding["amount"]["value"] = 0
        errors = self.errors(case)
        self.assertTrue(
            any("UNPROVABLE" in error and "value" in error for error in errors),
            errors,
        )
        self.assertTrue(any("0" in error for error in errors), errors)

    def test_realized_requires_a_settlement_reference(self):
        case = copy.deepcopy(self.case)
        finding = self.finding(case, "F-001")
        self.assertEqual(finding["status"], "realized")
        self.assertTrue(finding["settlement_ref"])
        del finding["settlement_ref"]
        errors = self.errors(case)
        self.assertTrue(
            any("settlement_ref" in error and "realized" in error for error in errors),
            errors,
        )

        finding["settlement_ref"] = ""
        errors = self.errors(case)
        self.assertTrue(
            any("settlement_ref" in error and "realized" in error for error in errors),
            errors,
        )

    def test_realized_finding_must_be_provable(self):
        case = copy.deepcopy(self.case)
        finding = self.finding(case, "F-001")
        finding["provable"] = False
        finding["amount"]["value"] = None
        finding["reason"] = "not decided"
        errors = self.errors(case)
        self.assertTrue(
            any("realized finding must be provable" in error for error in errors),
            errors,
        )

    def test_evidence_ref_record_mismatch(self):
        case = copy.deepcopy(self.case)
        self.finding(case, "F-004")["evidence_refs"][0]["record_id"] = "line-999"
        errors = self.errors(case)
        self.assertTrue(
            any("line-999" in error and "not found" in error for error in errors),
            errors,
        )

    def test_evidence_ref_cannot_escape_the_case_root(self):
        case = copy.deepcopy(self.case)
        self.finding(case, "F-004")["evidence_refs"][0]["path"] = "../outside.json"
        errors = self.errors(case)
        self.assertTrue(
            any("escapes the evidence root" in error for error in errors),
            errors,
        )

    def test_evidence_ref_field_must_exist(self):
        case = copy.deepcopy(self.case)
        self.finding(case, "F-004")["evidence_refs"][0]["field"] = "not_a_field"
        errors = self.errors(case)
        self.assertTrue(
            any("no field" in error and "not_a_field" in error for error in errors),
            errors,
        )

    def test_finding_case_id_must_match(self):
        case = copy.deepcopy(self.case)
        self.finding(case, "F-001")["case_id"] = "another-case"
        errors = self.errors(case)
        self.assertTrue(
            any("does not match" in error and "case_id" in error for error in errors),
            errors,
        )

    def test_duplicate_finding_ids_are_rejected(self):
        case = copy.deepcopy(self.case)
        self.finding(case, "F-002")["finding_id"] = "F-001"
        errors = self.errors(case)
        self.assertTrue(
            any("duplicate finding_id" in error for error in errors),
            errors,
        )

    def test_cli_validates_golden_case_and_rejects_mutated_copy(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = audit.main([str(CASE_PATH)])
        self.assertEqual(code, 0)
        self.assertIn("VALID", output.getvalue())

        with tempfile.TemporaryDirectory() as temp:
            copied = Path(temp) / "audit-golden-case"
            shutil.copytree(BUNDLE, copied)
            path = copied / "audit-case.json"
            case = load_json(path)
            case["findings"][1]["reason"] = ""
            path.write_text(json.dumps(case), encoding="utf-8")

            output = io.StringIO()
            with redirect_stdout(output):
                code = audit.main([str(path)])
            self.assertEqual(code, 1)
            self.assertIn("INVALID", output.getvalue())
            self.assertIn("reason", output.getvalue())


if __name__ == "__main__":
    unittest.main()
