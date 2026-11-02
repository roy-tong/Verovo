#!/usr/bin/env python3
"""Audit Case Builder: generated findings must match the hand-authored oracle."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "examples" / "audit-golden-case"
BUILDER = ROOT / "scripts" / "build_audit_case.py"
VALIDATOR = ROOT / "scripts" / "validate_audit_case.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


class BuildAuditCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.builder = _load("build_audit_case_test", BUILDER)
        cls.validator = _load("validate_audit_case_test", VALIDATOR)
        cls.case = cls.builder.build_case(GOLDEN)
        cls.expected = json.loads((GOLDEN / "expected-findings.json").read_text(encoding="utf-8"))

    def _by_classification(self):
        return {finding["classification"]: finding for finding in self.case["findings"]}

    def test_generated_case_passes_the_validator(self) -> None:
        errors = self.validator.validate_audit_case(self.case, GOLDEN)
        self.assertEqual([], errors)

    def test_every_expected_classification_is_generated_once(self) -> None:
        got = [finding["classification"] for finding in self.case["findings"]]
        expected = [finding["classification"] for finding in self.expected["findings"]]
        self.assertEqual(sorted(expected), sorted(got))
        self.assertEqual(8, len(got))

    def test_amounts_provability_and_rules_match_the_oracle(self) -> None:
        generated = self._by_classification()
        for wanted in self.expected["findings"]:
            with self.subTest(classification=wanted["classification"]):
                got = generated[wanted["classification"]]
                self.assertEqual(wanted["amount"]["value"], got["amount"]["value"])
                self.assertEqual(wanted["amount"]["currency"], got["amount"]["currency"])
                self.assertEqual(wanted["provable"], got["provable"])
                self.assertEqual(wanted["rule_ref"]["rule_id"], got["rule_ref"]["rule_id"])
                self.assertEqual(wanted["rule_ref"]["version"], got["rule_ref"]["version"])

    def test_generated_status_never_outruns_the_reviewer(self) -> None:
        order = {"identified": 0, "validated": 1, "accepted": 2, "realized": 3}
        generated = self._by_classification()
        for wanted in self.expected["findings"]:
            got = generated[wanted["classification"]]
            self.assertLessEqual(order[got["status"]], order[wanted["status"]])
        self.assertEqual("realized", generated["duplicate_charge"]["status"])
        self.assertEqual("identified", generated["refund_missing"]["status"])

    def test_evidence_refs_cover_the_oracle_records(self) -> None:
        generated = self._by_classification()
        for wanted in self.expected["findings"]:
            got = generated[wanted["classification"]]
            wanted_records = {ref["record_id"] for ref in wanted["evidence_refs"]}
            got_records = {ref["record_id"] for ref in got["evidence_refs"]}
            self.assertTrue(wanted_records.issubset(got_records),
                            f"{wanted['classification']}: {wanted_records - got_records}")

    def test_unprovable_amounts_are_null_not_zero(self) -> None:
        for finding in self.case["findings"]:
            if finding["provable"]:
                continue
            self.assertIsNone(finding["amount"]["value"])
            self.assertTrue(finding.get("reason"))

    def test_coverage_matches_the_oracle(self) -> None:
        self.assertEqual(1686.0, self.case["coverage"]["verified_amount"])
        self.assertEqual(394.0, self.case["coverage"]["unverified_amount"])
        self.assertEqual("USD", self.case["coverage"]["currency"])

    def test_clean_lines_are_not_flagged(self) -> None:
        manifest = json.loads((GOLDEN / "manifest.json").read_text(encoding="utf-8"))
        clean = set(manifest.get("clean_charge_lines") or [])
        referenced = {ref["record_id"] for finding in self.case["findings"]
                      for ref in finding["evidence_refs"]}
        self.assertFalse(clean & referenced)

    def test_cli_writes_a_valid_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "case.json"
            completed = subprocess.run(
                [sys.executable, str(BUILDER), str(GOLDEN), "--out", str(output)],
                capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            checked = subprocess.run(
                [sys.executable, str(VALIDATOR), str(output), "--evidence-root", str(GOLDEN)],
                capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
