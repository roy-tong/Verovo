"""V-04 acceptance tests (AC V24-V29) for versioned close periods.

Each revision is an independent recalc in an append-only directory.  The tests
freeze the three delta categories that are easiest to conflate:

- amount / cause / completion-axis changes,
- newly available evidence (previously absent),
- credit applied (reduces amount due) versus credit merely issued (open
  exposure, never realized savings).

Frozen red before implementation.
"""
import contextlib
import io
import json
import shutil
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
from buyer_ledger.close import CloseError
from tests.test_close_report import (
    BILLS_HEADER,
    USAGE,
    CloseReportTestBase,
    _job,
    _rules,
    _source,
)


class RevisionTestBase(CloseReportTestBase):
    def base_case(self, bills=None):
        return self.report(
            sources=[_source("bills", "bill-export.csv"),
                     _source("usage", "usage-export.jsonl")],
            bills=self.full_bills() if bills is None else bills,
            usage=USAGE)

    def revised_bundle(self, bills, name="revised", usage=USAGE):
        bundle = self.root / name
        if bundle.exists():
            shutil.rmtree(bundle)
        bundle.mkdir()
        job = _job([
            _source("bills", "bill-export.csv"),
            _source("usage", "usage-export.jsonl"),
        ])
        (bundle / "period-job.json").write_text(
            json.dumps(job, indent=2) + "\n", encoding="utf-8")
        (bundle / "bill-export.csv").write_text(bills, encoding="utf-8")
        (bundle / "usage-export.jsonl").write_text(usage, encoding="utf-8")
        (bundle / "contract-rules.json").write_text(
            json.dumps(_rules(), indent=2) + "\n", encoding="utf-8")
        return bundle

    def revision_report(self, revision_id):
        return json.loads(
            (self.case / "revisions" / revision_id / "report" / "report.json")
            .read_text(encoding="utf-8"))

    def run_cli(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()


class RevisionIdempotencyTests(RevisionTestBase):
    """AC V24/V28: same input is stable; a changed source is not double counted."""

    def test_v24_same_input_is_idempotent(self):
        self.base_case()
        first = close_revision.revise(self.case, bundle=self.bundle,
                                      reviewer="finance")
        self.assertTrue(first["idempotent"])
        self.assertEqual("r1", first["revision"]["revision_id"])
        self.assertFalse((self.case / "revisions" / "r2").exists())
        second = close_revision.revise(self.case, bundle=self.bundle,
                                       reviewer="finance")
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["revision"]["input_snapshot_hash"],
                         second["revision"]["input_snapshot_hash"])
        self.assertEqual(64, len(first["revision"]["input_snapshot_hash"]))

    def test_v28_same_source_revision_is_not_double_counted(self):
        self.base_case()
        changed = self.revised_bundle(self.full_bills(amount="25.00"),
                                      name="changed")
        result = close_revision.revise(self.case, bundle=changed,
                                       reviewer="finance")
        revision = result["revision"]
        self.assertEqual("r2", revision["revision_id"])
        self.assertIn("bill-export.csv", revision["source_changed"])
        self.assertEqual(2, len(revision["source_snapshot"]))
        report = self.revision_report("r2")
        self.assertEqual(Decimal("25.00"),
                         Decimal(report["amounts"]["invoice_total"]))
        self.assertEqual(1, len(report["line_reconciliation"]))
        self.assertEqual(Decimal("25.00"),
                         sum(Decimal(line["amount"]) for line in
                             report["line_reconciliation"]))
        self.assertIn("bill-export.csv",
                      result["delta"]["evidence"]["changed_sources"])


