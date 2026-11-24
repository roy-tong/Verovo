from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List

from .case_repository import CaseRepository
from .close import CloseError


def _validate_signoff_binding(report, calculation, signoff, revision) -> None:
    if signoff is None:
        if revision and revision.get("status") in {"signed", "signed_with_open_items"}:
            raise CloseError("STALE_SOURCE", "signed revision is missing its signoff record")
        return
    if report is None or calculation is None:
        raise CloseError("STALE_SOURCE", "a signed view needs its report and calculation")
    report_revision = report.get("revision_id") or "r1"
    signoff_revision = signoff.get("revision_id") or "r1"
    if report_revision != signoff_revision:
        raise CloseError(
            "STALE_SOURCE",
            "signoff revision %s does not match report revision %s"
            % (signoff_revision, report_revision),
        )
    report_fingerprint = (report.get("replay") or {}).get("output_snapshot_fingerprint")
    if not report_fingerprint or signoff.get("report_fingerprint") != report_fingerprint:
        raise CloseError("STALE_SOURCE", "signoff does not bind the current report fingerprint")
    if signoff.get("calculation_fingerprint") != calculation.get("calculation_fingerprint"):
        raise CloseError("STALE_SOURCE", "signoff does not bind the current calculation")
    canonical_fingerprint = (calculation.get("canonical_snapshot") or {}).get("fingerprint")
    if signoff.get("canonical_snapshot_fingerprint") != canonical_fingerprint:
        raise CloseError("STALE_SOURCE", "signoff does not bind the current canonical snapshot")
    if revision is not None:
        if revision.get("revision_id") != signoff_revision:
            raise CloseError("STALE_SOURCE", "signoff and revision metadata name different revisions")
        if revision.get("status") != signoff.get("state") or revision.get("signoff") != signoff:
            raise CloseError("STALE_SOURCE", "signoff and revision metadata are not one committed record")
    from . import close_revision
    recorded = signoff.get("record_fingerprint")
    if recorded:
        body = {key: value for key, value in signoff.items() if key != "record_fingerprint"}
        if recorded != close_revision._fingerprint(body):
            raise CloseError("STALE_SOURCE", "signoff record fingerprint is invalid")


class PeriodCase:
    def __init__(self, case_dir: Any):
        self.repository = CaseRepository(case_dir)

    @property
    def case_dir(self) -> Path:
        return self.repository.case_dir

    def project(self, *, allow_pending: bool = False) -> Dict[str, Any]:
        from . import close_revision

        case_root = (self.case_dir.parent.parent
                     if self.case_dir.parent.name == "revisions" else self.case_dir)

        def assert_committed() -> None:
            recovery = close_revision.recover(case_root)
            if recovery.get("issues"):
                raise CloseError(
                    "REVIEW_REQUIRED",
                    "close case has an unfinished revision transaction; run close status "
                    "or close recover before using the direct read-only projection",
                )

        if not allow_pending:
            assert_committed()
        self.repository.snapshot()
        payload = _project_case(self.repository)
        self.repository.assert_snapshot_current()
        if not allow_pending:
            assert_committed()
        return payload


