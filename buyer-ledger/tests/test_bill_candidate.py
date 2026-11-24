"""V-08 acceptance tests: the OCR bill golden chain (review 2026-11-17 §4.3).

bill page/screenshot/pasted text -> OCR candidate draft -> field-level
evidence + review queue -> human booking into a verovo.period-job bundle ->
the existing close chain (ingest/mapping/calculate/report/review).

These tests were frozen red before the implementation existed.  The red lines
they guard:

- an OCR draft never books itself and never reaches canonical;
- key fields (amount/currency/period/...) must point back at a region of the
  screenshot or a span of the text; without that they queue for review and
  are never guessed;
- bad OCR ends BLOCKED_USER_INPUT with one concrete user action, and invents
  no fake precision;
- a duplicate invoice_no never double-books;
- a paid OCR engine is a requires_procurement branch, never a purchase;
- UNPROVABLE stays a verification conclusion, never a delivery terminal state.
"""
import contextlib
import io
import json
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
HEALTHCHECK_ROOT = REPO_ROOT / "healthcheck"

from buyer_ledger import bill_candidate as bc
from buyer_ledger import cli
from buyer_ledger.close import CloseError, confirm_mapping, ingest, init_case

INVOICE_TEXT = (
    "INVOICE No: INV-2026-1023\n"
    "Vendor: vendor-1\n"
    "Billing Period: 2026-10-01 - 2026-10-31\n"
    "api requests (agent tier)   1000   0.020   20.00\n"
    "TOTAL AMOUNT: 20.00 USD\n"
)

# A synthetic screenshot: the Vision engine output is injected, so the chain
# is exercised without depending on a real OCR engine or a network.
OCR_LINES = {
    "schema": "verovo.ocr-lines/1",
    "lines": [
        {"text": "INVOICE No: INV-2026-1023", "confidence": 0.98,
         "bbox": [0.08, 0.06, 0.55, 0.04]},
        {"text": "Vendor: vendor-1", "confidence": 0.97,
         "bbox": [0.08, 0.12, 0.35, 0.04]},
        {"text": "Billing Period: 2026-10-01 - 2026-10-31", "confidence": 0.95,
         "bbox": [0.08, 0.18, 0.70, 0.04]},
        {"text": "api requests (agent tier)   1000   0.020   20.00",
         "confidence": 0.93, "bbox": [0.08, 0.40, 0.80, 0.04]},
        {"text": "TOTAL AMOUNT: 20.00 USD", "confidence": 0.96,
         "bbox": [0.08, 0.90, 0.40, 0.05]},
    ],
}

USAGE_JSONL = (
    '{"record id":"u1","run":"r1","charge reference":"INV-2026-1023-1",'
    '"total cost":"20.00","currency":"USD","quantity":"1000",'
    '"unit":"request","rate plan":"api-request","financialized":true}\n'
)
RULE_MAP = {
    "schema_version": "1.0",
    "rules": [
        {"rule_id": "intake-bills", "kind": "intake", "format": "bills-csv",
         "version": "1", "confirmed_by": "Finance",
         "confirmed_at": "2026-10-01T00:00:00Z", "source_ref": "rules#intake-bills"},
        {"rule_id": "intake-usage", "kind": "intake", "format": "usage-jsonl",
         "version": "1", "confirmed_by": "Finance",
         "confirmed_at": "2026-10-01T00:00:00Z", "source_ref": "rules#intake-usage"},
        {"rule_id": "api-request", "kind": "pricing", "method": "per_unit",
         "unit_price": "0.020", "currency": "USD", "unit": "request",
         "version": "2026-10", "valid_from": "2026-10-01", "valid_to": "2026-10-31",
         "confirmed_by": "Procurement", "confirmed_at": "2026-10-01T00:00:00Z",
         "source_ref": "rules#api-request"},
    ],
}

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"synthetic screenshot bytes" * 4


