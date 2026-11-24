"""V-P0 page acceptance tests for the read-only Evidence Bundle page.

The page is an L0 read-only projection over an existing close case: it never
writes canonical data, never re-runs calculate/report, never approves, signs,
recovers, sends, or retries an external action.  Missing evidence stays
UNKNOWN/UNPROVABLE and is never folded into FALSE.
"""
import contextlib
import hashlib
import io
import json
import shutil
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from buyer_ledger import cli, close_bundle, close_report, close_revision
from buyer_ledger.close import confirm_mapping, init_case, ingest
from tests.test_close_report import (
    BILLS_HEADER,
    OUTCOME_HEADER,
    USAGE,
    CloseReportTestBase,
    _job,
    _source,
)

DELIVERY_FAIL = (
    OUTCOME_HEADER
    + "t1,o1,failure,true,buyer-1,2026-10-31T00:00:00Z,outcomes.csv#o1,,USD\n")
DELIVERY_VENDOR_ACCEPTED = (
    OUTCOME_HEADER
    + "t1,o1,success,true,vendor-1,2026-10-31T00:00:00Z,outcomes.csv#o1,,USD\n")
ECON_OUTCOMES = (
    OUTCOME_HEADER
    + "t1,o1,success,true,buyer-1,2026-10-31T00:00:00Z,outcomes.csv#o1,"
      "100.00,USD\n")
HISTORICAL_WARNING = "本页展示 r1，但当前最新 revision 是 r2；该页是历史只读快照。"


def _tree(root):
    root = Path(root)
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


class CloseBundleTestBase(CloseReportTestBase):
    def close_case(self, **kwargs):
        self.make_case(**kwargs)
        close_report.calculate(self.case)
        close_report.build_report(self.case)
        return self.case

    def build(self, revision=""):
        return close_bundle.build_bundle(self.case, revision)

    def axes(self, payload):
        return {item["axis"]: item["status"] for item in payload["axes"]}

    def verdicts(self, payload):
        return {item["name"]: item["status"] for item in payload["verdicts"]}

    def credit_bills(self):
        return (BILLS_HEADER
                + "ch-1,20.00,USD,1000,0.020,api-request,t1,2026-10-05,,\n"
                + "tax-1,2.00,USD,1,2.00,tax,,2026-10-05,tax,applied\n"
                + "credit-1,-3.00,USD,1,-3.00,credit,,2026-10-05,credit,applied\n"
                + "refund-1,-4.00,USD,1,-4.00,refund,,2026-10-05,refund,issued\n")

    def economics_case(self):
        sources = [
            _source("bills", "bill-export.csv"),
            _source("usage", "usage-export.jsonl", "buyer"),
            _source("tasks", "tasks.csv", "buyer"),
            _source("runs", "runs.jsonl", "buyer"),
            _source("outcomes", "outcomes.csv", "buyer"),
        ]
        self.write_bundle(sources=sources,
                          bills=self.full_bills(amount="25.00"),
                          usage=USAGE,
                          outcomes=ECON_OUTCOMES)
        (self.bundle / "tasks.csv").write_text("task_id,type\nt1,support\n",
                                               encoding="utf-8")
        (self.bundle / "runs.jsonl").write_text('{"run id":"r1","task":"t1"}\n',
                                                encoding="utf-8")
        job = _job(sources)
        job["task_economics_requested"] = True
        (self.bundle / "period-job.json").write_text(
            json.dumps(job, indent=2) + "\n", encoding="utf-8")
        init_case(self.case, job_path=self.bundle / "period-job.json")
        ingest(self.case, bundle=self.bundle)
        confirm_mapping(self.case, reviewer="finance-reviewer")
        close_report.calculate(self.case)
        close_report.build_report(self.case)
        return self.case