def _project_case(repository: CaseRepository) -> Dict[str, Any]:
    from . import close as services

    _text = services._text
    ROLES = services.ROLES
    LEDGER_CHECK_ROLES = services.LEDGER_CHECK_ROLES
    BUYER_CLOSE_CASE_SCHEMA = services.BUYER_CLOSE_CASE_SCHEMA
    _iso_date = services._iso_date
    RESOLVED_CONTROL_DOMAINS = services.RESOLVED_CONTROL_DOMAINS
    _bundle_root = services._bundle_root
    _load_rule_map = services._load_rule_map
    CloseError = services.CloseError
    _latest_mtime = services._latest_mtime
    delegation_block = services.delegation_block

    case_dir = repository.case_dir
    job = services._validate_job(repository.read_json("job", missing_ok=False))
    scan_path = repository.path("scan")
    draft_path = repository.path("draft")
    confirmed_path = repository.path("confirmation")
    canonical_manifest = repository.path("canonical_manifest")
    calculation_path = repository.path("calculation")
    report_path = repository.path("report")
    review_path = repository.path("review")
    signoff_path = repository.path("signoff")
    revision_path = repository.path("revision")
    has_scan = repository.exists("scan")
    draft = repository.read_json("draft")
    confirmation = repository.read_json("confirmation")
    scan = repository.read_json("scan")
    calculation = repository.read_json("calculation")
    report = repository.read_json("report")
    review = repository.read_json("review")
    signoff = repository.read_json("signoff")
    revision = repository.read_json("revision")
    confirmed = confirmation is not None
    _validate_signoff_binding(report, calculation, signoff, revision)

    snapshot_refs: List[Dict[str, Any]] = []
    domains: Dict[str, List[str]] = {"vendor": [], "buyer": [], "pending": []}
    resolved = (confirmation or {}).get("sources", {})
    for entry in job["sources"]:
        role = entry["role"]
        domain = _text((resolved.get(role) or {}).get("control_domain")) \
            or _text(entry.get("control_domain"))
        bucket = domain if domain in ("vendor", "buyer") else "pending"
        if not ROLES[role]["document"]:
            domains[bucket].append(role)
        ref = {"role": role, "path": Path(entry["path"]).as_posix(),
               "control_domain": domain,
               "declared_sha256": _text(entry.get("sha256")) or None}
        snapshot_refs.append(ref)
    if scan:
        known = {row["path"]: row["sha256"]
                 for rows in scan.get("sources", {}).values() for row in rows}
        for ref in snapshot_refs:
            if ref["path"] in known:
                ref["sha256"] = known[ref["path"]]

    distinct_ledger = sum(1 for role in LEDGER_CHECK_ROLES if role in domains["vendor"]
                          or role in domains["buyer"] or role in domains["pending"])
    buyer_ledger = any(role in domains["buyer"] for role in ("bills", "usage"))
    vendor_ledger_count = sum(1 for role in ("bills", "usage") if role in domains["vendor"])

    # Source-check conclusions decidable at import stage. They are never PASS:
    # PASS/FAIL verdicts belong to the canonical run in V-03.
    source_check: Dict[str, Dict[str, str]] = {}
    if distinct_ledger < 2:
        source_check["Invoice Reconciled"] = {
            "status": "UNPROVABLE",
            "reason": ("fewer than two distinct ledger sources are ingested; "
                       "reconciliation needs a second source and is never PASS at "
                       "import stage")}
    else:
        source_check["Invoice Reconciled"] = {
            "status": "PENDING_V03",
            "reason": "two ledger sources are ingested; canonical reconciliation runs in V-03"}
    if not buyer_ledger:
        source_check["Buyer Verified"] = {
            "status": "UNPROVABLE",
            "reason": ("every ledger source is vendor-controlled (even when downloaded "
                       "by the buyer); repeated vendor exports never qualify as buyer "
                       "verification")}
    else:
        source_check["Buyer Verified"] = {
            "status": "PENDING_V03",
            "reason": "a buyer-controlled ledger source is declared; qualification runs in V-03"}
    source_check["Vendor Reconciled"] = (
        {"status": "PENDING_V03",
         "reason": "two vendor-controlled exports can be compared in V-03"}
        if vendor_ledger_count >= 2 else
        {"status": "UNPROVABLE",
         "reason": "two vendor-controlled exports are required for Vendor Reconciled"})
    if report is not None:
        # The published report owns the canonical verdicts now; keep the
        # import-stage source check from looking like it is still pending.
        for name in ("Invoice Reconciled", "Vendor Reconciled", "Buyer Verified"):
            verdict = (report.get("verdicts") or {}).get(name)
            if not verdict:
                continue
            rows = verdict.get("rows") or []
            source_check[name] = {
                "status": verdict.get("status") or "UNPROVABLE",
                "reason": (rows[0].get("reason") if rows else
                           "; ".join(verdict.get("limitations") or []) or
                           "see report verdict"),
            }

    axes: Dict[str, str] = {}
    axes["finance"] = "pending_calculation" if confirmed and distinct_ledger else "blocked"
    outcome_requested = job.get("task_economics_requested") or any(
        entry["role"] == "outcomes" for entry in job["sources"])
    for axis in ("verification", "delivery", "economics"):
        if not outcome_requested:
            axes[axis] = "not_applicable"
        elif confirmed:
            axes[axis] = "pending_calculation"
        else:
            axes[axis] = "blocked"
    completion_semantics = {
        "finance": ("blocked until a human confirms the mapping and a ledger pair "
                    "exists; pending_calculation means ready for the V-03 canonical "
                    "run"),
    }
    if report is not None:
        # A published report is the completed canonical run: show its four
        # independent axes instead of the pre-calculation placeholders.
        for axis in ("finance", "verification", "delivery", "economics"):
            axes[axis] = _text(report.get(axis + "_status")) or axes[axis]
            meaning = (report.get("axes_semantics") or {}).get(axis + "_status")
            if meaning:
                completion_semantics[axis] = meaning

    missing_inputs: List[Dict[str, str]] = []
    if distinct_ledger == 0:
        missing_inputs.append({
            "item": "an independent cost or usage record",
            "why": "an invoice alone cannot be reconciled; a second ledger source "
                   "is required"})
    if distinct_ledger and not buyer_ledger:
        missing_inputs.append({
            "item": "a buyer-controlled ledger source",
            "why": "Buyer Verified stays UNPROVABLE while all ledger sources are "
                   "vendor exports"})
    if not job.get("rule_map"):
        missing_inputs.append({
            "item": "an approved rule map (price table with currency, unit, "
                    "validity and version)",
            "why": "Rule Repriced needs approved per-unit pricing that covers the period"})
    if not confirmed:
        missing_inputs.append({
            "item": "field mapping confirmation by a human reviewer",
            "why": "nothing enters canonical before the mapping draft is confirmed"})
    pending_domains = sorted({entry["role"] for entry in job["sources"]
                              if _text(entry.get("control_domain"))
                              not in RESOLVED_CONTROL_DOMAINS})
    if pending_domains and not confirmed:
        missing_inputs.append({
            "item": "control-domain resolution for %s" % ", ".join(pending_domains),
            "why": "each source must be confirmed as vendor- or buyer-controlled"})
    if draft is not None and not confirmed:
        for item in draft.get("open_items", []):
            if item.get("kind") == "mapping_pending":
                missing_inputs.append({
                    "item": "a column for %s.%s" % (item.get("source"), item.get("field")),
                    "why": "the required field has no proposed column; it is never guessed"})
                break

    review_queue: List[Dict[str, Any]] = []
    if draft is not None:
        for item in draft.get("open_items", []):
            review_queue.append({
                "ref": "%s:%s" % (item.get("source", "rule-map"),
                                  item.get("kind", "open")),
                "kind": item.get("kind"), "detail": item.get("detail")})
    for document in (scan or {}).get("documents", []):
        if document.get("review_status") == "reviewed" and document.get("booking_status") == "booked":
            continue
        review_queue.append({"ref": document["document_ref"],
                             "kind": "invoice_document",
                             "detail": document.get("detail")})
    if report is not None:
        for item in report.get("open_items", []):
            review_queue.append({
                "ref": item.get("id"), "kind": item.get("kind"),
                "detail": item.get("detail"), "state": item.get("state"),
                "owner": item.get("owner")})

    # Cross-period reuse: only rules whose stated validity covers this period
    # may be proposed at all, and every one of them still requires fresh review
    # next period (a PASS last month never proves this month's price).
    reusable: List[Dict[str, Any]] = []
    rule_versions: List[Dict[str, Any]] = []
    start = _iso_date(job["period"]["from"], "period.from")
    end = _iso_date(job["period"]["to"], "period.to")
    last_day = end - timedelta(days=1)
    if scan is not None and job.get("rule_map"):
        try:
            bundle_root = _bundle_root(case_dir)
            rule_map = _load_rule_map(bundle_root, job)
        except CloseError:
            rule_map = None
        for rule in (rule_map or {}).get("rules", []):
            if _text(rule.get("kind")) == "pricing":
                rule_versions.append({"rule_id": rule.get("rule_id"),
                                      "version": rule.get("version")})
                valid_from = _text(rule.get("valid_from"))
                valid_to = _text(rule.get("valid_to"))
                if not valid_from or not valid_to:
                    continue
                if _iso_date(valid_from, "valid_from") <= start \
                        and _iso_date(valid_to, "valid_to") >= last_day:
                    reusable.append({
                        "rule_id": _text(rule.get("rule_id")),
                        "version": rule.get("version"),
                        "valid_from": valid_from, "valid_to": valid_to,
                        "requires_re_review_next_period": True})

    if report is not None:
        if signoff is not None:
            task_state, blocked_code = "delivered", None
            next_actor = "human_reviewer"
            next_required = {
                "action": "period signed for this revision; a new source must "
                          "create a new revision (V-04)",
                "ticket": "V-04"}
        elif review is not None:
            task_state, blocked_code = "delivered", None
            next_actor = "human_reviewer"
            next_required = {
                "action": "sign off the period or open a revision (V-04)",
                "ticket": "V-04"}
        else:
            task_state, blocked_code = "needs_review", "REVIEW_REQUIRED"
            next_actor = "human_reviewer"
            if revision is not None:
                next_required = {
                    "action": "review and sign off this revision",
                    "signoff": "close signoff %s --revision %s --by <reviewer> "
                               "--scope <scope>" % (case_dir.parent.parent,
                                                    revision.get("revision_id")),
                    "ticket": "V-04"}
            else:
                next_required = {
                    "action": "review the month-end report and record a named reviewer",
                    "report": "close review %s --by <reviewer> --scope <scope>"
                              % case_dir,
                    "ticket": "V-03"}
    elif calculation is not None:
        task_state, blocked_code = "ready", None
        next_actor = "verovo_cli"
        next_required = {"action": "build the month-end report (close report)",
                         "ticket": "V-03"}
    elif confirmed and distinct_ledger:
        task_state, blocked_code = "ready", None
        next_actor = "verovo_cli"
        next_required = {"action": "run the canonical calculation (close calculate)",
                         "ticket": "V-03"}
    elif draft is not None and draft.get("validation", {}).get("blocked"):
        task_state = "needs_input"
        blocked_code = draft["validation"].get("blocked_code") or "INPUT_REQUIRED"
        next_actor = "buyer_operator"
        next_required = {"action": "resolve the blocked validation (for example split "
                                   "the bundle by currency)", "ticket": "V-02"}
    elif has_scan and distinct_ledger == 0:
        task_state, blocked_code = "needs_input", "INPUT_REQUIRED"
        next_actor = "buyer_operator"
        next_required = {"action": "supply the missing inputs listed in missing_inputs",
                         "ticket": "V-02"}
    elif pending_domains and not confirmed:
        task_state, blocked_code = "needs_review", "REVIEW_REQUIRED"
        next_actor = "human_reviewer"
        next_required = {
            "action": "resolve source control domains, then confirm the mapping",
            "control_domains": "close mapping --control-domain role=vendor|buyer",
            "mapping": "close mapping --confirm --review-by <reviewer>",
            "ticket": "V-02"}
    elif not confirmed and has_scan:
        task_state, blocked_code = "needs_review", "REVIEW_REQUIRED"
        next_actor = "human_reviewer"
        next_required = {
            "action": "confirm the field mapping draft",
            "mapping": "close mapping --confirm --review-by <reviewer>",
            "ticket": "V-02"}
    elif has_scan:
        task_state, blocked_code = "needs_input", "INPUT_REQUIRED"
        next_actor = "buyer_operator"
        next_required = {"action": "supply the missing inputs listed in missing_inputs",
                         "ticket": "V-02"}
    else:
        task_state, blocked_code = "created", "INPUT_REQUIRED"
        next_actor = "buyer_operator"
        next_required = {"action": "ingest the period bundle (close ingest)",
                         "ticket": "V-02"}

    state_files = list(repository.state_files())
    return {
        "schema": BUYER_CLOSE_CASE_SCHEMA,
        "read_only": True,
        "close_case_id": job["job_id"],
        "identity": {
            "buyer_account_ref": {"buyer": job.get("buyer", ""),
                                  "account": job["account"]},
            "vendor_ref": job["vendor"],
            "period_range": dict(job["period"]),
            "currency": job["currency"],
        },
        "sources": {
            "source_snapshot_refs": snapshot_refs,
            "collection_control_domains": domains,
            "documents": (scan or {}).get("documents", []),
            "approved_rule_version": rule_versions,
            "mapping_revision": ("confirmed:%s"
                                 % confirmation.get("based_on_draft_sha256", "")[:12]
                                 if confirmed else "unconfirmed"),
        },
        "computed_facts": {
            "amount_basis": {
                "ledger_sources": distinct_ledger,
                "note": ("observed parsed rows only; canonical recalculation, "
                         "allocation and the five-amount reconciliation arrive in V-03"),
            },
            "difference_items": [],
            "explanation_basis_refs": [],
            "completion_axes": axes,
            "completion_axes_semantics": completion_semantics,
            "source_check": source_check,
        },
        "review_flow": {
            "review_queue": review_queue,
            "assigned_reviewer": ((signoff or {}).get("reviewer")
                                  or (review or {}).get("reviewer")
                                  or (confirmation or {}).get("confirmed_by")),
            "approved_scope": ((signoff or {}).get("approved_scope")
                               or (review or {}).get("approved_scope")
                               or (confirmation or {}).get("sources")),
            "outstanding_requests": missing_inputs,
            "signoff_revision": ((signoff or {}).get("revision_id")
                                 or ((review or {}).get("signoff") or {}).get("revision_id")),
            "signoff_state": ((signoff or {}).get("state")
                              or ((review or {}).get("signoff") or {}).get("state")),
        },
        "history": {
            "previous_period_ref": None,
            "supersedes_revision": None,
            "credits_pending": [],
            "credits_applied": [],
            "next_period_reusable_rules": reusable,
        },
        "missing_inputs": missing_inputs,
        "delegation": delegation_block(
            job_id=job.get("job_id") or case_dir.name,
            task_state=task_state, next_actor=next_actor,
            next_required=next_required, blocked_code=blocked_code,
            result_ref=("report/report.json"
                        if (case_dir / "report" / "report.json").is_file() else None),
            scope={"currency": job.get("currency"),
                   "period": job.get("period"),
                   "account": {"buyer": job.get("buyer", ""),
                               "account": job.get("account", "")}},
            recovery_entry=("buyer-ledger close recover <case> --apply"
                            if (case_dir / "revisions").is_dir() else None),
            open_items=len(missing_inputs or [])),
        "c01_status": {
            "task_state": task_state,
            "next_actor": next_actor,
            "next_required_action": next_required,
            "blocked_reason_code": blocked_code,
            "last_updated_at": _latest_mtime(state_files),
            "has_pending_external_effect": False,
            "artifact_refs": [path.relative_to(case_dir).as_posix()
                              for path in state_files if path.is_file()],
        },
        "boundary": ("read-only projection of the close case; it never writes back into "
                     "canonical structures and no verdict here authorizes payment"),
    }
