"""V-02 acceptance tests (AC V07-V15, V30, V34), frozen red before implementation.

The close pipeline imports one real billing period (verovo.period-job/1),
proposes a field mapping for human confirmation and only then generates the
canonical bundle the existing Period Intake consumes. Original files are
never modified; nothing is booked from OCR drafts; no number is invented.
"""
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HEALTHCHECK_ROOT = REPO_ROOT / "healthcheck"

from buyer_ledger import cli
from buyer_ledger import close as close_mod
from buyer_ledger.close import (
    CloseError,
    ERROR_CODES,
    PERIOD_JOB_SCHEMA,
    build_case,
    confirm_mapping,
    ingest,
    init_case,
    load_period_job,
)

BILLS_CSV = (
    "Charge Reference,Total Amount,Currency,Quantity,Unit Price,Rate Plan,Task,Service Date\n"
    "ch-api-1,20.00,USD,1000,0.020,api-request,t1,2026-10-05\n"
)
USAGE_JSONL = (
    '{"record id":"u1","run":"r1","charge reference":"ch-api-1","total cost":"20.00",'
    '"currency":"USD","quantity":"1000","unit":"request","rate plan":"api-request",'
    '"financialized":true}\n'
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


def _job(**overrides):
    job = {
        "schema": PERIOD_JOB_SCHEMA,
        "job_id": "close-2026-10-a",
        "buyer": "buyer-1", "vendor": "vendor-1", "account": "acct-1",
        "buyer_signers": ["buyer-1", "roy", "controller", "finance-reviewer", "finance", "test-role"],
        "period": {"from": "2026-10-01", "to": "2026-11-01",
                   "to_exclusive": True, "timezone": "UTC"},
        "currency": "USD",
        "sources": [
            {"role": "bills", "path": "bill-export.csv", "control_domain": "vendor"},
            {"role": "usage", "path": "usage-export.jsonl", "control_domain": "vendor"},
        ],
        "rule_map": "contract-rules.json",
        "task_economics_requested": False,
        "approved_by": None,
    }
    for key, value in overrides.items():
        if key == "sources" and isinstance(value, dict):
            job["sources"] = [dict(entry) for entry in value.values()]
        else:
            job[key] = value
    return job


class CloseCaseTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.case = self.root / "close-2026-10"

    def write_bundle(self, *, job=None, bills=BILLS_CSV, usage=USAGE_JSONL,
                     invoice=None, rule_map=RULE_MAP):
        job = _job() if job is None else job
        (self.bundle / "period-job.json").write_text(
            json.dumps(job, indent=2) + "\n", encoding="utf-8")
        if bills is not None:
            (self.bundle / "bill-export.csv").write_text(bills, encoding="utf-8")
        if usage is not None:
            (self.bundle / "usage-export.jsonl").write_text(usage, encoding="utf-8")
        if invoice is not None:
            (self.bundle / "invoice-oct.txt").write_text(invoice, encoding="utf-8")
        if rule_map is not None:
            (self.bundle / "contract-rules.json").write_text(
                json.dumps(rule_map, indent=2) + "\n", encoding="utf-8")

    def full_flow(self, **kwargs):
        self.write_bundle(**kwargs)
        init_case(self.case, job_path=self.bundle / "period-job.json")
        ingest(self.case, bundle=self.bundle)
        return confirm_mapping(self.case, reviewer="finance-reviewer")


class PeriodJobContractTests(CloseCaseTestBase):
    def test_load_period_job_rejects_wrong_schema_and_fields(self):
        self.write_bundle()
        good = load_period_job(self.bundle / "period-job.json")
        self.assertEqual(PERIOD_JOB_SCHEMA, good["schema"])
        bad = _job(schema="verovo.period-job/2")
        for field in ("job_id", "vendor", "account", "currency"):
            broken = _job(**{field: ""})
            (self.bundle / "broken.json").write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaises(CloseError) as ctx:
                load_period_job(self.bundle / "broken.json")
            self.assertEqual("INPUT_REQUIRED", ctx.exception.code)
        (self.bundle / "bad-schema.json").write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaises(CloseError) as ctx:
            load_period_job(self.bundle / "bad-schema.json")
        self.assertEqual("UNSUPPORTED_SCOPE", ctx.exception.code)

    def test_closed_period_end_is_out_of_scope(self):
        job = _job(period={"from": "2026-10-01", "to": "2026-10-31",
                           "to_exclusive": False, "timezone": "UTC"})
        (self.bundle / "period-job.json").write_text(json.dumps(job), encoding="utf-8")
        with self.assertRaises(CloseError) as ctx:
            load_period_job(self.bundle / "period-job.json")
        self.assertIn("half-open", str(ctx.exception))

    def test_error_codes_are_c01_registered(self):
        self.assertEqual(
            ("INPUT_REQUIRED", "AUTH_REQUIRED", "REVIEW_REQUIRED", "UNSUPPORTED_SCOPE",
             "UNSUPPORTED_CAPABILITY", "POLICY_DENIED", "RATE_LIMITED",
             "STALE_SOURCE", "INDETERMINATE_EXTERNAL_ACTION", "ARTIFACT_PARTIAL",
             "INTERNAL_ERROR"),
            ERROR_CODES)
        err = CloseError("INPUT_REQUIRED", "x")
        self.assertTrue(issubclass(CloseError, ValueError))


class ImportValidationTests(CloseCaseTestBase):
    """AC V07/V08/V09/V14/V15: hostile or degenerate inputs fail closed."""

    def test_v07_non_utf8_file_is_a_clear_input_error(self):
        self.write_bundle()
        (self.bundle / "bill-export.csv").write_bytes(
            b"Charge Reference,Total Amount\n\xff\xfe\x00bad,1,00\n")
        init_case(self.case, job_path=self.bundle / "period-job.json")
        with self.assertRaises(CloseError) as ctx:
            ingest(self.case, bundle=self.bundle)
        self.assertEqual("INPUT_REQUIRED", ctx.exception.code)
        self.assertIn("UTF-8", str(ctx.exception))
        self.assertIn("bill-export.csv", str(ctx.exception))

    def test_v07_bom_csv_is_accepted_with_clean_headers(self):
        self.write_bundle(bills=BILLS_CSV.replace("Charge Reference", "\ufeffCharge Reference"))
        init_case(self.case, job_path=self.bundle / "period-job.json")
        result = ingest(self.case, bundle=self.bundle)
        draft = result["mapping_draft"]
        bills = draft["sources"]["bills"]
        self.assertEqual({"charge_id", "amount", "currency"},
                         set(bills["required_mapped"]))
        self.assertEqual([], bills["pending_fields"])

    def test_v07_empty_file_is_rejected(self):
        self.write_bundle(bills="")
        init_case(self.case, job_path=self.bundle / "period-job.json")
        with self.assertRaises(CloseError) as ctx:
            ingest(self.case, bundle=self.bundle)
        self.assertEqual("INPUT_REQUIRED", ctx.exception.code)
        self.assertIn("empty", str(ctx.exception))

    def test_v07_corrupt_rows_are_rejected_with_file_and_line(self):
        self.write_bundle(bills=BILLS_CSV + "ch-api-2,5.00,USD,250,0.020,api-request,t2\n")
        init_case(self.case, job_path=self.bundle / "period-job.json")
        with self.assertRaises(CloseError) as ctx:
            ingest(self.case, bundle=self.bundle)
        self.assertEqual("INPUT_REQUIRED", ctx.exception.code)
        self.assertIn("line 3", str(ctx.exception))
        self.write_bundle(usage=USAGE_JSONL + '{"record id": broken\n')
        with self.assertRaises(CloseError) as ctx:
            ingest(self.case, bundle=self.bundle)
        self.assertEqual("INPUT_REQUIRED", ctx.exception.code)
        self.assertIn("line 2", str(ctx.exception))

    def test_v08_currency_mix_refuses_merge(self):
        self.write_bundle(bills=BILLS_CSV + "ch-api-2,10.00,EUR,500,0.020,api-request,t2,2026-10-06\n")
        init_case(self.case, job_path=self.bundle / "period-job.json")
        result = ingest(self.case, bundle=self.bundle)
        validation = result["mapping_draft"]["validation"]
        self.assertTrue(validation["blocked"])
        self.assertEqual("UNSUPPORTED_SCOPE", validation["blocked_code"])
        self.assertEqual({"USD": 1, "EUR": 1},
                         {row["currency"]: row["rows"] for row in validation["currencies"]})
        with self.assertRaises(CloseError) as ctx:
            confirm_mapping(self.case, reviewer="finance")
        self.assertEqual("UNSUPPORTED_SCOPE", ctx.exception.code)
        self.assertFalse((self.case / "canonical").exists())

    def test_v09_duplicate_ids_are_flagged_not_double_counted(self):
        self.write_bundle(
            bills=BILLS_CSV + "ch-api-1,20.00,USD,1000,0.020,api-request,t1,2026-10-05\n",
            usage=USAGE_JSONL + USAGE_JSONL)
        init_case(self.case, job_path=self.bundle / "period-job.json")
        result = ingest(self.case, bundle=self.bundle)
        validation = result["mapping_draft"]["validation"]
        self.assertEqual(["ch-api-1"], [d["id"] for d in validation["duplicate_charge_ids"]])
        self.assertEqual(["u1"], [d["id"] for d in validation["duplicate_usage_ids"]])
        confirm_mapping(self.case, reviewer="finance")
        if str(HEALTHCHECK_ROOT) not in sys.path:
            sys.path.insert(0, str(HEALTHCHECK_ROOT))
        from verovo_healthcheck import canonical_finance
        outcome = canonical_finance.run(self.case / "canonical")
        report = outcome["intake_report"]
        self.assertEqual(1, len(report["duplicates"]))
        self.assertEqual("20.00", report["reconciliation"]["billed_subtotal"],
                         "the duplicated charge is booked once, not twice")
        from buyer_ledger import close_report
        close_report.calculate(self.case)
        close_report_payload = close_report.build_report(self.case)
        self.assertEqual("20.00",
                         close_report_payload["amounts"]["invoice_total"],
                         "the V-03 report must not re-count the duplicate charge")
        self.assertEqual(1, len(close_report_payload["line_reconciliation"]))
        self.assertEqual("partial",
                         close_report_payload["amounts"]["invoice_total_status"])

    def test_v14_path_traversal_is_denied(self):
        outside = self.root / "outside.csv"
        outside.write_text(BILLS_CSV, encoding="utf-8")
        job = _job(sources=[{"role": "bills", "path": "../outside.csv",
                             "control_domain": "vendor"},
                            {"role": "usage", "path": "usage-export.jsonl",
                             "control_domain": "vendor"}])
        self.write_bundle(job=job)
        init_case(self.case, job_path=self.bundle / "period-job.json")
        with self.assertRaises(CloseError) as ctx:
            ingest(self.case, bundle=self.bundle)
        self.assertEqual("POLICY_DENIED", ctx.exception.code)

    def test_v14_symlink_escape_is_denied(self):
        outside = self.root / "secret-usage.jsonl"
        outside.write_text(USAGE_JSONL, encoding="utf-8")
        (self.bundle / "link-usage.jsonl").symlink_to(outside)
        job = _job(sources=[{"role": "bills", "path": "bill-export.csv",
                             "control_domain": "vendor"},
                            {"role": "usage", "path": "link-usage.jsonl",
                             "control_domain": "vendor"}])
        self.write_bundle(job=job, usage=None)
        init_case(self.case, job_path=self.bundle / "period-job.json")
        with self.assertRaises(CloseError) as ctx:
            ingest(self.case, bundle=self.bundle)
        self.assertEqual("POLICY_DENIED", ctx.exception.code)

    def test_v14_windows_style_paths_are_denied(self):
        for index, relative in enumerate((r"C:\secret.csv", r"..\outside.csv")):
            case = self.root / ("win-case-%d" % index)
            # A literal file with that name inside the bundle must not make a
            # Windows-style path look relative and safe on POSIX.
            (self.bundle / relative).write_text(BILLS_CSV, encoding="utf-8")
            job = _job(sources=[{"role": "bills", "path": relative,
                                 "control_domain": "vendor"}])
            self.write_bundle(job=job, bills=None, usage=None)
            init_case(case, job_path=self.bundle / "period-job.json")
            with self.assertRaises(CloseError) as ctx:
                ingest(case, bundle=self.bundle)
            self.assertEqual("POLICY_DENIED", ctx.exception.code,
                             relative)

    def test_v15_reingest_is_idempotent_and_keeps_original_hash(self):
        self.write_bundle()
        init_case(self.case, job_path=self.bundle / "period-job.json")
        first = ingest(self.case, bundle=self.bundle)
        draft_path = self.case / "intake" / "mapping-draft.json"
        draft_first = draft_path.read_bytes()
        originals = {p.name: p.read_bytes()
                     for p in sorted(self.bundle.iterdir()) if p.is_file()}
        second = ingest(self.case, bundle=self.bundle)
        self.assertTrue(second["idempotent"])
        self.assertEqual(draft_first, draft_path.read_bytes(),
                         "the mapping draft is deterministic across re-ingest")
        scan = json.loads((self.case / "intake" / "scan.json").read_text(encoding="utf-8"))
        self.assertEqual(first["mapping_draft"]["source_hashes"],
                         second["mapping_draft"]["source_hashes"])
        self.assertEqual(originals,
                         {p.name: p.read_bytes()
                          for p in sorted(self.bundle.iterdir()) if p.is_file()},
                         "original files are never modified")
        registry = scan["sources"]["bills"][0]
        self.assertEqual(64, len(registry["sha256"]))

    def test_v15_changed_source_after_ingest_is_stale_and_hash_preserved(self):
        self.write_bundle()
        init_case(self.case, job_path=self.bundle / "period-job.json")
        ingest(self.case, bundle=self.bundle)
        scan = json.loads((self.case / "intake" / "scan.json").read_text(encoding="utf-8"))
        original_hash = scan["sources"]["bills"][0]["sha256"]
        (self.bundle / "bill-export.csv").write_text(
            BILLS_CSV + "ch-api-9,1.00,USD,50,0.020,api-request,t9,2026-10-07\n",
            encoding="utf-8")
        with self.assertRaises(CloseError) as ctx:
            ingest(self.case, bundle=self.bundle)
        self.assertEqual("STALE_SOURCE", ctx.exception.code)
        scan2 = json.loads((self.case / "intake" / "scan.json").read_text(encoding="utf-8"))
        self.assertEqual(original_hash, scan2["sources"]["bills"][0]["sha256"],
                         "the original hash stays on record after a stale re-ingest")


class MappingAndCanonicalTests(CloseCaseTestBase):
    """AC V11/V12/V13: human confirmation is the only road into canonical."""

    def test_v11_invoice_documents_stay_unreviewed_drafts(self):
        self.full_flow(invoice="INVOICE vendor-1 2026-10 TOTAL 20.00 USD",
                       job=_job(sources={
                           "bills": {"role": "bills", "path": "bill-export.csv",
                                     "control_domain": "vendor"},
                           "usage": {"role": "usage", "path": "usage-export.jsonl",
                                     "control_domain": "vendor"},
                           "invoice": {"role": "invoice", "path": "invoice-oct.txt",
                                       "control_domain": "vendor"}}))
        case = build_case(self.case)
        docs = case["sources"]["documents"]
        self.assertEqual(1, len(docs))
        self.assertEqual("unreviewed", docs[0]["review_status"])
        self.assertIn(docs[0]["document_ref"],
                      [item["ref"] for item in case["review_flow"]["review_queue"]])
        manifest = json.loads((self.case / "canonical" / "manifest.json")
                              .read_text(encoding="utf-8"))
        for entries in manifest["sources"].values():
            for entry in entries:
                self.assertNotIn("invoice-oct", entry["path"])
        canonical_ledger = "".join(
            path.read_text(encoding="utf-8")
            for path in (self.case / "canonical").iterdir() if path.suffix in (".csv", ".jsonl"))
        self.assertNotIn("INVOICE vendor-1", canonical_ledger,
                         "an OCR/invoice2data draft never reaches the ledger rows")

    def test_v11_booked_invoice_keeps_review_provenance_not_a_false_draft(self):
        self.full_flow(invoice="Vendor: Example\nInvoice No: INV-1\n",
                       job=_job(sources={
                           "bills": {"role": "bills", "path": "bill-export.csv",
                                     "control_domain": "vendor"},
                           "invoice": {"role": "invoice", "path": "invoice-oct.txt",
                                       "control_domain": "vendor"}}))
        booking = {"schema": "verovo.bill-booking/1", "candidate_id": "cand-0001",
                   "booking": {"booked_by": "li.na", "booked_at": "2026-11-20T04:47:46+00:00",
                               "attestation": "attested_by_declaration",
                               "export_path": "bill-export.csv",
                               "export_sha256": hashlib.sha256((self.bundle / "bill-export.csv").read_bytes()).hexdigest(),
                               "document_path": "invoice-oct.txt",
                               "document_sha256": hashlib.sha256((self.bundle / "invoice-oct.txt").read_bytes()).hexdigest()}}
        (self.bundle / "booking.json").write_text(json.dumps(booking), encoding="utf-8")
        ingest(self.case, bundle=self.bundle)
        case = build_case(self.case)
        document = case["sources"]["documents"][0]
        self.assertEqual("reviewed", document["review_status"])
        self.assertEqual("booked", document["booking_status"])
        self.assertEqual("li.na", document["booked_by"])
        self.assertNotIn("invoice_draft", {item["kind"] for item in case["review_flow"]["review_queue"]})
        booking["booking"]["export_sha256"] = "0" * 64
        (self.bundle / "booking.json").write_text(json.dumps(booking), encoding="utf-8")
        other = self.root / "tampered"
        init_case(other, job_path=self.bundle / "period-job.json")
        with self.assertRaises(CloseError):
            ingest(other, bundle=self.bundle)

    def test_v12_rule_validity_mismatch_refuses_repricing(self):
        stale_rules = json.loads(json.dumps(RULE_MAP))
        stale_rules["rules"][2]["valid_to"] = "2026-09-30"
        self.write_bundle(rule_map=stale_rules)
        init_case(self.case, job_path=self.bundle / "period-job.json")
        ingest(self.case, bundle=self.bundle)
        with self.assertRaises(CloseError) as ctx:
            confirm_mapping(self.case, reviewer="finance")
        self.assertEqual("UNSUPPORTED_SCOPE", ctx.exception.code)
        self.assertIn("api-request", str(ctx.exception))
        self.assertIn("valid", str(ctx.exception))
        self.assertFalse((self.case / "canonical").exists(),
                         "repricing must not run on an expired price table")

    def test_v13_unconfirmed_mapping_blocks_canonical_and_finance(self):
        self.write_bundle()
        init_case(self.case, job_path=self.bundle / "period-job.json")
        ingest(self.case, bundle=self.bundle)
        self.assertFalse((self.case / "canonical").exists())
        case = build_case(self.case)
        self.assertEqual("blocked", case["computed_facts"]["completion_axes"]["finance"])
        self.assertEqual("REVIEW_REQUIRED", case["c01_status"]["blocked_reason_code"])
        self.assertEqual("needs_review", case["c01_status"]["task_state"])
        self.assertIn("mapping", case["c01_status"]["next_required_action"])

    def test_v13_confirmed_mapping_generates_consumable_canonical(self):
        self.full_flow()
        manifest = json.loads((self.case / "canonical" / "manifest.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual("close-2026-10-a", manifest["case_id"])
        self.assertEqual("USD", manifest["currency"])
        self.assertEqual({"bills", "usage"}, set(manifest["sources"]))
        bills_entry = manifest["sources"]["bills"][0]
        self.assertEqual("vendor", bills_entry["control_domain"])
        self.assertEqual("intake-bills", bills_entry["rule_id"])
        if str(HEALTHCHECK_ROOT) not in sys.path:
            sys.path.insert(0, str(HEALTHCHECK_ROOT))
        from verovo_healthcheck import canonical_finance
        outcome = canonical_finance.run(self.case / "canonical")
        self.assertEqual("complete", outcome["finance_status"])
        buyer = outcome["evidence_conclusions"]["Buyer Verified"][0]
        self.assertEqual("UNPROVABLE", buyer["status"])

    def test_pending_control_domain_requires_explicit_resolution(self):
        self.write_bundle(job=_job(sources={
            "bills": {"role": "bills", "path": "bill-export.csv",
                      "control_domain": "buyer_or_vendor_pending_review"},
            "usage": {"role": "usage", "path": "usage-export.jsonl",
                      "control_domain": "vendor"}}))
        init_case(self.case, job_path=self.bundle / "period-job.json")
        ingest(self.case, bundle=self.bundle)
        with self.assertRaises(CloseError) as ctx:
            confirm_mapping(self.case, reviewer="finance")
        self.assertEqual("REVIEW_REQUIRED", ctx.exception.code)
        confirm_mapping(self.case, reviewer="finance",
                        control_domains={"bills": "vendor"})
        manifest = json.loads((self.case / "canonical" / "manifest.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual("vendor", manifest["sources"]["bills"][0]["control_domain"])

    def test_confirm_refuses_when_draft_changed_since_review(self):
        self.write_bundle()
        init_case(self.case, job_path=self.bundle / "period-job.json")
        ingest(self.case, bundle=self.bundle)
        (self.bundle / "bill-export.csv").write_text(
            BILLS_CSV + "ch-api-2,5.00,USD,250,0.020,api-request,t2,2026-10-06\n",
            encoding="utf-8")
        with self.assertRaises(CloseError) as ctx:
            ingest(self.case, bundle=self.bundle)
        self.assertEqual("STALE_SOURCE", ctx.exception.code)

    def test_second_confirmation_is_refused_until_v04(self):
        self.full_flow()
        confirmation = json.loads((self.case / "intake" / "mapping-confirmed.json")
                                  .read_text(encoding="utf-8"))
        self.assertEqual("confirmed", confirmation["state"])
        self.assertEqual("finance-reviewer", confirmation["confirmed_by"])
        self.assertEqual("attested_by_declaration", confirmation["attestation"])
        with self.assertRaises(CloseError) as ctx:
            confirm_mapping(self.case, reviewer="someone-else")
        self.assertEqual("REVIEW_REQUIRED", ctx.exception.code)

    def test_period_boundary_rows_are_flagged_half_open(self):
        self.write_bundle(
            bills=(BILLS_CSV
                   + "ch-edge-a,1.00,USD,50,0.020,api-request,t1,2026-11-01\n"
                   + "ch-edge-b,2.00,USD,100,0.020,api-request,t1,2026-09-30\n"))
        init_case(self.case, job_path=self.bundle / "period-job.json")
        result = ingest(self.case, bundle=self.bundle)
        outliers = result["mapping_draft"]["validation"]["period_outliers"]
        self.assertEqual({"ch-edge-a", "ch-edge-b"}, {row["id"] for row in outliers})
        reason = next(row for row in outliers if row["id"] == "ch-edge-a")["reason"]
        self.assertIn("half-open", reason)
        # Flagging is not enough: the out-of-period rows must not be booked.
        from buyer_ledger import close_report
        confirm_mapping(self.case, reviewer="finance")
        close_report.calculate(self.case)
        report = close_report.build_report(self.case)
        self.assertEqual("20.00", report["amounts"]["invoice_total"])
        self.assertEqual("partial", report["amounts"]["invoice_total_status"])
        self.assertEqual(1, len(report["line_reconciliation"]))
        queue_text = " ".join(
            "%s %s" % (item.get("kind"), item.get("detail"))
            for item in report["open_items"])
        self.assertIn("period_outlier", queue_text)
        self.assertIn("ch-edge-a", queue_text)
        self.assertIn("ch-edge-b", queue_text)

    def test_coverage_diagnostics_distinguish_four_categories(self):
        shared = (
            "Charge Reference,Total Amount,Currency,Quantity,Rate Plan,Task,Account,Service Date\n"
            "ch-api-1,20.00,USD,1000,api-request,t1,acct-1,2026-10-05\n"
            "ch-api-2,10.00,USD,500,api-request,t2,acct-1,2026-10-06\n"
            "ch-api-3,5.00,USD,250,api-request,,acct-1,2026-10-07\n"
            "ch-api-4,2.00,USD,100,ghost-driver,,acct-1,2026-10-08\n"
        )
        self.write_bundle(bills=shared, usage=None, job=_job(sources={
            "bills": {"role": "bills", "path": "bill-export.csv",
                      "control_domain": "vendor"}}))
        init_case(self.case, job_path=self.bundle / "period-job.json")
        result = ingest(self.case, bundle=self.bundle)
        diag = result["mapping_draft"]["coverage_diagnostics"]
        self.assertEqual(
            {"missing_task_id", "shared_account", "unapproved_allocation",
             "missing_real_cost"}, set(diag))
        self.assertEqual(2, diag["missing_task_id"]["count"])
        self.assertIn("acct-1", diag["shared_account"]["examples"])
        self.assertEqual(["ghost-driver"], diag["unapproved_allocation"]["referenced_rules"])
        self.assertEqual("no independent cost or usage record is ingested",
                         diag["missing_real_cost"]["detail"])


class BuyerCloseCaseTests(CloseCaseTestBase):
    """AC V10/V30/V34 and the read-only BuyerCloseCase projection."""

    def test_v10_vendor_only_exports_never_become_buyer_verified(self):
        self.full_flow()
        case = build_case(self.case)
        check = case["computed_facts"]["source_check"]
        self.assertEqual("UNPROVABLE", check["Buyer Verified"]["status"])
        self.assertIn("vendor", check["Buyer Verified"]["reason"])
        self.assertEqual({"bills", "usage"},
                         set(case["sources"]["collection_control_domains"]["vendor"]))

    def test_v30_minimal_job_returns_missing_list_without_numbers(self):
        self.write_bundle(
            bills=None, usage=None, rule_map=None,
            job=_job(sources={"invoice": {"role": "invoice", "path": "invoice-oct.txt",
                                          "control_domain": "vendor"}},
                     rule_map=None))
        (self.bundle / "invoice-oct.txt").write_text(
            "INVOICE vendor-1 2026-10 TOTAL 20.00 USD", encoding="utf-8")
        init_case(self.case, job_path=self.bundle / "period-job.json")
        ingest(self.case, bundle=self.bundle)
        case = build_case(self.case)
        missing = case["missing_inputs"]
        labels = " ".join(item["item"] for item in missing)
        self.assertIn("independent", labels)
        self.assertIn("rule map", labels)
        self.assertIn("field mapping confirmation", labels)
        for item in missing:
            for key, value in item.items():
                self.assertNotIsInstance(value, (int, float),
                                         "the missing list describes gaps, never amounts")
        self.assertEqual("needs_input", case["c01_status"]["task_state"])
        self.assertEqual("INPUT_REQUIRED", case["c01_status"]["blocked_reason_code"])

    def test_v34_single_invoice_keeps_invoice_reconciled_unprovable(self):
        self.write_bundle(
            bills=None, usage=None,
            job=_job(sources={"invoice": {"role": "invoice", "path": "invoice-oct.txt",
                                          "control_domain": "vendor"}}))
        (self.bundle / "invoice-oct.txt").write_text(
            "INVOICE vendor-1 2026-10 TOTAL 20.00 USD", encoding="utf-8")
        init_case(self.case, job_path=self.bundle / "period-job.json")
        result = ingest(self.case, bundle=self.bundle)
        self.assertEqual("partial", result["mapping_draft"]["source_check_status"])
        case = build_case(self.case)
        check = case["computed_facts"]["source_check"]
        self.assertEqual("UNPROVABLE", check["Invoice Reconciled"]["status"])
        self.assertNotEqual("PASS", check["Invoice Reconciled"]["status"])

    def test_projection_is_read_only(self):
        self.full_flow()
        def snapshot():
            return {p.relative_to(self.case).as_posix(): p.read_bytes()
                    for p in sorted(self.case.rglob("*")) if p.is_file()}
        before = snapshot()
        build_case(self.case)
        build_case(self.case)
        self.assertEqual(before, snapshot())

    def test_reusable_rules_require_fresh_validity_next_period(self):
        self.full_flow()
        case = build_case(self.case)
        reusable = case["history"]["next_period_reusable_rules"]
        self.assertEqual(["api-request"], [rule["rule_id"] for rule in reusable])
        self.assertTrue(reusable[0]["requires_re_review_next_period"])


class CloseCliTests(CloseCaseTestBase):
    def _run(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_init_ingest_mapping_flow(self):
        self.write_bundle()
        code, out = self._run([
            "close", "init", str(self.case), "--from-job",
            str(self.bundle / "period-job.json")])
        self.assertEqual(0, code)
        code, out = self._run(["close", "ingest", str(self.case),
                               "--bundle", str(self.bundle)])
        self.assertEqual(0, code)
        self.assertIn("mapping-draft.json", out)
        code, out = self._run(["close", "mapping", str(self.case)])
        self.assertEqual(0, code)
        self.assertIn("review", out.lower())
        code, out = self._run(["close", "mapping", str(self.case), "--confirm",
                               "--review-by", "finance"])
        self.assertEqual(0, code)
        self.assertIn("confirmed", out)
        code, out = self._run(["close", "status", str(self.case)])
        self.assertEqual(0, code)
        payload = json.loads(out)
        self.assertEqual("ready", payload["c01_status"]["task_state"])
        self.assertEqual("V-03", payload["c01_status"]["next_required_action"]["ticket"])
        manifest = json.loads((self.case / "canonical" / "manifest.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual("close-2026-10-a", manifest["case_id"])

    def test_mapping_preview_names_open_items_and_required_flags(self):
        # 账单少 charge_id、usage 控制域未裁决：预览必须点名缺口，并给出可跑的确认命令
        bills = ("Description,Amount,Currency,Quantity,Unit Price,Rate Plan,Task,"
                 "Service Date\nch-x,20.00,USD,1000,0.020,api-request,t1,2026-10-05\n")
        self.write_bundle(bills=bills, job=_job(sources={
            "bills": {"role": "bills", "path": "bill-export.csv",
                      "control_domain": "vendor"},
            "usage": {"role": "usage", "path": "usage-export.jsonl",
                      "control_domain": "buyer_or_vendor_pending_review"}}))
        init_case(self.case, job_path=self.bundle / "period-job.json")
        ingest(self.case, bundle=self.bundle)
        code, out = self._run(["close", "mapping", str(self.case)])
        self.assertEqual(0, code)
        self.assertIn("bill-export.csv / charge_id", out)
        self.assertIn("control domain pending for usage", out)
        self.assertIn("--control-domain usage=", out)

    def test_confirm_reports_the_gaps_it_approved(self):
        bills = ("Description,Amount,Currency,Quantity,Unit Price,Rate Plan,Task,"
                 "Service Date\nch-x,20.00,USD,1000,0.020,api-request,t1,2026-10-05\n")
        self.write_bundle(bills=bills)
        init_case(self.case, job_path=self.bundle / "period-job.json")
        ingest(self.case, bundle=self.bundle)
        code, out = self._run(["close", "mapping", str(self.case), "--confirm",
                               "--review-by", "finance"])
        self.assertEqual(0, code)
        self.assertIn("approved unmapped fields as gaps", out)
        self.assertIn("bills.charge_id", out)

    def test_v04_revision_commands_need_a_ready_period(self):
        # V-04 supersedes the V-02/V-03 revision stubs.  A period that has no
        # calculation/report is still refused before any revision or signoff.
        self.full_flow()
        for argv, ticket in (
            (["close", "revise", str(self.case), "--source", "x.csv"], "V-04"),
            (["close", "signoff", str(self.case), "--by", "c"], "V-04"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                self._run(argv)
            self.assertEqual(2, ctx.exception.code)

    def test_no_later_ticket_stubs_remain(self):
        from buyer_ledger.close import LATER_TICKET_COMMANDS
        self.assertEqual({}, LATER_TICKET_COMMANDS)


if __name__ == "__main__":
    unittest.main()
