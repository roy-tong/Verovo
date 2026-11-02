"""Mutation tests for the Audit Case builder's financial correctness.

Each test mutates a copy of the golden bundle the way a different contract or
a different vendor export would, and pins the arithmetic that previously
relied on hardcoded constants, float math, issuance-equals-settlement and
sum-over-findings coverage:

- M1 a confirmed parameter change must flow into the findings, and a missing
  parameter must fail closed instead of falling back to a built-in constant;
- M2 two findings on the same invoice line must not double count coverage;
- M3 an issued-but-unapplied credit note is not settlement;
- M4 money is Decimal end to end and equal splits sum back to the total.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "examples" / "audit-golden-case"

_spec = importlib.util.spec_from_file_location("build_audit_case_mut", ROOT / "scripts" / "build_audit_case.py")
builder = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("build_audit_case_mut", builder)
_spec.loader.exec_module(builder)

_spec_v = importlib.util.spec_from_file_location("validate_audit_case_mut", ROOT / "scripts" / "validate_audit_case.py")
validator = importlib.util.module_from_spec(_spec_v)
sys.modules.setdefault("validate_audit_case_mut", validator)
_spec_v.loader.exec_module(validator)

MONEY_RE = re.compile(r"^\d+\.\d{2}$")


class GoldenBundle:
    """A mutable copy of the golden bundle inside a temp directory."""

    def __init__(self, case_dir: Path) -> None:
        self.dir = case_dir

    @classmethod
    def copy(cls, test=None) -> "GoldenBundle":
        temp = tempfile.mkdtemp(prefix="audit-mutation-")
        copied = Path(temp) / "audit-golden-case"
        shutil.copytree(GOLDEN, copied)
        if test is not None:
            test.addCleanup(shutil.rmtree, copied.parent, True)
        return cls(copied)

    def json(self, relative: str):
        return json.loads((self.dir / relative).read_text(encoding="utf-8"))

    def write(self, relative: str, payload) -> None:
        (self.dir / relative).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def rule(self, rule_id: str):
        contract = self.json("contract-rules.json")
        return next(rule for rule in contract["rules"] if rule["rule_id"] == rule_id)

    def set_rule_param(self, rule_id: str, key: str, value) -> None:
        contract = self.json("contract-rules.json")
        for rule in contract["rules"]:
            if rule["rule_id"] == rule_id:
                rule.setdefault("parameters", {})[key] = value
        self.write("contract-rules.json", contract)

    def drop_rule_params(self, rule_id: str) -> None:
        contract = self.json("contract-rules.json")
        for rule in contract["rules"]:
            if rule["rule_id"] == rule_id:
                rule.pop("parameters", None)
        self.write("contract-rules.json", contract)

    def invoice_line(self, line_id: str):
        invoice = self.json("invoices/invoice-2026-10.json")
        return invoice, next(line for line in invoice["lines"] if line["line_id"] == line_id)

    def set_line(self, line_id: str, **fields) -> None:
        invoice, _ = self.invoice_line(line_id)
        for line in invoice["lines"]:
            if line["line_id"] == line_id:
                line.update(fields)
        self.write("invoices/invoice-2026-10.json", invoice)

    def credit(self):
        register = self.json("invoices/credit-register-2026-11.json")
        return register, register["credits"][0]

    def build(self):
        return builder.build_case(self.dir)

    def validate(self, case) -> list:
        return validator.validate_audit_case(case, self.dir)


def _find(case, classification):
    return [f for f in case["findings"] if f["classification"] == classification]


class TestM1ContractParameters(unittest.TestCase):
    """Previously failing sample: CR-006 price changed in contract-rules.json
    but the builder kept billing the hardcoded 150.0, so a renegotiated price
    silently produced the same finding."""

    def test_confirmed_price_change_flows_into_the_findings(self):
        bundle = GoldenBundle.copy(self)
        # The vendor now bills 175.00 which matches the renegotiated contract.
        bundle.set_rule_param("CR-006", "unit_price", "175.00")
        case = bundle.build()
        self.assertEqual([], _find(case, "rate_mismatch"))

    def test_confirmed_discount_change_flows_into_the_findings(self):
        bundle = GoldenBundle.copy(self)
        bundle.set_rule_param("CR-002", "discount_rate", "0.20")
        case = bundle.build()
        discounts = _find(case, "discount_not_applied")
        self.assertEqual(1, len(discounts))
        self.assertEqual("88.00", discounts[0]["amount"]["value"])

    def test_raised_volume_threshold_removes_the_discount(self):
        bundle = GoldenBundle.copy(self)
        bundle.set_rule_param("CR-002", "volume_threshold", 25000)
        case = bundle.build()
        self.assertEqual([], _find(case, "discount_not_applied"))

    def test_missing_parameter_fails_closed_not_a_builtin_default(self):
        for rule_id in ("CR-001", "CR-002", "CR-003", "CR-006"):
            with self.subTest(rule=rule_id):
                bundle = GoldenBundle.copy(self)
                bundle.drop_rule_params(rule_id)
                with self.assertRaises(builder.BuildError) as raised:
                    bundle.build()
                self.assertIn("refuses to fall back", str(raised.exception))


class TestM2CoverageDeduplicatesPerLine(unittest.TestCase):
    """Previously failing sample: line-003 was both a duplicate repeat (120)
    and overbilled at 0.03/unit (rate difference 60); the old coverage summed
    both findings and reported 574 of exposure for a 120 line."""

    def test_two_findings_on_one_line_count_once(self):
        bundle = GoldenBundle.copy(self)
        register, credit = bundle.credit()
        credit["applied"] = False  # keep the duplicate open as well
        bundle.write("invoices/credit-register-2026-11.json", register)
        bundle.set_line("line-003", unit_price=0.03)

        case = bundle.build()
        self.assertEqual([], bundle.validate(case))

        duplicates = _find(case, "duplicate_charge")
        rates = _find(case, "rate_mismatch")
        self.assertEqual("120.00", duplicates[0]["amount"]["value"])
        self.assertEqual("60.00", rates[0]["amount"]["value"])
        self.assertTrue(
            {ref["line_id"] for ref in duplicates[0]["affected_lines"]} &
            {ref["line_id"] for ref in rates[0]["affected_lines"]},
            "both findings should dispute line-003",
        )

        # The line contributes its worst single finding (120), never 180.
        self.assertEqual("514.00", case["coverage"]["unverified_amount"])
        self.assertEqual("1686.00", case["coverage"]["verified_amount"])


class TestM3SettlementRequiresApplication(unittest.TestCase):
    """Previously failing sample: a credit note existed for the duplicate, so
    the finding jumped to realized and coverage counted it as settled, even
    though the credit was never applied to any invoice."""

    def test_issued_but_unapplied_credit_is_not_settlement(self):
        bundle = GoldenBundle.copy(self)
        register, credit = bundle.credit()
        credit["applied"] = False
        credit.pop("applied_at", None)
        bundle.write("invoices/credit-register-2026-11.json", register)

        case = bundle.build()
        self.assertEqual([], bundle.validate(case))

        duplicate = _find(case, "duplicate_charge")[0]
        self.assertEqual("identified", duplicate["status"])
        self.assertNotIn("settlement_ref", duplicate)
        self.assertIn("not evidenced as applied", duplicate["amount"]["basis"])
        self.assertTrue(
            any(ref["field"] == "applied" for ref in duplicate["evidence_refs"]))
        self.assertEqual(
            {"identified"},
            {finding["status"] for finding in case["findings"]})

        # Nothing settled: the duplicate stays inside open exposure.
        coverage = case["coverage"]
        self.assertEqual("514.00", coverage["unverified_amount"])
        self.assertEqual("1686.00", coverage["verified_amount"])

    def test_applied_missing_timestamp_is_not_settlement_either(self):
        bundle = GoldenBundle.copy(self)
        register, credit = bundle.credit()
        credit["applied"] = True
        credit.pop("applied_at", None)
        bundle.write("invoices/credit-register-2026-11.json", register)

        case = bundle.build()
        duplicate = _find(case, "duplicate_charge")[0]
        self.assertEqual("identified", duplicate["status"])


class TestM4DecimalArithmetic(unittest.TestCase):
    """Previously failing sample: a USD 1000.00 shared fee across three active
    tasks; float division rounded each share to 333.33, so the shares summed
    to 999.99 and the misallocation (666.67) disagreed with any split that
    adds back to the invoice total."""

    def test_equal_splits_sum_back_to_the_total(self):
        for total, count, expected in (
            (Decimal("1000.00"), 3, [Decimal("333.34"), Decimal("333.33"), Decimal("333.33")]),
            (Decimal("900.00"), 3, [Decimal("300.00")] * 3),
            (Decimal("0.10"), 3, [Decimal("0.04"), Decimal("0.03"), Decimal("0.03")]),
            (Decimal("100.01"), 7, [Decimal("14.29")] * 5 + [Decimal("14.28")] * 2),
        ):
            with self.subTest(total=str(total), count=count):
                shares = builder._split_equal(total, count)
                self.assertEqual(expected, shares)
                self.assertEqual(_money(total), sum(shares, Decimal("0.00")))

    def test_shared_cost_misallocation_is_consistent_with_the_split(self):
        bundle = GoldenBundle.copy(self)
        bundle.set_line("line-006", amount=1000.00, unit_price=1000.00)
        case = bundle.build()
        self.assertEqual([], bundle.validate(case))

        shared = _find(case, "shared_cost_allocation")[0]
        # 1000.00 - 333.34 = 666.66, and the three shares sum back to 1000.00.
        self.assertEqual("666.66", shared["amount"]["value"])

        coverage = case["coverage"]
        total = Decimal("2300.00")  # 2200.00 - 900.00 + 1000.00
        self.assertEqual(
            total,
            Decimal(coverage["verified_amount"])
            + Decimal(coverage["unverified_amount"])
            + Decimal("120.00"),
        )

    def test_three_decimal_unit_prices_stay_exact(self):
        bundle = GoldenBundle.copy(self)
        # Contract price 0.020, billed 0.0205 across 20,000 requests.
        bundle.set_line(
            "line-001", unit_price=0.0205, amount=410.00, quantity=20000)
        case = bundle.build()
        self.assertEqual([], bundle.validate(case))
        line_001_rate = next(
            finding for finding in _find(case, "rate_mismatch")
            if finding["affected_lines"][0]["line_id"] == "line-001")
        # (0.0205 - 0.020) * 20000 = 10.00 exactly, not 9.999999...
        self.assertEqual("10.00", line_001_rate["amount"]["value"])

    def test_every_serialized_amount_is_a_two_decimal_string(self):
        bundle = GoldenBundle.copy(self)
        case = bundle.build()
        for finding in case["findings"]:
            value = finding["amount"]["value"]
            if value is not None:
                self.assertTrue(
                    MONEY_RE.fullmatch(value),
                    "%s amount %r" % (finding["finding_id"], value),
                )
        for name in ("verified_amount", "unverified_amount"):
            self.assertTrue(MONEY_RE.fullmatch(case["coverage"][name]))


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


if __name__ == "__main__":
    unittest.main()