class BundleIdentityTests(CloseBundleTestBase):
    """V-P0-01..04: one page model, three independent columns, no re-summing."""

    def test_v_p0_01_shared_identity_is_read_only(self):
        self.close_case(sources=[_source("bills", "bill-export.csv"),
                                 _source("usage", "usage-export.jsonl", "buyer")],
                        bills=self.full_bills(amount="25.00"), usage=USAGE)
        payload = self.build()
        self.assertEqual("verovo.close-bundle/1", payload["schema"])
        self.assertEqual("buyer_ledger.close_bundle", payload["generator"])
        self.assertIs(True, payload["read_only"])
        self.assertEqual("close-2026-10-a", payload["bundle"]["case_id"])
        self.assertEqual("r1", payload["bundle"]["revision_id"])
        self.assertEqual("USD", payload["bundle"]["currency"])
        self.assertEqual("vendor-1", payload["bundle"]["scope"]["vendor"])
        self.assertEqual(4, len(payload["axes"]))
        self.assertIn("不写 canonical", payload["boundary"])
        self.assertIn("不自动审批", payload["boundary"])

    def test_v_p0_02_three_columns_stay_independent(self):
        self.close_case(sources=[_source("bills", "bill-export.csv"),
                                 _source("usage", "usage-export.jsonl", "buyer")],
                        bills=self.full_bills(amount="25.00"), usage=USAGE)
        payload = self.build()
        self.assertEqual("partial", payload["charges"]["state"])
        self.assertEqual("not_applicable", payload["delivery"]["state"])
        self.assertEqual("complete", payload["coverage"]["state"])
        self.assertEqual({
            "Rule Repriced": "FAIL",
            "Invoice Reconciled": "FAIL",
            "Vendor Reconciled": "UNPROVABLE",
            "Buyer Verified": "PASS",
            "Delivery Accepted": "not_applicable",
        }, self.verdicts(payload))
        self.assertEqual("partial", payload["states"]["finance"])
        # not_applicable is a scope answer, not a negative delivery verdict.
        self.assertEqual("not_applicable",
                         payload["delivery"]["verdict"]["status"])
        self.assertNotEqual("FAIL", payload["delivery"]["verdict"]["status"])
        self.assertIn("不是 FALSE", payload["delivery"]["gaps"][-1])

    def test_v_p0_03_finance_fail_keeps_difference(self):
        self.close_case(sources=[_source("bills", "bill-export.csv"),
                                 _source("usage", "usage-export.jsonl", "buyer")],
                        bills=self.full_bills(amount="25.00"), usage=USAGE)
        identity = self.build()["charges"]["identity"]
        self.assertEqual("25.00", identity["invoice_total"])
        self.assertEqual("20.00", identity["matched_postings"])
        self.assertEqual(Decimal("0"), Decimal(identity["known_adjustments"]))
        self.assertEqual("5.00", identity["open_difference"])
        self.assertIs(True, identity["holds"])

    def test_v_p0_04_two_findings_on_one_line_are_not_summed(self):
        self.close_case(sources=[_source("bills", "bill-export.csv"),
                                 _source("usage", "usage-export.jsonl", "buyer")],
                        bills=self.full_bills(amount="25.00"), usage=USAGE)
        charges = self.build()["charges"]
        self.assertEqual(2, charges["findings_count"])
        self.assertEqual(1, len(charges["lines"]))
        line_refs = {item["line_ref"] for item in charges["findings"]}
        self.assertEqual(1, len(line_refs))
        impacts = [Decimal(item["impact_amount"]) for item in charges["findings"]]
        self.assertEqual([Decimal("5.00"), Decimal("5.00")], impacts)
        self.assertNotEqual(Decimal(charges["lines"][0]["difference"]),
                            sum(impacts))
        self.assertIs(True, charges["findings_policy"]["findings_never_summed"])
        self.assertEqual("5.00", charges["identity"]["open_difference"])