class RevisionDeltaTests(RevisionTestBase):
    """AC V25/V26: amount deltas stay separate from credit application."""

    def _credit_bills(self, state):
        return (
            BILLS_HEADER
            + "ch-1,20.00,USD,1000,0.020,api-request,t1,2026-10-05,,\n"
            + "credit-1,-4.00,USD,,,,,2026-10-07,credit,%s\n" % state
        )

    def test_v25_bill_revision_keeps_both_versions_and_difference(self):
        self.base_case()
        r1_report = (self.case / "report" / "report.json").read_bytes()
        revised = self.revised_bundle(self.full_bills(amount="25.00"),
                                      name="v2")
        result = close_revision.revise(self.case, bundle=revised,
                                       reviewer="finance")
        self.assertEqual("r2", result["revision"]["revision_id"])
        delta = result["delta"]["amounts"]["invoice_total"]
        self.assertEqual("20.00", delta["before"])
        self.assertEqual("25.00", delta["after"])
        self.assertEqual("5.00", delta["delta"])
        self.assertEqual(r1_report,
                         (self.case / "revisions" / "r1" / "report" /
                          "report.json").read_bytes())
        self.assertEqual("20.00",
                         self.revision_report("r1")["amounts"]["invoice_total"])
        self.assertEqual("25.00",
                         self.revision_report("r2")["amounts"]["invoice_total"])
        self.assertEqual("5.00",
                         self.revision_report("r2")["amounts"]["open_difference"])

    def test_delta_distinguishes_new_evidence_from_amount_change(self):
        # r1 has a bill only: Invoice Reconciled is UNPROVABLE.  r2 adds a
        # distinct usage source; the amount is unchanged but new evidence
        # becomes available, which is its own delta category.
        self.report(sources=[_source("bills", "bill-export.csv")],
                    bills=self.full_bills())
        revised = self.revised_bundle(self.full_bills(), name="with-usage")
        result = close_revision.revise(
            self.case, bundle=revised, reviewer="finance",
            control_domains={"usage": "vendor"})
        self.assertEqual("r2", result["revision"]["revision_id"])
        self.assertIn("usage-export.jsonl",
                      result["revision"]["source_added"])
        delta = result["delta"]
        self.assertEqual(Decimal("0"),
                         Decimal(delta["amounts"]["invoice_total"]["delta"]))
        self.assertIn("usage-export.jsonl", delta["evidence"]["new_sources"])
        self.assertIn("Invoice Reconciled:PASS",
                      delta["evidence"]["newly_available"])
        self.assertTrue(delta["classification"]["evidence_added"])
        self.assertEqual("UNPROVABLE",
                         close_revision._read_json(
                             self.case / "revisions" / "r1" / "report" /
                             "report.json", "report")["verdicts"]
                         ["Invoice Reconciled"]["status"])

    def test_v26_credit_issued_vs_applied(self):
        self.base_case()
        issued = self.revised_bundle(self._credit_bills("issued"), name="issued")
        r2 = close_revision.revise(self.case, bundle=issued,
                                   reviewer="finance")
        report2 = self.revision_report("r2")
        self.assertEqual("20.00", report2["amounts"]["invoice_total"])
        self.assertEqual("-4.00", report2["amounts"]["credits_pending"])
        self.assertEqual("0", report2["amounts"]["credits_applied"])
        self.assertEqual("0.00", report2["amounts"]["realized_savings"])
        credits2 = r2["delta"]["credits"]
        self.assertEqual(Decimal("0"), Decimal(credits2["applied"]["delta"]))
        self.assertEqual(Decimal("0"), Decimal(credits2["realized"]["delta"]))
        self.assertTrue(credits2["issued_not_applied"])
        self.assertFalse(credits2["applied_this_revision"])
        self.assertEqual("0.00",
                         r2["delta"]["amounts"]["invoice_total"]["delta"])

        applied = self.revised_bundle(self._credit_bills("applied"),
                                      name="applied")
        r3 = close_revision.revise(self.case, bundle=applied,
                                   reviewer="finance")
        report3 = self.revision_report("r3")
        self.assertEqual("16.00", report3["amounts"]["invoice_total"])
        self.assertEqual("-4.00", report3["amounts"]["credits_applied"])
        self.assertEqual("0", report3["amounts"]["credits_pending"])
        self.assertEqual("0.00", report3["amounts"]["realized_savings"])
        credits3 = r3["delta"]["credits"]
        self.assertEqual(Decimal("-4.00"), Decimal(credits3["applied"]["delta"]))
        self.assertEqual(Decimal("0"), Decimal(credits3["realized"]["delta"]))
        self.assertTrue(credits3["applied_this_revision"])
        self.assertFalse(credits3["issued_not_applied"])


