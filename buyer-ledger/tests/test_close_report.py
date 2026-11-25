"""V-03 acceptance tests (AC V16-V23) and the review-signature gate.

The report is a projection over the V-02 canonical bundle and the existing
canonical finance chain.  Every number must be replayable from the canonical
inputs + approved rule map; the four completion axes stay independent from the
PASS/FAIL/UNKNOWN/UNPROVABLE evidence verdicts.

Frozen red before implementation.
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HEALTHCHECK_ROOT = REPO_ROOT / "healthcheck"
if str(HEALTHCHECK_ROOT) not in sys.path:
    sys.path.insert(0, str(HEALTHCHECK_ROOT))

from buyer_ledger import cli
from buyer_ledger import close_report
from buyer_ledger import close_revision
from buyer_ledger.close import (
    CloseError,
    confirm_mapping,
    init_case,
    ingest,
    load_period_job,
)

BILLS_HEADER = (
    "Charge Reference,Total Amount,Currency,Quantity,Unit Price,Rate Plan,Task,"
    "Service Date,Adjustment Type,Adjustment State\n"
)
USAGE = (
    '{"record id":"u1","run":"r1","charge reference":"ch-1","total cost":"20.00",'
    '"currency":"USD","quantity":"1000","unit":"request","rate plan":"api-request",'
    '"financialized":true}\n'
)
OUTCOME_HEADER = (
    "task_id,outcome_id,status,verified,accepted_by,accepted_at,source_ref,"
    "value,currency\n"
)


def _rules(unit_price="0.020"):
    return {
        "schema_version": "1.0",
        "rules": [
            {"rule_id": "intake-bills", "kind": "intake", "format": "bills-csv",
             "version": "1", "confirmed_by": "Finance",
             "confirmed_at": "2026-10-01T00:00:00Z", "source_ref": "rules#intake-bills"},
            {"rule_id": "intake-usage", "kind": "intake", "format": "usage-jsonl",
             "version": "1", "confirmed_by": "Finance",
             "confirmed_at": "2026-10-01T00:00:00Z", "source_ref": "rules#intake-usage"},
            {"rule_id": "intake-tasks", "kind": "intake", "format": "tasks-csv",
             "version": "1", "confirmed_by": "Finance",
             "confirmed_at": "2026-10-01T00:00:00Z", "source_ref": "rules#intake-tasks"},
            {"rule_id": "intake-runs", "kind": "intake", "format": "runs-jsonl",
             "version": "1", "confirmed_by": "Finance",
             "confirmed_at": "2026-10-01T00:00:00Z", "source_ref": "rules#intake-runs"},
            {"rule_id": "intake-outcomes", "kind": "intake", "format": "outcomes-csv",
             "version": "1", "confirmed_by": "Finance",
             "confirmed_at": "2026-10-01T00:00:00Z", "source_ref": "rules#intake-outcomes"},
            {"rule_id": "api-request", "kind": "pricing", "method": "per_unit",
             "unit_price": unit_price, "currency": "USD", "unit": "request",
             "version": "2026-10", "valid_from": "2026-10-01",
             "valid_to": "2026-10-31", "confirmed_by": "Procurement",
             "confirmed_at": "2026-10-01T00:00:00Z",
             "source_ref": "rules#api-request"},
        ],
    }


def _job(sources, *, rule_map="contract-rules.json", extra=None):
    job = {
        "schema": "verovo.period-job/1",
        "job_id": "close-2026-10-a",
        "buyer": "buyer-1", "vendor": "vendor-1", "account": "acct-1",
        "buyer_signers": ["buyer-1", "controller", "finance-reviewer", "finance", "test-role"],
        "period": {"from": "2026-10-01", "to": "2026-11-01",
                   "to_exclusive": True, "timezone": "UTC"},
        "currency": "USD",
        "sources": sources,
        "rule_map": rule_map,
        "task_economics_requested": False,
        "approved_by": None,
    }
    if extra:
        job.update(extra)
    return job


def _source(role, path, control_domain="vendor"):
    return {"role": role, "path": path, "control_domain": control_domain}


class CloseReportTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.case = self.root / "close-2026-10"

    def write_bundle(self, *, sources, bills=None, usage=None, outcomes=None,
                     rule_map=None, job_extra=None):
        job = _job(sources, extra=job_extra)
        (self.bundle / "period-job.json").write_text(
            json.dumps(job, indent=2) + "\n", encoding="utf-8")
        if bills is not None:
            (self.bundle / "bill-export.csv").write_text(bills, encoding="utf-8")
        if usage is not None:
            (self.bundle / "usage-export.jsonl").write_text(usage, encoding="utf-8")
        if outcomes is not None:
            (self.bundle / "outcomes.csv").write_text(outcomes, encoding="utf-8")
        (self.bundle / "contract-rules.json").write_text(
            json.dumps(rule_map if rule_map is not None else _rules(),
                       indent=2) + "\n", encoding="utf-8")

    def make_case(self, **kwargs):
        self.write_bundle(**kwargs)
        init_case(self.case, job_path=self.bundle / "period-job.json")
        ingest(self.case, bundle=self.bundle)
        confirm_mapping(self.case, reviewer="finance-reviewer")
        return self.case

    def report(self, **kwargs):
        self.make_case(**kwargs)
        close_report.calculate(self.case)
        return close_report.build_report(self.case)

    def full_bills(self, amount="20.00", price="0.020", task="t1"):
        return BILLS_HEADER + "ch-1,%s,USD,1000,%s,api-request,%s,2026-10-05,,\n" % (
            amount, price, task)

    def load_report(self):
        return json.loads((self.case / "report" / "report.json")
                          .read_text(encoding="utf-8"))


class IndependentAxesTests(CloseReportTestBase):
    """AC V16/V17/V18/V19/V20: axes and verdicts are separate dimensions."""

    def test_v16_single_bill_reprices_but_is_not_reconciled(self):
        report = self.report(
            sources=[_source("bills", "bill-export.csv")],
            bills=self.full_bills())
        self.assertEqual("PASS", report["verdicts"]["Rule Repriced"]["status"])
        self.assertEqual("UNPROVABLE",
                         report["verdicts"]["Invoice Reconciled"]["status"])
        line = report["line_reconciliation"][0]
        self.assertEqual("UNPROVABLE", line["invoice_reconciled"])
        self.assertEqual("matched", line["recalculation_status"])
        self.assertEqual(1, len(report["verdicts"]["Rule Repriced"]["rows"]))
        # Finance can be complete while the invoice is not cross-source
        # reconciled: completion is an operating axis, not a zero difference.
        self.assertEqual("complete", report["finance_status"])
        self.assertEqual(Decimal("20.00"),
                         Decimal(report["amounts"]["open_difference"]))
        self.assertIn("not a payment authorization", report["disclaimer"])

    def test_markdown_verdict_labels_are_bilingual(self):
        report = self.report(
            sources=[_source("bills", "bill-export.csv")],
            bills=self.full_bills())
        text = close_report._report_markdown(report)
        self.assertIn("Rule Repriced（规则重算）", text)
        self.assertIn("Buyer Verified（买方独立核验）", text)
        self.assertIn("finance_status（财务）", text)

    def test_v17_buyer_usage_matches_rule_but_vendor_bill_differs(self):
        report = self.report(
            sources=[_source("bills", "bill-export.csv"),
                     _source("usage", "usage-export.jsonl", "buyer")],
            bills=self.full_bills(amount="25.00"),
            usage=USAGE)
        self.assertEqual("FAIL", report["verdicts"]["Invoice Reconciled"]["status"])
        self.assertEqual("FAIL", report["verdicts"]["Rule Repriced"]["status"])
        self.assertEqual("PASS", report["verdicts"]["Buyer Verified"]["status"])
        self.assertNotEqual("complete", report["finance_status"])
        amounts = report["amounts"]
        self.assertEqual(Decimal("25.00"), Decimal(amounts["invoice_total"]))
        self.assertEqual(Decimal("20.00"),
                         Decimal(amounts["matched_postings"]))
        self.assertEqual(Decimal("5.00"), Decimal(amounts["open_difference"]))
        self.assertEqual(0, len(report["realized_savings"]["claims"]))

    def test_v18_equal_amounts_do_not_auto_verify_buyer(self):
        report = self.report(
            sources=[_source("bills", "bill-export.csv"),
                     _source("usage", "usage-export.jsonl")],
            bills=self.full_bills(),
            usage=USAGE,
            rule_map=_rules(unit_price="0.030"))
        self.assertEqual("PASS",
                         report["verdicts"]["Invoice Reconciled"]["status"])
        self.assertEqual("FAIL", report["verdicts"]["Rule Repriced"]["status"])
        self.assertEqual("UNPROVABLE",
                         report["verdicts"]["Buyer Verified"]["status"])
        self.assertNotEqual("complete", report["finance_status"])

    def test_v19_verified_delivery_failure_is_a_fail_not_partial(self):
        report = self.report(
            sources=[_source("bills", "bill-export.csv"),
                     _source("usage", "usage-export.jsonl"),
                     _source("outcomes", "outcomes.csv", "buyer")],
            bills=self.full_bills(),
            usage=USAGE,
            outcomes=OUTCOME_HEADER +
            "t1,o1,failure,true,buyer-1,2026-10-31T00:00:00Z,outcomes.csv#o1,,USD\n")
        self.assertEqual("complete", report["delivery_status"])
        self.assertEqual("complete", report["verification_status"])
        self.assertEqual("FAIL", report["verdicts"]["Delivery Accepted"]["status"])
        row = report["verdicts"]["Delivery Accepted"]["rows"][0]
        self.assertEqual("FAIL", row["status"])
        self.assertEqual("t1", row["task_id"])

    def test_v20_absent_outcomes_are_not_applicable_empty_outcomes_are_partial(self):
        absent = self.report(
            sources=[_source("bills", "bill-export.csv"),
                     _source("usage", "usage-export.jsonl")],
            bills=self.full_bills(),
            usage=USAGE)
        for axis in ("verification_status", "delivery_status", "economics_status"):
            self.assertEqual("not_applicable", absent[axis])
        self.assertEqual("not_applicable",
                         absent["verdicts"]["Delivery Accepted"]["status"])
        self.assertEqual("not_applicable", absent["task_economics"]["status"])

        # A fresh case with an outcomes source present but an empty file.
        with tempfile.TemporaryDirectory() as tmp:
            old_case = self.case
            self.root = Path(tmp)
            self.bundle = self.root / "bundle"
            self.bundle.mkdir()
            self.case = self.root / "close-2026-10"
            empty = self.report(
                sources=[_source("bills", "bill-export.csv"),
                         _source("usage", "usage-export.jsonl"),
                         _source("outcomes", "outcomes.csv", "buyer")],
                bills=self.full_bills(),
                usage=USAGE,
                outcomes="")
            self.assertEqual("partial", empty["verification_status"])
            self.assertEqual("partial", empty["delivery_status"])
            self.assertEqual("partial", empty["economics_status"])
            self.assertEqual("partial", empty["task_economics"]["status"])
            self.case = old_case


class AcceptanceAuthorityTests(CloseReportTestBase):
    """V-21: the report names the controlled acceptance owner on its first line."""

    def _delivery_sources(self):
        return [_source("bills", "bill-export.csv"),
                _source("usage", "usage-export.jsonl"),
                _source("outcomes", "outcomes.csv", "buyer")]

    def test_report_first_line_names_the_authorized_signer(self):
        report = self.report(
            sources=self._delivery_sources(),
            bills=self.full_bills(),
            usage=USAGE,
            outcomes=OUTCOME_HEADER +
            "t1,o1,success,true,Buyer Owner,2026-10-31T00:00:00Z,"
            "outcomes.csv#o1,100.00,USD\n",
            job_extra={"buyer_signers": ["Buyer Owner"]})
        self.assertEqual("PASS", report["verdicts"]["Delivery Accepted"]["status"])
        authority = report["acceptance_authority"]
        self.assertTrue(authority["configured"])
        self.assertEqual("buyer-1", authority["org_id"])
        self.assertEqual(["Buyer Owner"], authority["qualified_signers"])
        lines = close_report._report_markdown(report).splitlines()
        self.assertEqual(
            "> 实际验收方：Buyer Owner（买方 buyer-1）",
            lines[2])

    def test_vendor_self_acceptance_leaves_the_report_unidentified(self):
        report = self.report(
            sources=self._delivery_sources(),
            bills=self.full_bills(),
            usage=USAGE,
            outcomes=OUTCOME_HEADER +
            "t1,o1,success,true,vendor-1,2026-10-31T00:00:00Z,"
            "outcomes.csv#o1,100.00,USD\n")
        self.assertEqual("UNPROVABLE",
                         report["verdicts"]["Delivery Accepted"]["status"])
        authority = report["acceptance_authority"]
        self.assertEqual([], authority["qualified_signers"])
        self.assertIn("无合格验收记录", authority["headline"])
        self.assertIn("no qualified controlled-buyer acceptance record",
                      "\n".join(report["limitations"]))
        lines = close_report._report_markdown(report).splitlines()
        self.assertIn("实际验收方：未识别", lines[2])


class AmountInvariantTests(CloseReportTestBase):
    """AC V21/V22: mutually exclusive lines and no invented savings."""

    def test_v21_multiple_findings_on_one_line_count_once(self):
        report = self.report(
            sources=[_source("bills", "bill-export.csv"),
                     _source("usage", "usage-export.jsonl")],
            bills=self.full_bills(amount="25.00"),
            usage=USAGE)
        line = report["line_reconciliation"][0]
        self.assertEqual("ch-1", line["charge_id"])
        self.assertGreaterEqual(len(line["finding_ids"]), 2)
        findings = [f for f in report["findings"] if f["line_ref"] == line["line_ref"]]
        self.assertGreaterEqual(len(findings), 2)
        self.assertEqual(Decimal("5.00"),
                         Decimal(report["amounts"]["open_difference"]))
        self.assertEqual(1, len(report["line_reconciliation"]))
        self.assertNotEqual(
            Decimal(report["amounts"]["open_difference"]),
            sum((Decimal(f["impact_amount"]) for f in findings
                 if f["impact_amount"] is not None), Decimal("0")),
            "findings on the same line are never summed into the difference")
        self.assertEqual(Decimal("25.00"),
                         sum(Decimal(item["amount"]) for item in
                             report["line_reconciliation"]
                             if item["included_in_invoice_total"]))
        self.assertTrue(report["findings_policy"]["findings_never_summed"])

    def test_missing_invoice_lines_are_unknown_not_zero(self):
        report = self.report(
            sources=[_source("usage", "usage-export.jsonl")],
            usage=USAGE)
        self.assertIsNone(report["amounts"]["invoice_total"])
        self.assertIsNone(report["amounts"]["open_difference"])
        self.assertEqual("not_comparable",
                         report["coverage"]["amount"]["status"])
        kinds = {item["kind"] for item in report["open_items"]}
        self.assertIn("missing_invoice_lines", kinds)

    def test_missing_currency_is_unknown_not_usd(self):
        bills = (BILLS_HEADER
                 + "ch-1,20.00,,1000,0.020,api-request,t1,2026-10-05,,\n")
        report = self.report(
            sources=[_source("bills", "bill-export.csv")], bills=bills)
        self.assertIsNone(report["amounts"]["invoice_total"])
        self.assertIsNone(report["amounts"]["open_difference"])
        self.assertEqual("unknown",
                         report["amounts"]["invoice_total_status"])
        self.assertEqual("not_comparable",
                         report["coverage"]["amount"]["status"])
        line = report["line_reconciliation"][0]
        self.assertEqual("excluded_currency_unknown", line["classification"])
        self.assertFalse(line["included_in_invoice_total"])
        kinds = {item["kind"] for item in report["open_items"]}
        self.assertIn("currency_unknown", kinds)

    def test_missing_unit_price_keeps_rule_subtotal_unknown(self):
        bills = (BILLS_HEADER
                 + "ch-1,20.00,USD,1000,,,t1,2026-10-05,,\n")
        report = self.report(
            sources=[_source("bills", "bill-export.csv")], bills=bills)
        self.assertIsNone(report["amounts"]["rule_repriced_subtotal"])
        self.assertEqual("partial",
                         report["amounts"]["rule_repricing_status"])
        self.assertIsNone(report["line_reconciliation"][0]["expected_amount"])

    def test_missing_charge_id_marks_invoice_partial(self):
        bills = (
            BILLS_HEADER
            + "ch-1,20.00,USD,1000,0.020,api-request,t1,2026-10-05,,\n"
            + ",5.00,USD,250,0.020,api-request,t2,2026-10-06,,\n"
        )
        report = self.report(
            sources=[_source("bills", "bill-export.csv")], bills=bills)
        self.assertEqual("20.00", report["amounts"]["invoice_total"])
        self.assertEqual("partial",
                         report["amounts"]["invoice_total_status"])
        kinds = {item["kind"] for item in report["open_items"]}
        self.assertIn("missing_charge_id", kinds)

    def test_v22_credit_tax_and_unapplied_refund_are_not_savings(self):
        bills = (
            BILLS_HEADER
            + "ch-1,20.00,USD,1000,0.020,api-request,t1,2026-10-05,,\n"
            + "tax-1,2.00,USD,,,,,2026-10-06,tax,applied\n"
            + "credit-1,-3.00,USD,,,,,2026-10-07,credit,applied\n"
            + "refund-1,-4.00,USD,,,,,2026-10-08,refund,issued\n"
        )
        report = self.report(
            sources=[_source("bills", "bill-export.csv"),
                     _source("usage", "usage-export.jsonl")],
            bills=bills,
            usage=USAGE)
        amounts = report["amounts"]
        self.assertEqual(Decimal("19.00"), Decimal(amounts["invoice_total"]))
        self.assertEqual(Decimal("-1.00"),
                         Decimal(amounts["known_adjustments"]))
        self.assertEqual(Decimal("2.00"), Decimal(amounts["tax_applied"]))
        self.assertEqual(Decimal("-3.00"), Decimal(amounts["credits_applied"]))
        self.assertEqual(Decimal("-4.00"), Decimal(amounts["credits_pending"]))
        self.assertEqual("0.00", amounts["realized_savings"])
        self.assertEqual([], report["realized_savings"]["claims"])
        refund = next(row for row in report["line_reconciliation"]
                      if row["charge_id"] == "refund-1")
        self.assertEqual("excluded_pending_adjustment", refund["classification"])
        self.assertFalse(refund["included_in_invoice_total"])
        self.assertEqual("unresolved", refund["state"])


class ReplayTests(CloseReportTestBase):
    """AC V23: every amount has line + rule lineage and a stable manifest."""

    def test_v23_report_artifacts_and_replay_fingerprint(self):
        self.make_case(
            sources=[_source("bills", "bill-export.csv"),
                     _source("usage", "usage-export.jsonl")],
            bills=self.full_bills(),
            usage=USAGE)
        calculation = close_report.calculate(self.case)
        self.assertEqual("verovo.close-calculation/1", calculation["schema"])
        report = close_report.build_report(self.case)
        for name in ("report.md", "report.json", "line-reconciliation.csv",
                     "review-queue.csv", "evidence-index.json", "manifest.json"):
            self.assertTrue((self.case / "report" / name).is_file(), name)
        manifest = json.loads((self.case / "report" / "manifest.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(64, len(manifest["output_snapshot_fingerprint"]))
        self.assertTrue(manifest["inputs"]["canonical_files"])
        pricing = next(rule for rule in manifest["rule_versions"]
                       if rule["rule_id"] == "api-request")
        self.assertEqual("2026-10", pricing["version"])
        self.assertEqual("0.020", pricing["unit_price"])
        self.assertIn("canonical_finance", manifest["algorithm_versions"])
        self.assertTrue(manifest["code_version"])
        self.assertIn("signoff", report)
        self.assertTrue(report["source_hashes"])
        self.assertTrue(report["rule_map_hash"].startswith("sha256:"))
        self.assertEqual(report["amounts"]["rule_repriced"],
                         report["amounts"]["rule_repriced_subtotal"])
        for line in report["line_reconciliation"]:
            self.assertTrue(line["line_ref"])
            self.assertTrue(line["basis"])
            Decimal(line["amount"])
            self.assertTrue(line["quantity"])
            self.assertTrue(line["unit_price"])
        self.assertEqual(Decimal(report["amounts"]["invoice_total"]),
                         sum(Decimal(line["amount"]) for line in
                             report["line_reconciliation"]
                             if line["included_in_invoice_total"]))
        first = manifest["output_snapshot_fingerprint"]
        close_report.calculate(self.case)
        second_report = close_report.build_report(self.case)
        second_manifest = json.loads((self.case / "report" / "manifest.json")
                                     .read_text(encoding="utf-8"))
        self.assertEqual(first, second_manifest["output_snapshot_fingerprint"])
        self.assertEqual(report["amounts"], second_report["amounts"])
        self.assertTrue(any("single currency" in item
                            for item in report["limitations"]))
        self.assertTrue(any("FX" in item for item in report["limitations"]))

    def test_v23_job_and_mapping_changes_are_stale(self):
        self.make_case(
            sources=[_source("bills", "bill-export.csv"),
                     _source("usage", "usage-export.jsonl")],
            bills=self.full_bills(),
            usage=USAGE)
        close_report.calculate(self.case)
        close_report.build_report(self.case)
        job_path = self.case / "period-job.json"
        original_job = job_path.read_bytes()
        job = json.loads(original_job.decode("utf-8"))
        job["currency"] = "EUR"
        job_path.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
        with self.assertRaises(CloseError) as ctx:
            close_report.build_report(self.case)
        self.assertEqual("STALE_SOURCE", ctx.exception.code)
        job_path.write_bytes(original_job)

        mapping_path = self.case / "intake" / "mapping-confirmed.json"
        original_mapping = mapping_path.read_bytes()
        mapping = json.loads(original_mapping.decode("utf-8"))
        mapping["confirmed_by"] = "late-change"
        mapping_path.write_text(json.dumps(mapping, indent=2) + "\n",
                                encoding="utf-8")
        with self.assertRaises(CloseError) as ctx:
            close_report.build_report(self.case)
        self.assertEqual("STALE_SOURCE", ctx.exception.code)
        mapping_path.write_bytes(original_mapping)

    def test_v23_tampered_calculation_is_stale(self):
        self.make_case(
            sources=[_source("bills", "bill-export.csv"),
                     _source("usage", "usage-export.jsonl")],
            bills=self.full_bills(),
            usage=USAGE)
        close_report.calculate(self.case)
        close_report.build_report(self.case)
        calc_path = self.case / "calculation.json"
        calc = json.loads(calc_path.read_text(encoding="utf-8"))
        calc["outcome"]["intake_report"]["reconciliation_rows"][0][
            "bill_amount"] = "25.00"
        calc_path.write_text(json.dumps(calc, indent=2) + "\n", encoding="utf-8")
        with self.assertRaises(CloseError) as ctx:
            close_report.build_report(self.case)
        self.assertEqual("STALE_SOURCE", ctx.exception.code)


class ReviewGateTests(CloseReportTestBase):
    def _run(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_review_requires_a_named_reviewer_and_never_closes_unresolved(self):
        self.make_case(
            sources=[_source("bills", "bill-export.csv"),
                     _source("usage", "usage-export.jsonl")],
            bills=self.full_bills(amount="25.00"),
            usage=USAGE)
        code, _ = self._run(["close", "calculate", str(self.case)])
        self.assertEqual(0, code)
        code, out = self._run(["close", "report", str(self.case)])
        self.assertEqual(0, code)
        self.assertIn("report.md", out)
        code, out = self._run(["close", "status", str(self.case)])
        self.assertEqual(0, code)
        status = json.loads(out)
        self.assertEqual("needs_review", status["c01_status"]["task_state"])
        self.assertEqual("REVIEW_REQUIRED",
                         status["c01_status"]["blocked_reason_code"])
        self.assertEqual("V-03",
                         status["c01_status"]["next_required_action"]["ticket"])
        with self.assertRaises(SystemExit) as ctx:
            self._run(["close", "review", str(self.case), "--scope", "finance review"])
        self.assertEqual(2, ctx.exception.code)
        self.assertFalse((self.case / "review.json").exists())

        code, out = self._run([
            "close", "review", str(self.case), "--by", "finance-reviewer",
            "--scope", "read report and reserve on credit",
            "--reservation", "credit note not applied",
            "--next-review", "2026-11-30"])
        self.assertEqual(0, code)
        self.assertIn("reviewed", out.lower())
        review = json.loads((self.case / "review.json").read_text(encoding="utf-8"))
        self.assertEqual("finance-reviewer", review["reviewer"])
        self.assertEqual("attested_by_declaration", review["attestation"])
        self.assertEqual("not_signed", review["signoff"]["state"])
        self.assertFalse(review["payment_authorization"])
        self.assertTrue(review["unresolved_items"])
        self.assertTrue(all(item["state"] != "closed"
                            for item in review["unresolved_items"]))
        refreshed = self.load_report()
        self.assertEqual("reviewed_not_signed", refreshed["review"]["state"])
        self.assertEqual("finance-reviewer", refreshed["review"]["by"])
        code, out = self._run(["close", "status", str(self.case)])
        self.assertEqual(0, code)
        status = json.loads(out)
        self.assertEqual("delivered", status["c01_status"]["task_state"])
        self.assertIsNone(status["c01_status"]["blocked_reason_code"])
        self.assertEqual("V-04",
                         status["c01_status"]["next_required_action"]["ticket"])
        self.assertEqual("r1", status["review_flow"]["signoff_revision"])
        self.assertEqual("not_signed", status["review_flow"]["signoff_state"])

    def test_report_requires_calculation_first(self):
        self.make_case(
            sources=[_source("bills", "bill-export.csv")],
            bills=self.full_bills())
        with self.assertRaises(CloseError) as ctx:
            close_report.build_report(self.case)
        self.assertEqual("INPUT_REQUIRED", ctx.exception.code)


class ReviewSignoffSemanticsTests(CloseReportTestBase):
    def test_review_and_signoff_labels_differ_with_relation_note(self):
        report = self.report(
            sources=[_source("bills", "bill-export.csv"),
                     _source("usage", "usage-export.jsonl")],
            bills=self.full_bills(amount="25.00"), usage=USAGE)
        text = close_report._report_markdown(report)
        self.assertIn("- 审阅状态：", text)
        self.assertIn("审阅状态只表示复核进度，不等于签核；"
                      "已签核也不等于所有差异已解决。", text)
        self.assertNotIn("- 签核状态：", text)

        signoff = close_revision.signoff(self.case, reviewer="controller",
                                         approved_scope="read report")
        self.assertEqual("signed_with_open_items", signoff["state"])
        delivered = (self.case / "report" / "report-delivered.md").read_text(
            encoding="utf-8")
        self.assertIn("- 审阅状态：", delivered)
        self.assertIn("- 签核状态：signed_with_open_items", delivered)
        self.assertIn("已签核不等于所有差异已解决（未闭合项 %d 条）"
                      % len(signoff["unresolved_items"]), delivered)


class MoneyLayersTests(CloseReportTestBase):
    def test_task_economics_includes_task_level_costs_in_the_final_report(self):
        report = self.report(
            sources=[_source("bills", "bill-export.csv"),
                     _source("usage", "usage-export.jsonl", "buyer"),
                     _source("outcomes", "outcomes.csv", "buyer")],
            bills=self.full_bills(), usage=USAGE,
            outcomes=OUTCOME_HEADER +
            "t1,o1,success,true,buyer-1,2026-10-31T00:00:00Z,outcomes.csv#o1,100.00,USD\n")
        tasks = report["task_economics"]["tasks"]
        self.assertEqual(1, len(tasks))
        self.assertEqual("t1", tasks[0]["task_id"])
        self.assertEqual("20.00", tasks[0]["fully_loaded_cost"])
        self.assertTrue(tasks[0]["verified_success"])
        text = close_report._report_markdown(report)
        self.assertIn("按任务归集", text)
        self.assertIn("| t1 |", text)
        self.assertIn("| Task |", text)

    def test_missing_outcome_value_is_not_reported_as_missing_outcomes_file(self):
        report = self.report(
            sources=[_source("bills", "bill-export.csv"),
                     _source("usage", "usage-export.jsonl", "buyer"),
                     _source("outcomes", "outcomes.csv", "buyer")],
            bills=self.full_bills(), usage=USAGE,
            outcomes=OUTCOME_HEADER +
            "t1,o1,success,true,buyer-1,2026-10-31T00:00:00Z,outcomes.csv#o1,,USD\n")
        self.assertTrue(report["task_economics"]["outcome_source_present"])
        text = close_report._report_markdown(report)
        self.assertNotIn("运营提供 outcomes 导出", text)
        self.assertIn("outcomes.value", text)

    def test_unresolved_amounts_are_visible_in_the_report_conclusion_area(self):
        report = self.report(
            sources=[_source("bills", "bill-export.csv"),
                     _source("usage", "usage-export.jsonl", "buyer"),
                     _source("outcomes", "outcomes.csv", "buyer")],
            bills=self.full_bills(amount="25.00"), usage=USAGE,
            outcomes=OUTCOME_HEADER +
            "t1,o1,success,true,buyer-1,2026-10-31T00:00:00Z,outcomes.csv#o1,,USD\n")
        text = close_report._report_markdown(report)
        conclusion = text.split("## 一、核验范围", 1)[0]
        self.assertIn("优先处置", conclusion)
        self.assertIn("5.00", conclusion)
        self.assertIn("与合同规则重算不一致", conclusion)

    def test_missing_economics_renders_five_layers_without_bare_dashes(self):
        report = self.report(
            sources=[_source("bills", "bill-export.csv")],
            bills=self.full_bills())
        self.assertEqual("not_applicable", report["task_economics"]["status"])
        text = close_report._report_markdown(report)
        for label in ("- 状态：", "- 已确认金额：", "- 未归属金额：",
                      "- 缺失记录：", "- 无法计算：", "- 需要补充："):
            self.assertIn(label, text)
        self.assertNotIn("- fully loaded cost：", text)
        self.assertNotIn("—\n—", text)
        self.assertIn("不是 0", text)
        self.assertIn("运营提供 outcomes 导出", text)


class OpenItemGroupingTests(CloseReportTestBase):
    def test_open_items_grouped_by_root_cause_keeping_details(self):
        bills = (
            BILLS_HEADER
            + "ch-1,20.00,USD,1000,0.020,api-request,t1,2026-10-05,,\n"
            + ",5.00,USD,250,0.020,api-request,t2,2026-10-06,,\n"
        )
        report = self.report(
            sources=[_source("bills", "bill-export.csv")], bills=bills)
        template = next(item for item in report["open_items"]
                        if item["kind"] == "missing_charge_id")
        items = []
        for index in range(1, 21):
            item = json.loads(json.dumps(template))
            item["id"] = "D-%d" % index
            item["line_ref"] = "bill-export.csv#row%d" % index
            items.append(item)
        groups = close_report._group_open_items(items)
        self.assertEqual(1, len(groups))
        group = groups[0]
        self.assertEqual("missing_charge_id", group["kind"])
        self.assertEqual(20, group["count"])
        self.assertEqual(["D-1", "D-2"], group["samples"])
        self.assertEqual("charge_id", group["missing_field"])

        report["open_items"] = items
        report["open_item_groups"] = groups
        text = close_report._report_markdown(report)
        self.assertIn("**根因分组（同一原因只处理一次）**", text)
        self.assertIn("**逐条明细**", text)
        self.assertIn("| %s | 20 |" % group["root_cause"], text)
        detail_ids = [line.split("|")[1].strip()
                      for line in text.splitlines() if line.startswith("| D-")]
        self.assertEqual(["D-%d" % n for n in range(1, 21)], detail_ids)


class OpenItemPriorityTests(unittest.TestCase):
    def report(self, items):
        return {"scope": {"currency": "USD"}, "amounts": {"open_difference": "0"},
                "open_items": items}

    def test_orders_by_amount_then_dedupes_by_line(self):
        report = self.report([
            {"id": "D-1", "kind": "rule_repriced_fail", "amount": "40.00",
             "currency": "USD", "line_ref": "L1", "state": "unresolved"},
            {"id": "D-2", "kind": "rule_repriced_fail", "amount": "100.00",
             "currency": "USD", "line_ref": "L1", "state": "unresolved"},
            {"id": "D-3", "kind": "currency_unknown", "amount": "50.00",
             "currency": "EUR", "line_ref": "L2", "state": "unresolved"},
            {"id": "D-4", "kind": "invoice_draft", "amount": None,
             "currency": None, "line_ref": "L3", "state": "unresolved"},
        ])
        priority = close_report._open_item_priority(report)
        self.assertEqual(["D-2", "D-1", "D-3", "D-4"],
                         [item["id"] for item in priority["items"]])
        self.assertEqual("100.00", priority["deduped"])
        self.assertEqual("140.00", priority["gross"])
        self.assertEqual(1, priority["unknown_currency"])
        summary = close_report._priority_summary(report)
        self.assertIn("去重后敞口 100.00 USD", summary)
        self.assertIn("毛额 140.00", summary)
        self.assertIn("另有 1 项", summary)

    def test_due_date_persistent_and_systemic_break_ties(self):
        report = self.report([
            {"id": "D-1", "kind": "rule_repriced_fail", "amount": "50.00",
             "currency": "USD", "line_ref": "L1", "state": "unresolved",
             "due_date": "2026-12-31", "confidence": "confirmed"},
            {"id": "D-2", "kind": "rule_repriced_fail", "amount": "50.00",
             "currency": "USD", "line_ref": "L2", "state": "unresolved",
             "due_date": "2026-12-01", "confidence": "low", "recurring": True},
            {"id": "D-3", "kind": "rule_repriced_fail", "amount": "50.00",
             "currency": "USD", "line_ref": "L3", "state": "unresolved",
             "due_date": "2026-12-15", "confidence": "inferred"},
            {"id": "D-4", "kind": "rule_repriced_fail", "amount": "50.00",
             "currency": "USD", "line_ref": "L4", "state": "unresolved",
             "due_date": "2026-12-20", "confidence": "inferred"},
        ])
        priority = close_report._open_item_priority(report)
        self.assertEqual("D-2", priority["items"][0]["id"])
        self.assertEqual("D-3", priority["items"][1]["id"])
        self.assertEqual("D-4", priority["items"][2]["id"])

    def test_no_open_items(self):
        self.assertEqual("> 优先处置：无未决项", close_report._priority_summary(
            self.report([])))


class BusinessOutputTests(CloseReportTestBase):
    def test_business_decision_precedes_verdict_codes_and_line_items(self):
        report = self.report(
            sources=[_source("bills", "bill-export.csv"),
                     _source("usage", "usage-export.jsonl", "buyer"),
                     _source("outcomes", "outcomes.csv", "buyer")],
            bills=self.full_bills(amount="25.00"), usage=USAGE,
            outcomes=OUTCOME_HEADER +
            "t1,o1,success,true,buyer-1,2026-10-31T00:00:00Z,outcomes.csv#o1,100.00,USD\n")
        text = close_report._report_markdown(report)

        main = text.split("## 附录：", 1)[0]
        self.assertIn("## 本期业务结论", main)
        self.assertIn("当前不建议直接付款或续约", main)
        self.assertIn("供应商账单 25.00 USD", main)
        self.assertIn("按已批准规则重算 20.00 USD；未解释差额 5.00 USD", main)
        self.assertIn("每个已验证成功任务成本", main)
        self.assertIn("怎么判断这笔支出是否划算", main)
        for token in (
            "finance_status", "UNPROVABLE", "PASS", "Task Economics",
            "This report", "D-1", "canonical_fingerprint", "逐行差异",
            "manifest.buyer", "charge_amount_mismatch", "line-level", "usage：",
        ):
            self.assertNotIn(token, main)
        self.assertIn("Rule Repriced（规则重算）", text)
        self.assertIn("## 五、逐行差异", text)


if __name__ == "__main__":
    unittest.main()