class DeliveryAndDisputeTests(CloseBundleTestBase):
    """V-P0-05..07/17: delivery verdicts, credit exposure, dispute boundary."""

    def test_v_p0_05_delivery_fail_is_not_downgraded(self):
        self.close_case(sources=[_source("bills", "bill-export.csv"),
                                 _source("usage", "usage-export.jsonl", "buyer"),
                                 _source("outcomes", "outcomes.csv", "buyer")],
                        bills=self.full_bills(), usage=USAGE,
                        outcomes=DELIVERY_FAIL)
        payload = self.build()
        delivery = payload["delivery"]
        self.assertEqual("complete", delivery["state"])
        self.assertEqual("complete", delivery["delivery_status"])
        self.assertEqual("complete", delivery["verification_status"])
        self.assertEqual("FAIL", delivery["verdict"]["status"])
        row = delivery["rows"][0]
        self.assertEqual("FAIL", row["status"])
        self.assertEqual("t1", row["task_id"])
        self.assertEqual("declared_reference_not_independently_verified",
                         row["acceptance_basis"])

    def test_v_p0_06_vendor_control_domain_is_unprovable_not_false(self):
        self.close_case(sources=[_source("bills", "bill-export.csv"),
                                 _source("usage", "usage-export.jsonl", "buyer"),
                                 _source("outcomes", "outcomes.csv")],
                        bills=self.full_bills(), usage=USAGE,
                        outcomes=DELIVERY_VENDOR_ACCEPTED)
        delivery = self.build()["delivery"]
        self.assertEqual("partial", delivery["state"])
        self.assertEqual("partial", delivery["delivery_status"])
        self.assertEqual("UNPROVABLE", delivery["verdict"]["status"])
        row = delivery["rows"][0]
        self.assertEqual("UNPROVABLE", row["status"])
        self.assertEqual("same_vendor_control_domain", row["independence"])
        self.assertIn("vendor self-acceptance cannot support Buyer Verified",
                      row["reason"])
        self.assertIn("controlled buyer organization", row["reason"])
        self.assertTrue(any("UNKNOWN/UNPROVABLE" in gap
                            for gap in delivery["gaps"]))

    def test_v_p0_07_pending_credit_is_not_realized_savings(self):
        self.close_case(sources=[_source("bills", "bill-export.csv"),
                                 _source("usage", "usage-export.jsonl", "buyer")],
                        bills=self.credit_bills(), usage=USAGE)
        payload = self.build()
        credits = payload["renewal"]["credits"]
        self.assertEqual("-3.00", credits["applied"])
        self.assertEqual("-4.00", credits["pending"])
        self.assertEqual("0.00", payload["renewal"]["realized_savings"])
        refund = [line for line in payload["charges"]["lines"]
                  if line["charge_id"] == "refund-1"][0]
        self.assertEqual("excluded_pending_adjustment",
                         refund["classification"])
        self.assertTrue(any(item["kind"] == "credit_or_refund_not_applied"
                            for item in payload["charges"]["findings"]))
        identity = payload["charges"]["identity"]
        self.assertEqual("19.00", identity["invoice_total"])
        self.assertEqual("-1.00", identity["known_adjustments"])
        self.assertEqual(Decimal("0"), Decimal(identity["open_difference"]))

    def test_v_p0_17_disputes_do_not_execute_external_actions(self):
        self.close_case(sources=[_source("bills", "bill-export.csv"),
                                 _source("usage", "usage-export.jsonl", "buyer")],
                        bills=self.full_bills(amount="25.00"), usage=USAGE)
        disputes = self.build()["disputes"]
        self.assertEqual("ready", disputes["state"])
        self.assertIs(False, disputes["external_action_executed"])
        self.assertIs(False, disputes["payment_authorization"])
        self.assertEqual("declared_reference_not_independently_verified",
                         disputes["evidence_status"])
        self.assertEqual({"D-1", "D-2", "D-3", "D-4"},
                         {item["id"] for item in disputes["open_items"]})
        self.assertIn("不是对供应商多收的定性", disputes["note"])
        self.assertIn("没有外发给供应商", disputes["note"])