class SignoffAndRevisionTests(RevisionTestBase):
    """AC V27/V29 and the PeriodRevision contract."""

    def test_period_revision_has_the_frozen_fields(self):
        self.base_case()
        revised = self.revised_bundle(self.full_bills(amount="25.00"),
                                      name="v2")
        close_revision.revise(self.case, bundle=revised, reviewer="finance")
        record = close_revision.load_revision(self.case, "r2")
        for field in ("period_job_id", "revision_id", "parent_revision_id",
                      "input_snapshot_hash", "rule_map_hash", "source_added",
                      "source_removed", "reviewer", "closed_at", "status"):
            self.assertIn(field, record)
        self.assertEqual("r1", record["parent_revision_id"])
        self.assertIsNone(record["closed_at"])
        self.assertEqual("report_ready", record["status"])
        self.assertEqual(64, len(record["rule_map_hash"]))
        signed = close_revision.signoff(
            self.case, reviewer="controller", approved_scope="read v2")
        self.assertIn("signed", signed["state"])
        self.assertEqual(64, len(signed["scope_hash"]))
        self.assertTrue(signed["source_snapshot"])
        self.assertFalse(signed["payment_authorization"])
        updated = close_revision.load_revision(self.case, "r2")
        self.assertEqual(signed["state"], updated["status"])
        self.assertTrue(updated["closed_at"])
        self.assertEqual("controller", updated["reviewer"])

    def test_v27_signoff_does_not_cover_a_new_revision(self):
        self.base_case(bills=self.full_bills(amount="25.00"))
        signoff = close_revision.signoff(
            self.case, reviewer="controller", approved_scope="v1 accepted")
        root_report = (self.case / "report" / "report.json").read_bytes()
        root_signoff = (self.case / "signoff.json").read_bytes()
        # A signed root is immutable even before the first revision exists.
        # Late review/calculate/report must not rebuild the signed report.
        for operation, kwargs in (
                (close_report.record_review, {"reviewer": "late-reviewer"}),
                (close_report.calculate, {}),
                (close_report.build_report, {})):
            with self.assertRaises(CloseError) as ctx:
                operation(self.case, **kwargs)
            self.assertEqual("REVIEW_REQUIRED", ctx.exception.code)
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli(["close", "review", str(self.case),
                          "--by", "late-reviewer"])
        self.assertEqual(2, ctx.exception.code)
        self.assertEqual(root_report,
                         (self.case / "report" / "report.json").read_bytes())
        self.assertEqual(root_signoff,
                         (self.case / "signoff.json").read_bytes())
        revised = self.revised_bundle(self.full_bills(amount="30.00"),
                                      name="v2")
        result = close_revision.revise(self.case, bundle=revised,
                                       reviewer="finance")
        self.assertEqual("r2", result["revision"]["revision_id"])
        self.assertEqual(root_report,
                         (self.case / "revisions" / "r1" / "report" /
                          "report.json").read_bytes())
        self.assertEqual(root_signoff,
                         (self.case / "revisions" / "r1" /
                          "signoff.json").read_bytes())
        self.assertIn("signed",
                      close_revision.load_revision(self.case, "r1")["status"])
        self.assertEqual("report_ready",
                         close_revision.load_revision(self.case, "r2")["status"])
        self.assertFalse((self.case / "revisions" / "r2" / "signoff.json")
                         .exists())
        self.assertEqual("25.00",
                         self.revision_report("r1")["amounts"]["invoice_total"])
        self.assertEqual("30.00",
                         self.revision_report("r2")["amounts"]["invoice_total"])
        with self.assertRaises(CloseError) as ctx:
            close_revision.signoff(self.case, reviewer="controller",
                                   revision_id="r1")
        self.assertEqual("REVIEW_REQUIRED", ctx.exception.code)

    def test_v29_signoff_needs_a_reviewer_and_never_closes_unresolved(self):
        self.base_case(bills=self.full_bills(amount="25.00"))
        report_before = (self.case / "report" / "report.json").read_bytes()
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli(["close", "signoff", str(self.case),
                          "--scope", "controller review"])
        self.assertEqual(2, ctx.exception.code)
        self.assertFalse((self.case / "signoff.json").exists())

        signoff = close_revision.signoff(
            self.case, reviewer="controller", approved_scope="read report")
        self.assertEqual("signed_with_open_items", signoff["state"])
        self.assertFalse(signoff["payment_authorization"])
        self.assertTrue(signoff["unresolved_items"])
        report_after = json.loads(report_before.decode("utf-8"))
        self.assertTrue(all(item["state"] != "closed"
                            for item in report_after["open_items"]))
        self.assertEqual(report_before,
                         (self.case / "report" / "report.json").read_bytes())


