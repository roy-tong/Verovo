"""Line-level arithmetic trust for OCR bill candidates (review 2026-11-20).

The per-line check answers "does quantity × unit price equal the line amount?"
before a draft can be booked. These tests freeze both directions of the
trust contract:

- a wrong line is flagged, downgrades the draft, and blocks booking until a
  named reviewer either corrects the numbers or writes down why the
  discrepancy is accepted (``line_issues_accepted``);
- a line whose numbers cannot be parsed is never waved through by a note —
  it must be corrected, because "unreadable" is not a judgement a reviewer
  can accept on the bill's behalf;
- the tolerance is one smallest printed unit or 1% of the line amount,
  whichever is larger, so zero-decimal currencies (JPY/KRW) stay strict;
- the period's inclusive display end is converted to the half-open data range
  with the conversion spelled out on the candidate.

Note: accepting a flagged line keeps the audit trail (flags + acceptance
note) but does not make the line arithmetically true; the close-sum check
still has to match the invoice total.
"""
import json
import unittest
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

from buyer_ledger import bill_candidate as bc
from buyer_ledger.close import CloseError

from tests.test_bill_candidate import BillCandidateTestBase, INVOICE_TEXT

LINE_MATH_ONLY = INVOICE_TEXT.replace("1000   0.020", "900   0.020")
LINE_SUM_ONLY = INVOICE_TEXT.replace("0.020   20.00", "0.020   21.00")
ZERO_QTY = INVOICE_TEXT.replace("1000   0.020   20.00", "0   0.020   20.00")


class LineArithmeticFlagTests(BillCandidateTestBase):
    def test_mismatched_line_is_flagged_and_blocks_booking(self):
        candidate = self.review_all_key_fields(self.add_pasted(LINE_MATH_ONLY))
        items = {f["name"]: f for f in candidate["fields"]}["line_items"]
        self.assertEqual(1, len(items["line_issues"]))
        issue = items["line_issues"][0]
        self.assertEqual("line_arithmetic_mismatch", issue["code"])
        self.assertEqual("18.00", issue["expected"])
        self.assertIn("line_arithmetic_mismatch", items["flags"])
        self.assertLessEqual(items["confidence"], 0.5)
        self.assertLessEqual(candidate["confidence"], 0.5)
        self.assertIn("算术", "".join(candidate["review_notes"]))
        with self.assertRaises(CloseError) as ctx:
            bc.book(self.store, candidate["candidate_id"], reviewer="finance",
                    into=self.bundle, account="acct-1")
        self.assertEqual("REVIEW_REQUIRED", ctx.exception.code)
        self.assertIn("算术校验未通过", str(ctx.exception))
        self.assertIn("第 1 行", str(ctx.exception))
        self.assertFalse((self.bundle / "period-job.json").exists())

    def test_written_reason_unlocks_booking_and_keeps_flag(self):
        candidate = self.review_all_key_fields(self.add_pasted(LINE_MATH_ONLY))
        corrected = bc.projection(candidate)["fields"]
        items_value = {f["name"]: f for f in corrected}["line_items"]["value"]
        bc.review_field(self.store, candidate["candidate_id"],
                        field="line_items", value=items_value, reviewer="finance",
                        note="供应商确认按 20.00 结算，数量 OCR 误读")
        reviewed = bc.load_candidate(self.store, candidate["candidate_id"])
        items = {f["name"]: f for f in reviewed["fields"]}["line_items"]
        self.assertTrue(items["review"]["line_issues_accepted"])
        result = bc.book(self.store, candidate["candidate_id"],
                         reviewer="finance", into=self.bundle, account="acct-1")
        self.assertTrue((self.bundle / "period-job.json").is_file())
        self.assertTrue(Path(result["export_path"]).is_file())

    def test_line_sum_mismatch_survives_accepting_line_issue(self):
        candidate = self.review_all_key_fields(self.add_pasted(LINE_SUM_ONLY))
        items_value = bc.projection(candidate)["fields"]
        items_value = {f["name"]: f for f in items_value}["line_items"]["value"]
        bc.review_field(self.store, candidate["candidate_id"],
                        field="line_items", value=items_value, reviewer="finance",
                        note="接受 OCR 差异，按账单总额入账")
        with self.assertRaises(CloseError) as ctx:
            bc.book(self.store, candidate["candidate_id"], reviewer="finance",
                    into=self.bundle, account="acct-1")
        self.assertEqual("REVIEW_REQUIRED", ctx.exception.code)
        self.assertIn("line items sum to 21.00", str(ctx.exception))
        self.assertIn("20.00", str(ctx.exception))

    def test_zero_quantity_line_is_flagged(self):
        candidate = self.add_pasted(ZERO_QTY)
        items = {f["name"]: f for f in candidate["fields"]}["line_items"]
        self.assertEqual(["line_zero_quantity"],
                         [issue["code"] for issue in items["line_issues"]])
        self.assertIn("line_zero_quantity", items["flags"])

    def test_unparseable_line_requires_correction_not_a_note(self):
        candidate = self.review_all_key_fields(self.add_pasted())
        value = [{"description": "api requests (agent tier)",
                  "quantity": "n/a", "unit_price": "0.020", "amount": "20.00"}]
        bc.review_field(self.store, candidate["candidate_id"],
                        field="line_items", value=value, reviewer="finance",
                        note="数量 OCR 无法识别，接受该行")
        reviewed = bc.load_candidate(self.store, candidate["candidate_id"])
        items = {f["name"]: f for f in reviewed["fields"]}["line_items"]
        self.assertEqual(["line_field_unparseable"],
                         [issue["code"] for issue in items["line_issues"]])
        self.assertTrue(items["review"]["line_issues_accepted"])
        with self.assertRaises(CloseError) as ctx:
            bc.book(self.store, candidate["candidate_id"], reviewer="finance",
                    into=self.bundle, account="acct-1")
        self.assertEqual("REVIEW_REQUIRED", ctx.exception.code)
        self.assertIn("无法解析", str(ctx.exception))
        self.assertIn("不能靠 note 放行", str(ctx.exception))
        self.assertFalse((self.bundle / "period-job.json").exists())

    def test_clean_lines_pass_with_an_explicit_note(self):
        candidate = self.add_pasted()
        items = {f["name"]: f for f in candidate["fields"]}["line_items"]
        self.assertEqual([], items["line_issues"])
        self.assertIn("算术校验通过", items["review_note"])
        self.assertNotIn("line_issues", json.dumps(
            {f["name"]: f.get("flags") for f in candidate["fields"]}))