class CoverageAndEconomicsTests(CloseBundleTestBase):
    """V-P0-08..13: coverage ratios, allocation identity, no invented ROI."""

    def test_v_p0_08_record_and_amount_ratios_are_separate(self):
        self.close_case(sources=[_source("bills", "bill-export.csv"),
                                 _source("usage", "usage-export.jsonl", "buyer")],
                        bills=self.full_bills(amount="25.00"), usage=USAGE)
        coverage = self.build()["coverage"]
        self.assertEqual("complete", coverage["state"])
        self.assertEqual({"numerator": 1, "denominator": 1, "ratio": "1.0000"},
                         {key: coverage["record"][key]
                          for key in ("numerator", "denominator", "ratio")})
        self.assertEqual("20.00", coverage["amount"]["numerator"])
        self.assertEqual("25.00", coverage["amount"]["denominator"])
        self.assertEqual("0.8000", coverage["amount"]["ratio"])
        self.assertNotEqual(coverage["record"]["ratio"],
                            coverage["amount"]["ratio"])

    def test_v_p0_09_missing_currency_is_unknown_not_usd(self):
        self.close_case(
            sources=[_source("bills", "bill-export.csv")],
            bills=BILLS_HEADER
            + "ch-1,20.00,,1000,0.020,api-request,t1,2026-10-05,,\n")
        payload = self.build()
        identity = payload["charges"]["identity"]
        for key in ("invoice_total", "matched_postings", "known_adjustments",
                    "open_difference"):
            self.assertIsNone(identity[key])
        self.assertIsNone(identity["holds"])
        coverage = payload["coverage"]
        self.assertEqual("partial", coverage["state"])
        self.assertEqual("not_comparable", coverage["amount"]["status"])
        self.assertIn("no known period currency",
                      coverage["amount"]["reason"])
        line = payload["charges"]["lines"][0]
        self.assertEqual("excluded_currency_unknown", line["classification"])
        self.assertIs(True, line["currency_incomplete"])
        self.assertIs(False, line["included_in_invoice_total"])
        self.assertEqual("UNPROVABLE", line["invoice_reconciled"])
        kinds = {item["kind"] for item in payload["disputes"]["open_items"]}
        self.assertIn("currency_unknown", kinds)
        self.assertIn("missing_currency", kinds)

    def test_v_p0_10_allocation_identity_and_unallocated(self):
        self.economics_case()
        payload = self.build()
        self.assertEqual("partial", payload["economics"]["state"])
        self.assertEqual("unknown", payload["economics"]["cost_status"])
        allocation = payload["coverage"]["allocation_coverage"][0]
        self.assertEqual("USD", allocation["currency"])
        self.assertEqual("open", allocation["status"])
        self.assertEqual(2, allocation["open_items"])
        self.assertEqual("allocatable = allocated + unallocated + non_allocatable",
                         allocation["identity"])
        unallocated = payload["economics"]["unallocated"]
        self.assertEqual(1, len(unallocated))
        self.assertEqual("usage", unallocated[0]["kind"])
        self.assertEqual("20.00", unallocated[0]["amount"])

    def test_v_p0_11_roi_is_not_estimated(self):
        self.economics_case()
        economics = self.build()["economics"]
        self.assertIsNone(economics["roi"])
        self.assertIsNone(economics["roi_formula"])
        self.assertIsNone(economics["verified_business_value"])
        self.assertTrue(economics["roi_reason"])
        self.assertIn("cost is unknown or incomplete", economics["roi_reason"])

    def test_v_p0_12_cost_per_verified_success_is_independent(self):
        self.economics_case()
        economics = self.build()["economics"]
        self.assertIn("cost_per_verified_success", economics)
        self.assertIsNone(economics["cost_per_verified_success"])
        self.assertIn("open allocation/reconciliation items block the ratio",
                      economics["cost_per_verified_success_reason"])
        self.assertEqual(1, economics["verified_success_tasks"])
        self.assertEqual("complete", economics["success_denominator_status"])
        self.assertIsNone(economics["fully_loaded_cost"])

    def test_v_p0_13_request_coverage_is_not_computed(self):
        self.close_case(sources=[_source("bills", "bill-export.csv")],
                        bills=self.full_bills())
        request = self.build()["coverage"]["request_coverage"]
        self.assertEqual("not_computed", request["status"])
        self.assertIsNone(request["numerator"])
        self.assertIsNone(request["denominator"])
        self.assertIn("未计算请求覆盖率", request["reason"])