class BillCandidateTestBase(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / "bill-candidates"
        self.bundle = self.root / "bundle"

    def add_pasted(self, text=INVOICE_TEXT):
        return bc.add_candidate(self.store, engine="text",
                                pasted_text=text)["candidate"]

    def add_screenshot(self, lines=None, image=PNG_BYTES):
        lines_doc = json.loads(json.dumps(OCR_LINES if lines is None else lines))
        return bc.add_candidate(self.store, engine="vision", image_bytes=image,
                                image_name="invoice-shot.png",
                                ocr_lines=lines_doc["lines"])["candidate"]

    def review_all_key_fields(self, candidate, reviewer="finance-reviewer"):
        payload = bc.projection(candidate)
        for item in payload["review_queue"]:
            bc.review_field(self.store, candidate["candidate_id"],
                            field=item["field"], value=item["value"],
                            reviewer=reviewer)
        return bc.load_candidate(self.store, candidate["candidate_id"])

    def write_close_inputs(self):
        self.bundle.mkdir(parents=True, exist_ok=True)
        (self.bundle / "usage-export.jsonl").write_text(USAGE_JSONL, encoding="utf-8")
        (self.bundle / "contract-rules.json").write_text(
            json.dumps(RULE_MAP, indent=2) + "\n", encoding="utf-8")


class IntakeDraftTests(BillCandidateTestBase):
    def test_pasted_text_produces_unreviewed_draft_with_evidence_spans(self):
        candidate = self.add_pasted()
        self.assertEqual("unreviewed", candidate["status"])
        fields = {f["name"]: f for f in candidate["fields"]}
        self.assertEqual("INV-2026-1023", fields["invoice_no"]["value"])
        self.assertEqual("vendor-1", fields["vendor"]["value"])
        self.assertEqual("20.00", fields["amount"]["value"])
        self.assertEqual("USD", fields["currency"]["value"])
        self.assertEqual({"from": "2026-10-01", "to": "2026-11-01",
                          "to_exclusive": True}, fields["period"]["value"])
        # every key field points back at a text span that quotes the source
        for name in bc.KEY_FIELDS:
            evidence = fields[name]["evidence"]
            self.assertEqual("text_span", evidence["kind"])
            self.assertTrue(evidence["excerpt"], "%s needs an excerpt" % name)
            self.assertGreater(evidence["line"], 0)
            self.assertLess(evidence["char_start"], evidence["char_end"])
        self.assertIn("20.00", fields["amount"]["evidence"]["excerpt"])
        self.assertIn("USD", fields["currency"]["evidence"]["excerpt"])
        items = fields["line_items"]["value"]
        self.assertEqual(1, len(items))
        self.assertEqual("20.00", items[0]["amount"])
        self.assertEqual("1000", items[0]["quantity"])
        # the source text is preserved verbatim
        source = self.store / candidate["candidate_id"] / "source.txt"
        self.assertEqual(INVOICE_TEXT, source.read_text(encoding="utf-8"))

    def test_screenshot_path_keeps_bbox_evidence_and_ocr_payload(self):
        candidate = self.add_screenshot()
        fields = {f["name"]: f for f in candidate["fields"]}
        evidence = fields["amount"]["evidence"]
        self.assertEqual("image_region", evidence["kind"])
        self.assertEqual(4, len(evidence["bbox"]))
        self.assertIn("20.00", evidence["excerpt"])
        ocr = json.loads((self.store / candidate["candidate_id"] / "ocr.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual("verovo.ocr-lines/1", ocr["schema"])
        source = self.store / candidate["candidate_id"] / "source.png"
        self.assertEqual(PNG_BYTES, source.read_bytes())
        self.assertEqual("ocr.txt", candidate["source"]["raw_text"])
        raw_text = self.store / candidate["candidate_id"] / "ocr.txt"
        self.assertEqual("".join(line["text"] + "\n" for line in OCR_LINES["lines"]),
                         raw_text.read_text(encoding="utf-8"))
        self.assertEqual(64, len(candidate["source"]["raw_text_sha256"]))

    def test_unextracted_key_fields_stay_null_not_empty_or_zero(self):
        candidate = self.add_pasted("Synthetic visible bill text with no parseable fields")
        fields = {f["name"]: f for f in candidate["fields"]}
        for name in bc.KEY_FIELDS:
            self.assertIsNone(fields[name]["value"], name)
            self.assertIn("not_extracted", fields[name]["flags"])
        with self.assertRaises(CloseError) as ctx:
            bc.book(self.store, candidate["candidate_id"], reviewer="finance",
                    into=self.bundle, account="acct-1")
        self.assertEqual("REVIEW_REQUIRED", ctx.exception.code)

    def test_ocr_draft_is_never_booked_automatically(self):
        candidate = self.add_pasted()
        payload = bc.projection(candidate)
        # high confidence still only means draft: the whole key-field queue is
        # pending and the terminal state asks a human to act.
        self.assertEqual("unreviewed", payload["status"])
        self.assertEqual({"vendor", "invoice_no", "period", "currency", "amount"},
                         {item["field"] for item in payload["review_queue"]})
        self.assertEqual("BLOCKED_USER_INPUT", payload["terminal"]["terminal_state"])
        self.assertEqual("review_pending", payload["terminal"]["reason"])
        with self.assertRaises(CloseError) as ctx:
            bc.book(self.store, candidate["candidate_id"], reviewer="finance",
                    into=self.bundle, account="acct-1")
        self.assertEqual("REVIEW_REQUIRED", ctx.exception.code)

    def test_key_field_without_evidence_region_queues_and_is_never_guessed(self):
        lines = json.loads(json.dumps(OCR_LINES))
        for line in lines["lines"]:
            if "TOTAL" in line["text"]:
                line.pop("bbox", None)
        candidate = self.add_screenshot(lines=lines)
        fields = {f["name"]: f for f in candidate["fields"]}
        self.assertIn("missing_evidence", fields["amount"]["flags"])
        self.assertIn("amount", [i["field"] for i in bc.projection(candidate)["review_queue"]])
        # booking is refused while the flag is unresolved
        with self.assertRaises(CloseError) as ctx:
            bc.book(self.store, candidate["candidate_id"], reviewer="finance",
                    into=self.bundle, account="acct-1")
        self.assertEqual("REVIEW_REQUIRED", ctx.exception.code)
        # a human review resolves the flag; only then can the candidate book
        candidate = self.review_all_key_fields(candidate)
        result = bc.book(self.store, candidate["candidate_id"], reviewer="finance",
                         into=self.bundle, account="acct-1")
        self.assertTrue(Path(result["export_path"]).is_file())

    def test_low_confidence_key_field_queues_for_review(self):
        lines = json.loads(json.dumps(OCR_LINES))
        for line in lines["lines"]:
            if "TOTAL" in line["text"]:
                line["confidence"] = 0.41
        candidate = self.add_screenshot(lines=lines)
        fields = {f["name"]: f for f in candidate["fields"]}
        self.assertIn("low_confidence", fields["amount"]["flags"])
        payload = bc.projection(candidate)
        self.assertIn("amount", [i["field"] for i in payload["review_queue"]])

    def test_line_items_sum_mismatch_flags_the_amount(self):
        text = INVOICE_TEXT.replace("0.020   20.00", "0.020   21.00")
        candidate = self.add_pasted(text)
        fields = {f["name"]: f for f in candidate["fields"]}
        self.assertIn("line_sum_mismatch", fields["amount"]["flags"])

    def test_bad_ocr_blocks_with_user_action_and_invents_nothing(self):
        garbled = {"schema": "verovo.ocr-lines/1", "lines": [
            {"text": "lNV0lC £##@! ~~~ \ufffd\ufffd\ufffd", "confidence": 0.31,
             "bbox": [0.1, 0.1, 0.5, 0.05]},
            {"text": "\ufffd\ufffd ##$% ^^^ 77", "confidence": 0.28,
             "bbox": [0.1, 0.2, 0.5, 0.05]},
        ]}
        candidate = self.add_screenshot(lines=garbled)
        self.assertEqual("blocked_user_input", candidate["status"])
        terminal = bc.projection(candidate)["terminal"]
        self.assertEqual("BLOCKED_USER_INPUT", terminal["terminal_state"])
        self.assertEqual("bad_ocr", terminal["reason"])
        self.assertTrue(terminal["user_action"])
        fields = {f["name"]: f for f in candidate["fields"]}
        for name in bc.KEY_FIELDS:
            self.assertFalse(fields[name]["value"],
                             "%s must stay empty; bad OCR never becomes precision" % name)
        with self.assertRaises(CloseError) as ctx:
            bc.book(self.store, candidate["candidate_id"], reviewer="finance",
                    into=self.bundle, account="acct-1")
        self.assertEqual("INPUT_REQUIRED", ctx.exception.code)

    def test_duplicate_invoice_no_never_double_books(self):
        first = self.add_pasted()
        first = self.review_all_key_fields(first)
        second = self.add_pasted()
        self.assertEqual([first["candidate_id"]], second.get("duplicate_of"))
        bc.book(self.store, first["candidate_id"], reviewer="finance",
                into=self.bundle, account="acct-1")
        second = self.review_all_key_fields(second)
        with self.assertRaises(CloseError) as ctx:
            bc.book(self.store, second["candidate_id"], reviewer="finance",
                    into=self.bundle, account="acct-1")
        self.assertEqual("REVIEW_REQUIRED", ctx.exception.code)
        self.assertIn("duplicate", str(ctx.exception))

    def test_requires_procurement_is_a_structured_branch(self):
        result = bc.add_candidate(self.store, engine="paid-cloud-ocr",
                                  pasted_text=INVOICE_TEXT)
        payload = result["procurement"]
        self.assertIsNone(result["candidate"])
        self.assertEqual("BLOCKED_USER_INPUT", payload["terminal_state"])
        self.assertEqual("requires_procurement", payload["reason"])
        for key in ("item", "why", "user_action", "alternatives"):
            self.assertTrue(payload[key], "§5 payload needs %s" % key)
        self.assertFalse(payload["transaction_executed"])
        if self.store.exists():
            self.assertEqual([], list(self.store.iterdir()),
                             "a procurement refusal must not create candidate state")
        self.assertEqual([], bc.list_candidates(self.store))

    def test_vision_engine_without_runner_is_input_required(self):
        """runner 缺失 → INPUT_REQUIRED。平台门在前：mock darwin 资格后 Linux CI 也走这条路径。"""
        with patch("buyer_ledger.bill_candidate.platform.system",
                   return_value="Darwin"):
            with self.assertRaises(CloseError) as ctx:
                bc.add_candidate(self.store, engine="vision", image_bytes=PNG_BYTES,
                                 image_name="shot.png", ocr_lines=None,
                                 ocr_script=self.root / "missing.swift")
        self.assertEqual("INPUT_REQUIRED", ctx.exception.code)
        self.assertIn("paste", str(ctx.exception).lower())

    def test_non_darwin_local_vision_is_explicitly_unsupported(self):
        image = self.root / "shot.png"
        image.write_bytes(PNG_BYTES)
        with patch("buyer_ledger.bill_candidate.platform.system", return_value="Linux"), \
                patch("buyer_ledger.bill_candidate.subprocess.run") as run:
            with self.assertRaises(CloseError) as ctx:
                bc.add_candidate(self.store, engine="vision", image_path=image,
                                 ocr_script=self.root / "ocr_image.swift")
        self.assertEqual("UNSUPPORTED_SCOPE", ctx.exception.code)
        self.assertIn("unavailable on non-darwin", str(ctx.exception))
        run.assert_not_called()

    def test_currency_symbol_alone_is_ambiguous_and_queues(self):
        text = INVOICE_TEXT.replace("20.00 USD", "$20.00")
        candidate = self.add_pasted(text)
        fields = {f["name"]: f for f in candidate["fields"]}
        self.assertIsNone(fields["currency"]["value"])
        self.assertIn("ambiguous_symbol", fields["currency"]["flags"])


class FieldReviewTests(BillCandidateTestBase):
    def test_review_corrects_value_and_keeps_original(self):
        candidate = self.add_pasted()
        reviewed = bc.review_field(self.store, candidate["candidate_id"],
                                   field="amount", value="19.50",
                                   reviewer="finance", note="OCR read 20.00; invoice says 19.50")
        fields = {f["name"]: f for f in reviewed["fields"]}
        self.assertEqual("19.50", fields["amount"]["value"])
        self.assertEqual("20.00", fields["amount"]["review"]["original_value"])
        self.assertEqual("finance", fields["amount"]["review"]["reviewer"])
        self.assertEqual("confirmed", fields["amount"]["status"])

    def test_review_validates_values(self):
        candidate = self.add_pasted()
        for field, bad in (("currency", "dollars"), ("amount", "twenty"),
                           ("period", "October"), ("invoice_no", "")):
            with self.assertRaises(CloseError) as ctx:
                bc.review_field(self.store, candidate["candidate_id"],
                                field=field, value=bad, reviewer="finance")
            self.assertEqual("INPUT_REQUIRED", ctx.exception.code)
        with self.assertRaises(CloseError) as ctx:
            bc.review_field(self.store, candidate["candidate_id"],
                            field="amount", value="1.00", reviewer="")
        self.assertEqual("INPUT_REQUIRED", ctx.exception.code)

    def test_unknown_field_review_is_rejected(self):
        candidate = self.add_pasted()
        with self.assertRaises(CloseError) as ctx:
            bc.review_field(self.store, candidate["candidate_id"],
                            field="tax_rate", value="0.06", reviewer="finance")
        self.assertEqual("INPUT_REQUIRED", ctx.exception.code)


class BookingTests(BillCandidateTestBase):
    def test_booked_candidate_exports_job_and_bills_rows(self):
        self.write_close_inputs()
        candidate = self.review_all_key_fields(self.add_pasted())
        result = bc.book(self.store, candidate["candidate_id"],
                         reviewer="finance", into=self.bundle, account="acct-1",
                         usage="usage-export.jsonl", rule_map="contract-rules.json")
        job = json.loads((self.bundle / "period-job.json").read_text(encoding="utf-8"))
        self.assertEqual("verovo.period-job/1", job["schema"])
        self.assertEqual("vendor-1", job["vendor"])
        self.assertEqual("USD", job["currency"])
        self.assertEqual("acct-1", job["account"])
        self.assertEqual("2026-10-01", job["period"]["from"])
        self.assertTrue(job["period"]["to_exclusive"])
        roles = {entry["role"] for entry in job["sources"]}
        self.assertEqual({"bills", "invoice", "usage"}, roles)
        rows = Path(result["export_path"]).read_text(encoding="utf-8")
        self.assertIn("INV-2026-1023-1", rows)
        self.assertIn("20.00", rows)
        self.assertIn("field_review_attested", rows)
        booked = bc.load_candidate(self.store, candidate["candidate_id"])
        self.assertEqual("booked", booked["status"])
        terminal = bc.projection(booked)["terminal"]
        self.assertEqual("DELIVERED", terminal["terminal_state"])
        self.assertTrue(terminal["receipt"]["export_sha256"])
        # rebooking is refused
        with self.assertRaises(CloseError) as ctx:
            bc.book(self.store, candidate["candidate_id"], reviewer="finance",
                    into=self.bundle, account="acct-1")
        self.assertEqual("REVIEW_REQUIRED", ctx.exception.code)

    def test_book_refuses_existing_period_job(self):
        self.bundle.mkdir(parents=True)
        (self.bundle / "period-job.json").write_text("{}", encoding="utf-8")
        candidate = self.review_all_key_fields(self.add_pasted())
        with self.assertRaises(CloseError) as ctx:
            bc.book(self.store, candidate["candidate_id"], reviewer="finance",
                    into=self.bundle, account="acct-1")
        self.assertEqual("REVIEW_REQUIRED", ctx.exception.code)

    def test_book_requires_account(self):
        candidate = self.review_all_key_fields(self.add_pasted())
        with self.assertRaises(CloseError) as ctx:
            bc.book(self.store, candidate["candidate_id"], reviewer="finance",
                    into=self.bundle, account="")
        self.assertEqual("INPUT_REQUIRED", ctx.exception.code)

    def test_reviewed_total_cannot_hide_line_item_sum_mismatch(self):
        text = INVOICE_TEXT.replace("0.020   20.00", "0.020   21.00")
        candidate = self.review_all_key_fields(self.add_pasted(text))
        with self.assertRaises(CloseError) as ctx:
            bc.book(self.store, candidate["candidate_id"], reviewer="finance",
                    into=self.bundle, account="acct-1")
        self.assertEqual("REVIEW_REQUIRED", ctx.exception.code)
        self.assertIn("21.00", str(ctx.exception))
        self.assertIn("20.00", str(ctx.exception))
        self.assertFalse((self.bundle / "period-job.json").exists())


class GoldenChainEndToEndTests(BillCandidateTestBase):
    """Pasted text -> candidate -> field review -> booking -> month-end close."""

    def _run_close(self, case):
        init_case(case, job_path=self.bundle / "period-job.json")
        ingest(case, bundle=self.bundle)
        confirm_mapping(case, reviewer="finance-reviewer")
        from buyer_ledger import close_report
        close_report.calculate(case)
        report = close_report.build_report(case)
        review = close_report.record_review(case, reviewer="finance-reviewer",
                                            approved_scope="ocr golden chain")
        return report, review

    def test_paste_path_reaches_month_end_close(self):
        self.write_close_inputs()
        candidate = self.review_all_key_fields(self.add_pasted())
        bc.book(self.store, candidate["candidate_id"], reviewer="finance",
                into=self.bundle, account="acct-1",
                usage="usage-export.jsonl", rule_map="contract-rules.json")
        case = self.root / "close-2026-10"
        report, review = self._run_close(case)
        # the reviewed invoice total is the only billed amount: 20.00
        self.assertEqual("20.00", report["amounts"]["invoice_total"])
        self.assertEqual(1, len(report["line_reconciliation"]))
        # independent recount from usage x pricing matches the reviewed invoice
        self.assertEqual(Decimal("0"), Decimal(report["amounts"]["open_difference"]))
        # vendor-only evidence: Buyer Verified honestly stays UNPROVABLE
        self.assertEqual("UNPROVABLE", report["verdicts"]["Buyer Verified"]["status"])
        # UNPROVABLE is a verification conclusion, never a delivery terminal state
        terminal = bc.projection(bc.load_candidate(
            self.store, candidate["candidate_id"]))["terminal"]
        self.assertEqual("DELIVERED", terminal["terminal_state"])
        # every open item carries a disposition (owner + next evidence)
        for item in report["open_items"]:
            self.assertTrue(item["owner"], "open item %s needs an owner" % item["id"])
            self.assertTrue(item["next_evidence"])
        # review records the reviewer but never closes items or authorizes payment
        self.assertFalse(review["payment_authorization"])
        self.assertTrue(review["unresolved_items"] or
                        report["open_items"] == [])
        # the booked row points back at the candidate evidence
        bills = (case / "canonical" / "bills.csv").read_text(encoding="utf-8")
        self.assertIn("bill-candidates/%s" % candidate["candidate_id"], bills)
        self.assertIn(candidate["fields"][0]["evidence"]["kind"], ("text_span", "image_region"))

    def test_screenshot_path_reaches_month_end_close(self):
        self.write_close_inputs()
        candidate = self.add_screenshot()
        candidate = self.review_all_key_fields(candidate)
        bc.book(self.store, candidate["candidate_id"], reviewer="finance",
                into=self.bundle, account="acct-1",
                usage="usage-export.jsonl", rule_map="contract-rules.json")
        case = self.root / "close-2026-10-shot"
        report, _ = self._run_close(case)
        self.assertEqual("20.00", report["amounts"]["invoice_total"])
        # the screenshot itself stays a review document, never a ledger source
        manifest = json.loads((case / "canonical" / "manifest.json")
                              .read_text(encoding="utf-8"))
        self.assertIn("documents", manifest)
        self.assertEqual("reviewed", manifest["documents"][0]["review_status"])
        self.assertEqual("booked", manifest["documents"][0]["booking_status"])
        self.assertEqual("finance", manifest["documents"][0]["booked_by"])
        self.assertNotIn("invoices", manifest["sources"])
        # field evidence still points into the screenshot regions
        booked = bc.load_candidate(self.store, candidate["candidate_id"])
        fields = {f["name"]: f for f in booked["fields"]}
        self.assertEqual("image_region", fields["amount"]["evidence"]["kind"])

    def test_difference_becomes_exception_material_with_disposition(self):
        # the vendor's usage export says 800 requests (800 x 0.020 = 16.00)
        # while the reviewed invoice books 20.00: the recount difference must
        # surface as exception material with a disposition, never silently net.
        self.write_close_inputs()
        (self.bundle / "usage-export.jsonl").write_text(
            USAGE_JSONL.replace('"quantity":"1000"', '"quantity":"800"')
                       .replace('"total cost":"20.00"', '"total cost":"16.00"'),
            encoding="utf-8")
        candidate = self.review_all_key_fields(self.add_pasted())
        bc.book(self.store, candidate["candidate_id"], reviewer="finance",
                into=self.bundle, account="acct-1",
                usage="usage-export.jsonl", rule_map="contract-rules.json")
        case = self.root / "close-2026-10-diff"
        report, _ = self._run_close(case)
        self.assertEqual("20.00", report["amounts"]["invoice_total"])
        self.assertNotEqual(Decimal("0"), Decimal(report["amounts"]["open_difference"]))
        mismatch = [item for item in report["open_items"]
                    if "mismatch" in item["kind"] or "difference" in item["kind"]]
        self.assertTrue(mismatch, "a real difference must surface as exception material")
        for item in mismatch:
            self.assertTrue(item["next_evidence"],
                            "difference items need a disposition action")

    def test_reviewed_ocr_multi_currency_never_reaches_a_passing_close(self):
        self.write_close_inputs()
        # Keep the approved pricing rule in the invoice's EUR scope so the only
        # cross-currency conflict is the independently supplied USD usage row.
        rules = json.loads(json.dumps(RULE_MAP))
        rules["rules"][2]["currency"] = "EUR"
        (self.bundle / "contract-rules.json").write_text(
            json.dumps(rules, indent=2) + "\n", encoding="utf-8")
        candidate = self.review_all_key_fields(
            self.add_pasted(INVOICE_TEXT.replace("20.00 USD", "20.00 EUR")))
        bc.book(self.store, candidate["candidate_id"], reviewer="finance",
                into=self.bundle, account="acct-1",
                usage="usage-export.jsonl", rule_map="contract-rules.json")
        case = self.root / "close-2026-10-multi-currency"
        init_case(case, job_path=self.bundle / "period-job.json")
        result = ingest(case, bundle=self.bundle)
        validation = result["mapping_draft"]["validation"]
        self.assertTrue(validation["blocked"])
        self.assertEqual("UNSUPPORTED_SCOPE", validation["blocked_code"])
        self.assertEqual({"EUR"},
                         {row["currency"] for row in validation["currencies"]})
        self.assertIn("USD", validation["blocked_reason"])
        self.assertIn("EUR", validation["blocked_reason"])
        with self.assertRaises(CloseError) as ctx:
            confirm_mapping(case, reviewer="finance-reviewer")
        self.assertEqual("UNSUPPORTED_SCOPE", ctx.exception.code)
        self.assertFalse((case / "canonical").exists())
        self.assertFalse((case / "calculation.json").exists())

    def test_unprovable_stays_honest_without_independent_record(self):
        # booked invoice with an approved intake rule but no usage record: the
        # close chain must keep Invoice Reconciled UNPROVABLE, not a fake PASS
        self.bundle.mkdir(parents=True)
        minimal_rules = {"schema_version": "1.0", "rules": [RULE_MAP["rules"][0]]}
        (self.bundle / "contract-rules.json").write_text(
            json.dumps(minimal_rules, indent=2) + "\n", encoding="utf-8")
        candidate = self.review_all_key_fields(self.add_pasted())
        bc.book(self.store, candidate["candidate_id"], reviewer="finance",
                into=self.bundle, account="acct-1",
                rule_map="contract-rules.json")
        case = self.root / "close-2026-10-solo"
        report, _ = self._run_close(case)
        self.assertEqual("UNPROVABLE", report["verdicts"]["Invoice Reconciled"]["status"])
        self.assertNotEqual("PASS", report["finance_status"])


class CliContractTests(BillCandidateTestBase):
    def _run(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_add_prints_draft_and_refuses_auto_booking(self):
        bill = self.root / "bill.txt"
        bill.write_text(INVOICE_TEXT, encoding="utf-8")
        code, out = self._run(["bill-candidate", "add", str(self.store),
                               "--text", str(bill)])
        self.assertEqual(0, code)
        self.assertIn("candidate", out)
        self.assertIn("unreviewed", out)
        self.assertIn("BLOCKED_USER_INPUT", out)
        self.assertIn("review", out)

    def test_add_bad_ocr_exits_two_with_user_action(self):
        garbled = {"schema": "verovo.ocr-lines/1", "lines": [
            {"text": "\ufffd\ufffd ##@! ~~~", "confidence": 0.2,
             "bbox": [0.1, 0.1, 0.4, 0.04]}]}
        ocr_json = self.root / "ocr.json"
        ocr_json.write_text(json.dumps(garbled), encoding="utf-8")
        shot = self.root / "shot.png"
        shot.write_bytes(PNG_BYTES)
        with self.assertRaises(SystemExit) as ctx:
            self._run(["bill-candidate", "add", str(self.store),
                       "--image", str(shot), "--ocr-json", str(ocr_json)])
        self.assertEqual(2, ctx.exception.code)

    def test_add_paid_engine_prints_procurement_json(self):
        bill = self.root / "bill.txt"
        bill.write_text(INVOICE_TEXT, encoding="utf-8")
        with self.assertRaises(SystemExit) as ctx:
            self._run(["bill-candidate", "add", str(self.store),
                       "--text", str(bill), "--engine", "paid-cloud-ocr"])
        self.assertEqual(2, ctx.exception.code)

    def test_show_and_list_round_trip(self):
        self.add_pasted()
        code, out = self._run(["bill-candidate", "list", str(self.store)])
        self.assertEqual(0, code)
        self.assertIn("cand-", out)
        code, out = self._run(["bill-candidate", "show", str(self.store), "cand-0001"])
        self.assertEqual(0, code)
        payload = json.loads(out)
        self.assertEqual("verovo.bill-candidate/1", payload["schema"])

    def test_cli_review_and_book_flow(self):
        self.write_close_inputs()
        bill = self.root / "bill.txt"
        bill.write_text(INVOICE_TEXT, encoding="utf-8")
        self._run(["bill-candidate", "add", str(self.store), "--text", str(bill)])
        payload = json.loads(self._run(
            ["bill-candidate", "show", str(self.store), "cand-0001"])[1])
        for item in payload["review_queue"]:
            value = item["value"]
            if isinstance(value, dict):  # period: from..to half-open range
                value = "%s..%s" % (value["from"], value["to"])
            code, out = self._run([
                "bill-candidate", "review", str(self.store), "cand-0001",
                "--field", item["field"], "--value", str(value),
                "--by", "finance"])
            self.assertEqual(0, code)
        code, out = self._run([
            "bill-candidate", "book", str(self.store), "cand-0001",
            "--by", "finance", "--into", str(self.bundle),
            "--account", "acct-1", "--usage", "usage-export.jsonl",
            "--rule-map", "contract-rules.json"])
        self.assertEqual(0, code)
        self.assertIn("period-job.json", out)
        self.assertTrue((self.bundle / "period-job.json").is_file())


class LineArithmeticTests(BillCandidateTestBase):
    def test_line_arithmetic_pass_and_mismatch_flags_row_with_confidence_drop(self):
        passed = self.add_pasted()
        items = {f["name"]: f for f in passed["fields"]}["line_items"]
        self.assertEqual([], items["line_issues"])
        self.assertIn("校验通过", items["review_note"])
        self.assertGreaterEqual(passed["confidence"], 0.5)

        text = INVOICE_TEXT.replace("0.020   20.00", "0.020   21.00")
        candidate = self.add_pasted(text)
        fields = {f["name"]: f for f in candidate["fields"]}
        items = fields["line_items"]
        self.assertEqual(1, len(items["line_issues"]))
        issue = items["line_issues"][0]
        self.assertEqual("line_arithmetic_mismatch", issue["code"])
        self.assertEqual({"quantity": "1000", "unit_price": "0.020",
                          "amount": "21.00"}, issue["raw"])
        self.assertEqual("20.00", issue["expected"])
        self.assertIn("1000", issue["detail"])
        self.assertIn("0.020", issue["detail"])
        self.assertIn("21.00", issue["detail"])
        self.assertTrue(items["value"][0]["needs_review"])
        self.assertIn("line_arithmetic_mismatch", items["flags"])
        self.assertLessEqual(items["confidence"], 0.5)
        self.assertLessEqual(candidate["confidence"], 0.5)
        self.assertIn("line_items",
                      [i["field"] for i in bc.projection(candidate)["review_queue"]])
        self.assertTrue(any("line_items" in note
                            for note in candidate["review_notes"]))


class PeriodSemanticsTests(BillCandidateTestBase):
    def test_period_end_is_exclusive_with_explanation(self):
        candidate = self.add_pasted()
        period = {f["name"]: f for f in candidate["fields"]}["period"]
        self.assertEqual({"from": "2026-10-01", "to": "2026-11-01",
                          "to_exclusive": True}, period["value"])
        self.assertTrue(period["period_end_exclusive"])
        self.assertIn("不含当日处理", period["note"])
        self.assertIn("2026-10-31", period["note"])
        self.assertIn("[2026-10-01, 2026-11-01)", period["note"])
        self.assertIn("period_end_converted_to_exclusive", period["flags"])
        self.assertLess(period["confidence"], 1.0)
        self.assertTrue(period["review_note"])
        self.assertTrue(candidate["period_end_exclusive"])
        self.assertEqual(period["note"], candidate["period_end_note"])

        month = self.add_pasted(
            "Billing Period: 2026-10\n"
            "api requests (agent tier)   1000   0.020   20.00\n"
            "TOTAL AMOUNT: 20.00 USD\n")
        month_period = {f["name"]: f for f in month["fields"]}["period"]
        self.assertEqual("2026-11-01", month_period["value"]["to"])
        self.assertIn("2026-10-31", month_period["note"])


if __name__ == "__main__":
    unittest.main()