class RevisionCliTests(RevisionTestBase):
    def test_revise_can_add_a_named_outcomes_source(self):
        self.base_case()
        outcomes = self.root / "outcomes.csv"
        outcomes.write_text(
            "task_id,outcome_id,status,verified,accepted_by,accepted_at,source_ref,value,currency\n"
            "t1,o1,success,true,Buyer Owner,2026-10-31T00:00:00Z,outcomes.csv#o1,100.00,USD\n",
            encoding="utf-8")
        result = close_revision.revise(
            self.case, source=outcomes, role="outcomes", reviewer="finance",
            reason="add buyer outcomes", control_domains={"outcomes": "buyer"})
        self.assertEqual("r2", result["revision"]["revision_id"])
        self.assertIn("outcomes.csv", result["revision"]["source_added"])
        job = json.loads((self.case / "revisions/r2/period-job.json").read_text(encoding="utf-8"))
        self.assertIn({"role": "outcomes", "path": "outcomes.csv"},
                      [{key: entry.get(key) for key in ("role", "path")} for entry in job["sources"]])
        report = self.revision_report("r2")
        self.assertNotEqual("not_applicable", report["task_economics"]["status"])
        self.assertTrue(report["task_economics"]["fully_loaded_cost"])

    def test_revise_bundle_rejects_unregistered_files_instead_of_ignoring_them(self):
        self.base_case()
        revised = self.revised_bundle(self.full_bills(), name="with-undeclared-outcomes")
        (revised / "outcomes.csv").write_text("task_id\n", encoding="utf-8")
        with self.assertRaises(CloseError) as caught:
            close_revision.revise(self.case, bundle=revised, reviewer="finance")
        self.assertIn("outcomes.csv", str(caught.exception))
        self.assertIn("未声明", str(caught.exception))

    def test_review_targets_the_named_revision(self):
        self.base_case()
        revised = self.revised_bundle(self.full_bills(amount="25.00"), name="v2-review")
        close_revision.revise(self.case, bundle=revised, reviewer="finance")
        code, out = self.run_cli([
            "close", "review", str(self.case), "--revision", "r2",
            "--by", "controller", "--scope", "review only r2 before signoff"])
        self.assertEqual(0, code, out)
        review = json.loads((self.case / "revisions/r2/review.json").read_text(encoding="utf-8"))
        self.assertEqual("r2", review["revision_id"])
        report = self.revision_report("r2")
        self.assertEqual("reviewed_not_signed", report["review"]["state"])
        self.assertEqual("r2", report["signoff"]["revision_id"])
        self.assertFalse((self.case / "review.json").exists())
        code, out = self.run_cli([
            "close", "signoff", str(self.case), "--revision", "r2",
            "--by", "controller", "--scope", "sign the reviewed r2"])
        self.assertEqual(0, code, out)
        self.assertEqual("r2", close_revision.load_revision(self.case, "r2")["signoff"]["revision_id"])

    def test_revise_and_signoff_cli_flow(self):
        self.base_case()
        revised = self.revised_bundle(self.full_bills(amount="25.00"),
                                      name="v2")
        code, out = self.run_cli([
            "close", "revise", str(self.case), "--bundle", str(revised),
            "--review-by", "finance", "--reason", "vendor revision"])
        self.assertEqual(0, code)
        self.assertIn("r2", out)
        code, out = self.run_cli(["close", "status", str(self.case)])
        self.assertEqual(0, code)
        status = json.loads(out)
        self.assertEqual("needs_review", status["c01_status"]["task_state"])
        self.assertEqual("V-04",
                         status["c01_status"]["next_required_action"]["ticket"])
        code, out = self.run_cli([
            "close", "signoff", str(self.case), "--revision", "r2",
            "--by", "controller", "--scope", "accept v2"])
        self.assertEqual(0, code)
        self.assertIn("signed", out.lower())
        code, out = self.run_cli(["close", "status", str(self.case)])
        self.assertEqual(0, code)
        status = json.loads(out)
        self.assertEqual("delivered", status["c01_status"]["task_state"])


if __name__ == "__main__":
    unittest.main()