class PageStateTests(CloseBundleTestBase):
    """V-P0-14..20: boundaries, revisions, signoff, states, determinism."""

    def test_v_p0_14_limitations_keep_ocr_and_payment_boundary(self):
        self.close_case(sources=[_source("bills", "bill-export.csv")],
                        bills=self.full_bills())
        limitations = self.build()["charges"]["limitations"]
        self.assertTrue(any("unreviewed invoice/OCR" in item
                            for item in limitations))
        self.assertTrue(any("not a payment authorization" in item
                            for item in limitations))

    def test_v_p0_15_old_revision_is_historical_and_keeps_signoff(self):
        self.close_case(sources=[_source("bills", "bill-export.csv")],
                        bills=self.full_bills(amount="25.00"))
        close_revision.signoff(self.case, reviewer="finance-reviewer",
                               approved_scope="2026-10 charges")
        new_bills = self.root / "bills-v2.csv"
        new_bills.write_text(self.full_bills(amount="26.00"),
                             encoding="utf-8")
        close_revision.revise(self.case, source=new_bills, role="bills",
                              reason="vendor corrected invoice",
                              reviewer="finance-reviewer")

        current = self.build("r2")
        self.assertEqual("current", current["state"])
        self.assertEqual("r2", current["revisions"]["selected"])
        self.assertEqual("r2", current["revisions"]["latest"])
        self.assertIs(False, current["revisions"]["historical"])
        self.assertIs(False, current["renewal"]["signoff"]["present"])
        self.assertEqual("not_signed",
                         current["renewal"]["signoff"]["state"])
        self.assertEqual("26.00", current["charges"]["identity"]["invoice_total"])

        old = self.build("r1")
        self.assertEqual("historical", old["state"])
        self.assertIs(True, old["revisions"]["historical"])
        self.assertEqual("r1", old["bundle"]["revision_id"])
        self.assertEqual([HISTORICAL_WARNING], old["warnings"])
        self.assertIs(True, old["renewal"]["signoff"]["present"])
        self.assertEqual("signed_with_open_items",
                         old["renewal"]["signoff"]["state"])
        self.assertEqual("r1", old["renewal"]["current_revision"]["revision_id"])

    def test_v_p0_16_signoff_is_attestation_not_payment_authorization(self):
        self.close_case(sources=[_source("bills", "bill-export.csv"),
                                 _source("usage", "usage-export.jsonl", "buyer")],
                        bills=self.full_bills(amount="25.00"), usage=USAGE)
        close_revision.signoff(self.case, reviewer="finance-reviewer",
                               approved_scope="2026-10 charges",
                               reservations=["pending credit note"],
                               note="reviewed")
        payload = self.build()
        signoff = payload["renewal"]["signoff"]
        self.assertEqual("signed", payload["state"])
        self.assertEqual("signed_with_open_items", payload["states"]["signoff"])
        self.assertIs(True, signoff["present"])
        self.assertEqual("signoff.json", signoff["source"])
        self.assertEqual("attested_by_declaration", signoff["attestation"])
        self.assertIs(False, signoff["payment_authorization"])
        self.assertTrue(signoff["report_fingerprint"])
        self.assertEqual(4, len(signoff["unresolved_items"]))
        self.assertTrue(all(isinstance(item, dict)
                            for item in signoff["unresolved_items"]))
        self.assertEqual(4, len(payload["disputes"]["open_items"]))

    def test_x_p0_08_html_is_self_contained_and_escaped(self):
        self.close_case(sources=[_source("bills", "bill-export.csv"),
                                 _source("usage", "usage-export.jsonl", "buyer")],
                        bills=self.full_bills(amount="25.00"), usage=USAGE)
        injected = "</script><script>alert(1)</script>"
        close_revision.signoff(self.case, reviewer="finance-reviewer",
                               approved_scope="2026-10 charges",
                               reservations=[injected])
        before = _tree(self.case)
        html = close_bundle.render_html(self.build())
        self.assertNotIn("<script", html)
        self.assertNotIn("<link", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertIn("&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;",
                      html)
        self.assertNotIn(injected, html)
        self.assertEqual(before, _tree(self.case))

    def test_v_p0_19_projection_is_deterministic_and_read_only(self):
        self.economics_case()
        fixed = datetime(2026, 11, 19, 6, 0, 0, tzinfo=timezone.utc)
        before = _tree(self.case)
        first = close_bundle.build_bundle(self.case, now=fixed)
        second = close_bundle.build_bundle(self.case, now=fixed)
        self.assertEqual(
            json.dumps(first, sort_keys=True, ensure_ascii=False),
            json.dumps(second, sort_keys=True, ensure_ascii=False))
        self.assertTrue(first["generated_at"].startswith("2026-11-19T06:00:00"))
        self.assertEqual(before, _tree(self.case))

    def test_v_p0_20_empty_case_is_empty_not_zero(self):
        self.case.mkdir()
        payload = self.build()
        self.assertEqual("empty", payload["state"])
        self.assertEqual({"empty"}, set(self.axes(payload).values()))
        self.assertEqual({"not_applicable"},
                         set(self.verdicts(payload).values()))
        self.assertEqual([], payload["faults"])
        self.assertEqual([], list(self.case.iterdir()))
        for rel in ("report/report.json", "report/manifest.json",
                    "calculation.json", "period-job.json"):
            self.assertIn(rel, payload["missing"])
        self.assertNotIn("revision.json", payload["missing"])
        html = close_bundle.render_html(payload)
        self.assertIn("empty", html)


class SignoffBindingTests(CloseBundleTestBase):
    """A signoff only stands over the exact report/scope it was declared on."""

    def _signed_case(self):
        self.close_case(sources=[_source("bills", "bill-export.csv"),
                                 _source("usage", "usage-export.jsonl", "buyer")],
                        bills=self.full_bills(amount="25.00"), usage=USAGE)
        close_revision.signoff(self.case, reviewer="finance-reviewer",
                               approved_scope="2026-10 charges", note="reviewed")
        return self.case / "signoff.json"

    def _edit_signoff(self, path, **changes):
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(changes)
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    def _signoff_checks(self, payload):
        return {item["check"]: item["status"]
                for item in payload["bundle"]["freshness"]["checks"]
                if str(item["check"]).startswith("signoff.")}

    def test_tampered_signoff_fingerprint_is_not_accepted(self):
        path = self._signed_case()
        self._edit_signoff(path, report_fingerprint="sha256:deliberately-wrong",
                           reviewer="changed-after-signoff")
        payload = self.build()
        self.assertEqual("stale", payload["state"])
        self.assertEqual("stale", payload["states"]["signoff"])
        self.assertEqual("stale", payload["states"]["freshness"])
        self.assertEqual("mismatch", payload["bundle"]["signoff"]["binding"])
        checks = self._signoff_checks(payload)
        self.assertEqual("mismatch", checks["signoff.report_fingerprint"])
        self.assertEqual("mismatch", checks["signoff.record_fingerprint"])
        self.assertTrue(any("签核" in warning for warning in payload["warnings"]))

    def test_reviewer_edit_breaks_record_attestation(self):
        path = self._signed_case()
        self._edit_signoff(path, reviewer="changed-after-signoff")
        payload = self.build()
        checks = self._signoff_checks(payload)
        self.assertEqual("ok", checks["signoff.report_fingerprint"])
        self.assertEqual("mismatch", checks["signoff.record_fingerprint"])
        self.assertEqual("stale", payload["states"]["signoff"])
        self.assertEqual("stale", payload["state"])

    def test_unreadable_signoff_is_partial_and_fails_loudly(self):
        path = self._signed_case()
        path.write_text("{not-json", encoding="utf-8")
        payload = self.build()
        self.assertEqual("partial", payload["state"])
        self.assertEqual("unreadable", payload["states"]["signoff"])
        self.assertEqual("unverified", payload["states"]["freshness"])
        self.assertTrue(any(fault.get("artifact") == "signoff.json"
                            for fault in payload["faults"]))
        self.assertTrue(any("signoff.json" in warning
                            for warning in payload["warnings"]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                cli.main(["close", "bundle", str(self.case)])
        self.assertEqual(1, caught.exception.code)
        self.assertIn("partial", out.getvalue())


class BundleCliTests(CloseBundleTestBase):
    def test_cli_html_refuses_to_overwrite(self):
        self.close_case(sources=[_source("bills", "bill-export.csv")],
                        bills=self.full_bills())
        target = self.root / "bundle.html"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["close", "bundle", str(self.case),
                             "--json", "--html", str(target)])
        self.assertEqual(0, code)
        payload = json.loads(out.getvalue())
        self.assertEqual(str(target), payload["html_path"])
        self.assertTrue(target.is_file())
        body = target.read_text(encoding="utf-8")
        self.assertTrue(body.startswith("<!doctype html>"))

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                cli.main(["close", "bundle", str(self.case),
                          "--html", str(target)])
        self.assertEqual(2, caught.exception.code)
        refusal = json.loads(out.getvalue())
        self.assertEqual("OUTPUT_EXISTS", refusal["detail"])

    def test_cli_stale_state_exits_one(self):
        self.close_case(sources=[_source("bills", "bill-export.csv")],
                        bills=self.full_bills(amount="25.00"))
        payload = self.build()
        canonical = [item for item in payload["sources"]
                     if item["artifact"] == "canonical"]
        self.assertTrue(canonical)
        target = self.case / canonical[0]["path"]
        target.write_bytes(target.read_bytes() + b"\n")

        stale = self.build()
        self.assertEqual("stale", stale["state"])
        self.assertEqual("stale", stale["states"]["freshness"])
        self.assertTrue(stale["warnings"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                cli.main(["close", "bundle", str(self.case)])
        self.assertEqual(1, caught.exception.code)
        self.assertIn("stale", out.getvalue())

    def test_partial_case_does_not_fabricate_pass(self):
        self.close_case(sources=[_source("bills", "bill-export.csv")],
                        bills=self.full_bills())
        shutil.rmtree(self.case / "report")
        (self.case / "calculation.json").unlink()
        payload = self.build()
        self.assertEqual("partial", payload["state"])
        self.assertEqual({"partial"}, set(self.axes(payload).values()))
        self.assertNotIn("PASS", set(self.verdicts(payload).values()))
        self.assertNotIn("FAIL", set(self.verdicts(payload).values()))
        self.assertEqual([], payload["sources"])
        self.assertTrue(any("缺失结论不补造" in item
                            for item in payload["warnings"]))
        self.assertIn("report/report.json", payload["missing"])
        self.assertIn("calculation.json", payload["missing"])


class BusinessTerminalSummaryTests(unittest.TestCase):
    def test_terminal_summary_starts_with_the_buyer_decision(self):
        payload = {
            "bundle": {"case_id": "case-1", "revision_label": "r1",
                       "scope": {"buyer": "buyer-1", "vendor": "vendor-1",
                                 "account": "acct-1", "currency": "USD"}},
            "state": "ready", "state_label": "ready", "axes": [], "verdicts": [],
            "charges": {"identity": {"invoice_total": "25.00",
                                     "open_difference": "5.00", "holds": False},
                        "findings_count": 1},
            "delivery": {"verdict": {"status": "PASS"}, "rows": [{}]},
            "coverage": {},
            "economics": {"status": "complete", "cost_per_verified_success": "20.00",
                          "roi": "4.0"},
            "disputes": {"open_items": [{}], "external_action_executed": False,
                         "payment_authorization": False},
            "renewal": {"signoff": {}}, "warnings": [], "missing": [], "faults": [],
            "boundary": "test boundary",
        }
        text = close_bundle.render_summary(payload)

        main = text.split("--- 核验明细 ---", 1)[0]
        self.assertIn("Verovo 业务结论", main)
        self.assertIn("当前不建议直接付款或续约", main)
        self.assertIn("供应商账单 25.00 USD", main)
        self.assertIn("未解释差额 5.00 USD", main)
        self.assertIn("每个已验证成功任务成本 20.00 USD", main)
        for token in ("axes:", "verdicts:", "charges:", "delivery:", "PASS", "state:"):
            self.assertNotIn(token, main)
        self.assertIn("charges:", text)


if __name__ == "__main__":
    unittest.main()