class PeriodDisplayEndTests(BillCandidateTestBase):
    def test_period_display_end_is_converted_and_explained(self):
        candidate = self.add_pasted()
        period = {f["name"]: f for f in candidate["fields"]}["period"]
        self.assertEqual("2026-11-01", period["value"]["to"])
        self.assertIn("period_end_converted_to_exclusive", period["flags"])
        self.assertTrue(period["period_end_exclusive"])
        self.assertIn("2026-10-31", period["note"])
        self.assertIn("[2026-10-01, 2026-11-01)", period["note"])
        self.assertEqual(0.9, period["confidence"])
        self.assertTrue(candidate["period_end_exclusive"])
        self.assertEqual(period["note"], candidate["period_end_note"])
        self.assertIn(period["note"], candidate["review_notes"])

    def test_english_to_preserves_the_explicit_exclusive_boundary(self):
        candidate = self.add_pasted(
            "Vendor: Acme Cloud Services\n"
            "Invoice No: INV-2026-1023\n"
            "Billing period: 2026-10-01 to 2026-11-01\n"
            "Total: 25.00 USD\n"
            "api-request   1000   0.020   25.00\n")
        period = {f["name"]: f for f in candidate["fields"]}["period"]
        self.assertEqual({"from": "2026-10-01", "to": "2026-11-01", "to_exclusive": True}, period["value"])
        self.assertNotIn("period_end_converted_to_exclusive", period["flags"])
        self.assertIn("period_end_preserved_exclusive", period["flags"])
        self.assertEqual("calendar_date", period["date_type"])
        self.assertEqual("date_only_no_timezone_conversion", period["timezone_semantics"])
        self.assertIn("未 +1", period["note"])

    def test_month_period_uses_last_day_as_display_end(self):
        text = INVOICE_TEXT.replace(
            "Billing Period: 2026-10-01 - 2026-10-31", "Billing Period: 2026-10")
        candidate = self.add_pasted(text)
        period = {f["name"]: f for f in candidate["fields"]}["period"]
        self.assertEqual("2026-11-01", period["value"]["to"])
        self.assertIn("2026-10-31", period["note"])


class ToleranceTests(unittest.TestCase):
    def test_zero_decimal_currency_tolerance_stays_tight(self):
        # 1000 × 100 = 100000; 99000 is off by 1000, far beyond one yen.
        issues = bc._check_line_arithmetic(
            [{"quantity": "1000", "unit_price": "100", "amount": "99000"}], "JPY")
        self.assertEqual(["line_arithmetic_mismatch"],
                         [issue["code"] for issue in issues])
        self.assertEqual("100000", issues[0]["expected"])
        # A correctly printed line still passes.
        self.assertEqual([], bc._check_line_arithmetic(
            [{"quantity": "1000", "unit_price": "100", "amount": "100000"}], "JPY"))

    def test_two_decimal_tolerance_absorbs_printed_rounding(self):
        # 3 × 0.334 = 1.002 vs a line printed as 1.00: within one cent.
        self.assertEqual([], bc._check_line_arithmetic(
            [{"quantity": "3", "unit_price": "0.334", "amount": "1.00"}], "USD"))
        self.assertEqual(["line_arithmetic_mismatch"],
                         [issue["code"] for issue in bc._check_line_arithmetic(
                             [{"quantity": "3", "unit_price": "0.344",
                               "amount": "1.00"}], "USD")])


if __name__ == "__main__":
    unittest.main()
