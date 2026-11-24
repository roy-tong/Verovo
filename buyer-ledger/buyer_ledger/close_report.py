"""V-03 buyer-close report: replayable month-end report over the V-02 bundle.

This module is a projection/orchestrator over the existing canonical chain
(``verovo_healthcheck.canonical_finance`` -> ``period_intake`` /
``task_economics``).  It deliberately does not implement a second financial
engine:

    canonical/  --calculate-->  calculation.json
                --report---->  report.json / report.md / line-reconciliation.csv
                               review-queue.csv / evidence-index.json / manifest.json
                --review---->  review.json (named reviewer, scope, reservations)

Every amount is a Decimal string.  The report keeps the four completion axes
(``finance_status`` / ``verification_status`` / ``delivery_status`` /
``economics_status``) separate from the PASS/FAIL/UNKNOWN/UNPROVABLE evidence
verdicts.  Line classification is mutually exclusive, so one charge can be
referenced by several findings without being counted twice.  Credits, tax and
refunds are never reported as realized savings.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import __version__
from .close import CloseError, load_period_job

REPORT_SCHEMA = "verovo.buyer-close-report/1"
CALCULATION_SCHEMA = "verovo.close-calculation/1"
REVIEW_SCHEMA = "verovo.close-review/1"
REPORT_MANIFEST_SCHEMA = "verovo.close-report-manifest/1"
EVIDENCE_SCHEMA = "verovo.close-evidence-index/1"
CODE_VERSION = "buyer-ledger/%s close-report/1" % __version__
ALGORITHM_VERSIONS = {
    "canonical_finance": "1.0",
    "period_intake": "1.0",
    "task_economics": "1.0",
}
AXES = ("finance_status", "verification_status", "delivery_status",
        "economics_status")
VERDICT_NAMES = ("Rule Repriced", "Invoice Reconciled", "Vendor Reconciled",
                 "Buyer Verified", "Delivery Accepted")
AXIS_LABELS_ZH = {
    "finance_status": "财务",
    "verification_status": "验证",
    "delivery_status": "交付",
    "economics_status": "经济",
}
VERDICT_LABELS_ZH = {
    "Rule Repriced": "规则重算",
    "Invoice Reconciled": "发票对账",
    "Vendor Reconciled": "供应商对账",
    "Buyer Verified": "买方独立核验",
    "Delivery Accepted": "交付验收",
}
ADJUSTMENT_TYPES = ("tax", "credit", "refund", "discount")
APPLIED_STATES = {"applied", "posted", "realized"}
PENDING_STATES = {"issued", "pending", "unapplied", "void", "unknown", ""}
UNRESOLVED_STATES = {"unresolved", "open", "pending", "needs_input",
                     "needs_review"}


# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------

def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _display(value: Any) -> str:
    return "—" if value in (None, "") else str(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _fingerprint(payload: Any) -> str:
    return _sha_bytes(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")).encode("utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    temp.replace(path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def _dec(value: Any, label: str) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise CloseError("INPUT_REQUIRED", "%s must be a decimal amount" % label)
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise CloseError("INPUT_REQUIRED",
                         "%s is not a decimal amount: %r" % (label, value)) from exc
    if not number.is_finite():
        raise CloseError("INPUT_REQUIRED", "%s is not finite: %r" % (label, value))
    return number


def _money(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    if value == 0:
        value = abs(value)
    text = format(value, "f")
    return text


def _ratio(numerator: Decimal, denominator: Decimal) -> Optional[str]:
    if denominator == 0:
        return None
    return format((numerator / denominator).quantize(Decimal("0.0001")),
                  "f")


def _csv_safe(value: Any, *, money: bool = False) -> str:
    """Prevent spreadsheet formula injection in external text cells.

    Numeric amount columns stay untouched so a real negative amount is not
    turned into text; text starting with a formula trigger is prefixed with an
    apostrophe.
    """
    text = _text(value)
    if money:
        return text
    if text and text[0] in ("=", "+", "@"):
        return "'" + text
    return text


def _read_json(path: Path, label: str) -> Dict[str, Any]:
    if not path.is_file():
        raise CloseError("INPUT_REQUIRED", "%s not found: %s" % (label, path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CloseError("INPUT_REQUIRED", "%s is not valid JSON: %s"
                         % (label, exc)) from exc
    if not isinstance(data, dict):
        raise CloseError("INPUT_REQUIRED", "%s must be a JSON object" % label)
    return data


def _canonical_dir(case_dir: Path) -> Path:
    path = Path(case_dir) / "canonical"
    if not (path / "manifest.json").is_file():
        raise CloseError("INPUT_REQUIRED",
                         "canonical bundle not found; run close mapping --confirm first (%s)"
                         % path)
    return path


def _canonical_snapshot(case_dir: Path) -> Dict[str, Any]:
    canonical = _canonical_dir(case_dir)
    files = []
    for path in sorted(canonical.rglob("*")):
        if path.is_file():
            files.append({"path": path.relative_to(canonical).as_posix(),
                          "sha256": _sha_file(path),
                          "bytes": path.stat().st_size})
    return {"files": files, "fingerprint": _fingerprint(files)}


def _ensure_unsigned(case_dir: Path) -> None:
    if (Path(case_dir) / "signoff.json").is_file():
        raise CloseError(
            "REVIEW_REQUIRED",
            "this period is already signed; report/calculation/review must not "
            "be rebuilt in place — create a new revision with close revise")


def _input_hashes(case_dir: Path) -> Dict[str, Any]:
    canonical = _canonical_dir(case_dir)
    return {
        "period_job_sha256": _sha_file(Path(case_dir) / "period-job.json"),
        "mapping_confirmed_sha256": _sha_file(
            Path(case_dir) / "intake" / "mapping-confirmed.json"),
        "rule_map_sha256": _sha_file(canonical / "rule-map.json"),
    }


def _verify_job_against_manifest(job: Dict[str, Any],
                                 manifest: Dict[str, Any]) -> None:
    checks = (
        ("job_id", job.get("job_id"), manifest.get("case_id")),
        ("vendor", job.get("vendor"), manifest.get("vendor")),
        ("account", job.get("account"), manifest.get("account_ref")),
        ("currency", job.get("currency"), manifest.get("currency")),
        ("period.from", job["period"].get("from"),
         (manifest.get("period") or {}).get("start")),
        ("period.to", job["period"].get("to"),
         (manifest.get("period") or {}).get("end")),
    )
    for label, job_value, manifest_value in checks:
        if _text(job_value) != _text(manifest_value):
            raise CloseError(
                "STALE_SOURCE",
                "period-job %s (%r) no longer matches canonical (%r); re-ingest "
                "and re-confirm before reporting"
                % (label, job_value, manifest_value))
    buyer = manifest.get("buyer")
    manifest_buyer = _text(buyer.get("org_id")) if isinstance(buyer, dict) else ""
    if manifest_buyer and _text(job.get("buyer")) != manifest_buyer:
        raise CloseError(
            "STALE_SOURCE",
            "period-job buyer (%r) no longer matches the controlled canonical "
            "buyer organization (%r); re-ingest and re-confirm before reporting"
            % (job.get("buyer"), manifest_buyer))


def _load_canonical_finance():
    try:
        from verovo_healthcheck import canonical_finance
        return canonical_finance
    except ImportError:
        # The buyer-ledger package is deliberately standalone; the repo-local
        # healthcheck is the single financial engine and is imported as a
        # sibling checkout, never vendored or reimplemented here.
        root = Path(__file__).resolve().parents[2]
        healthcheck = root / "healthcheck"
        if healthcheck.is_dir() and str(healthcheck) not in sys.path:
            sys.path.insert(0, str(healthcheck))
        from verovo_healthcheck import canonical_finance
        return canonical_finance


def _read_rule_map(canonical: Path) -> Tuple[Dict[str, Any], str]:
    path = canonical / "rule-map.json"
    if not path.is_file():
        raise CloseError("INPUT_REQUIRED", "canonical/rule-map.json is missing")
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CloseError("INPUT_REQUIRED", "canonical rule map is invalid: %s" % exc)
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise CloseError("INPUT_REQUIRED", "canonical rule map needs a rules array")
    return data, _sha_bytes(raw)


def _rule_versions(rule_map: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for rule in rule_map.get("rules", []):
        if not isinstance(rule, dict):
            continue
        out.append({
            "rule_id": _text(rule.get("rule_id")),
            "kind": _text(rule.get("kind")),
            "version": _text(rule.get("version")),
            "method": _text(rule.get("method")) or None,
            "unit_price": _text(rule.get("unit_price")) or None,
            "currency": _text(rule.get("currency")).upper() or None,
            "unit": _text(rule.get("unit")) or None,
            "valid_from": _text(rule.get("valid_from")) or None,
            "valid_to": _text(rule.get("valid_to")) or None,
            "confirmed_by": _text(rule.get("confirmed_by")) or None,
            "confirmed_at": _text(rule.get("confirmed_at")) or None,
            "source_ref": _text(rule.get("source_ref")) or None,
        })
    return out


# ---------------------------------------------------------------------------
# calculation
# ---------------------------------------------------------------------------

def _calculation_projection(outcome: Dict[str, Any]) -> Dict[str, Any]:
    """Canonical, timestamp-free projection used for the replay fingerprint."""
    def trim_conclusions(name: str) -> List[Dict[str, Any]]:
        rows = []
        for row in (outcome.get("evidence_conclusions") or {}).get(name, []):
            rows.append({
                "id": row.get("id"), "status": row.get("status"),
                "recalculation_status": row.get("recalculation_status"),
                "basis": row.get("basis"), "reason": row.get("reason"),
                "independence": row.get("independence"),
                "control_domain": row.get("control_domain"),
            })
        return rows

    intake = outcome.get("intake_report") or {}
    case = outcome.get("case") or {}
    economics = outcome.get("economics")
    return {
        "axes": {axis: outcome.get(axis) for axis in AXES},
        "verdicts": {name: trim_conclusions(name) for name in VERDICT_NAMES},
        "intake": {
            "reconciliation": intake.get("reconciliation"),
            "reconciliation_rows": intake.get("reconciliation_rows"),
            "counts": intake.get("counts"),
            "duplicates": intake.get("duplicates"),
            "charge_amount_mismatches": intake.get("charge_amount_mismatches"),
            "financial_status": intake.get("financial_status"),
            "open_items": intake.get("open_items"),
        },
        "case": {
            "tasks": case.get("tasks"),
            "usage": case.get("usage"),
            "cost_items": case.get("cost_items"),
            "outcomes": case.get("outcomes"),
            "open_items": case.get("open_items"),
            "financial_status": case.get("financial_status"),
        },
        "economics": None if economics is None else {
            "totals": economics.get("totals"),
            "findings": economics.get("findings"),
            "unallocated": economics.get("unallocated"),
            "allocation_coverage": economics.get("allocation_coverage"),
            "coverage": economics.get("coverage"),
        },
    }


def _blocked_outcome(job: Dict[str, Any], manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Explicit cannot-calculate outcome for an invoice-only bundle.

    A single invoice document is a valid input, but it is not a ledger and no
    amount may be invented.  The report still lists the gap and keeps every
    axis blocked.
    """
    documents = [item for item in manifest.get("documents") or []
                 if isinstance(item, dict)]
    open_items = [{
        "kind": "missing_invoice_lines",
        "detail": "no table invoice/bill line is available; no amount is booked",
        "source": "", "status": "open", "domain": "finance",
    }]
    return {
        "schema_version": "1.0",
        "kind": "verovo.canonical-finance",
        "case_id": job["job_id"],
        "status": "blocked",
        "finance_status": "blocked", "verification_status": "blocked",
        "delivery_status": "blocked", "economics_status": "blocked",
        "intake_report": {
            "status": "blocked", "counts": {},
            "reconciliation": {"status": "unknown", "billed_total": None,
                               "billed_subtotal": None, "reconciled_charges": [],
                               "counts": {}},
            "reconciliation_rows": [], "open_items": open_items,
            "duplicates": [], "charge_amount_mismatches": [],
            "financial_status": "open",
        },
        "case": {
            "schema_version": "1.0", "case_id": job["job_id"],
            "currency": job["currency"], "period": {"start": job["period"]["from"],
                                                     "end": job["period"]["to"]},
            "scope": {"vendor": job["vendor"], "account_ref": job["account"],
                      "currency": job["currency"]},
            "financial_status": "open", "open_items": open_items,
            "tasks": [], "runs": [], "usage": [], "cost_items": [],
            "outcomes": [], "documents": documents, "allocation_rules": [],
        },
        "economics": None,
        "evidence_conclusions": {name: [] for name in VERDICT_NAMES},
        "boundary": ("invoice-only input: no canonical ledger table exists, so no "
                     "amount, reconciliation or verdict is produced"),
    }


def calculate(case_dir: Path) -> Dict[str, Any]:
    """Run the existing canonical chain and persist a replayable calculation."""
    case_dir = Path(case_dir)
    _ensure_unsigned(case_dir)
    job = load_period_job(case_dir / "period-job.json")
    confirmed = case_dir / "intake" / "mapping-confirmed.json"
    if not confirmed.is_file():
        raise CloseError("REVIEW_REQUIRED",
                         "the field mapping is not confirmed; nothing can be calculated")
    canonical = _canonical_dir(case_dir)
    manifest = _read_json(canonical / "manifest.json", "canonical/manifest.json")
    _verify_job_against_manifest(job, manifest)
    mapping_hash = _sha_file(confirmed)
    declared_mapping_hash = _text(manifest.get("mapping_confirmed_sha256"))
    if declared_mapping_hash and declared_mapping_hash != mapping_hash:
        raise CloseError(
            "STALE_SOURCE",
            "mapping-confirmed.json changed after the canonical bundle was "
            "generated; re-ingest and re-confirm before reporting")
    snapshot = _canonical_snapshot(case_dir)
    rule_map, rule_hash = _read_rule_map(canonical)
    canonical_finance = _load_canonical_finance()
    try:
        outcome = canonical_finance.run(canonical)
    except ValueError as exc:
        if not manifest.get("sources") and manifest.get("documents"):
            outcome = _blocked_outcome(job, manifest)
        else:
            raise CloseError("INPUT_REQUIRED",
                             "canonical calculation failed: %s" % exc) from exc
    calculation = {
        "schema": CALCULATION_SCHEMA,
        "job_id": job["job_id"],
        "calculated_at": _now_iso(),
        "canonical_snapshot": snapshot,
        "rule_map_hash": rule_hash,
        "input_hashes": _input_hashes(case_dir),
        "rule_versions": _rule_versions(rule_map),
        "code_version": CODE_VERSION,
        "algorithm_versions": dict(ALGORITHM_VERSIONS),
        "statuses": {axis: outcome.get(axis) for axis in AXES},
        "calculation_fingerprint": _fingerprint(_calculation_projection(outcome)),
        "outcome": outcome,
    }
    _write_json(case_dir / "calculation.json", calculation)
    return calculation


def load_calculation(case_dir: Path) -> Dict[str, Any]:
    return _read_json(Path(case_dir) / "calculation.json", "calculation.json")


# ---------------------------------------------------------------------------
# report projection
# ---------------------------------------------------------------------------

def _read_bills(canonical: Path) -> Dict[str, Dict[str, Any]]:
    path = canonical / "bills.csv"
    if not path.is_file():
        # A usage-only or invoice-only bundle can still produce a partial
        # report: missing invoice lines are gaps, never zero-filled amounts.
        return {}
    rows: Dict[str, Dict[str, Any]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise CloseError("INPUT_REQUIRED", "canonical/bills.csv has no header")
        for line_no, row in enumerate(reader, start=2):
            clean = {_text(key).lower(): _text(value) for key, value in row.items()}
            charge_id = clean.get("charge_id") or ""
            if charge_id:
                clean["__line__"] = str(line_no)
                rows.setdefault(charge_id, clean)
    return rows


def _manifest_sources(manifest: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    sources = manifest.get("sources") or {}
    return sources if isinstance(sources, dict) else {}


def _source_domains(manifest: Dict[str, Any]) -> Dict[str, str]:
    domains: Dict[str, str] = {}
    for role, entries in _manifest_sources(manifest).items():
        for entry in entries:
            if isinstance(entry, dict) and _text(entry.get("control_domain")):
                domains[role] = _text(entry.get("control_domain"))
    return domains


def _aggregate_status(rows: Iterable[Dict[str, Any]]) -> str:
    statuses = [_text(row.get("status")) for row in rows]
    if not statuses:
        return "not_applicable"
    if "FAIL" in statuses:
        return "FAIL"
    if "UNKNOWN" in statuses:
        return "UNKNOWN"
    if "UNPROVABLE" in statuses:
        return "UNPROVABLE"
    if all(status == "PASS" for status in statuses):
        return "PASS"
    return "UNKNOWN"


def _verdict(name: str, conclusions: Dict[str, List[Dict[str, Any]]],
             *, evidence_map: Dict[str, str] = None,
             limitations: List[str] = None) -> Dict[str, Any]:
    rows = []
    for row in conclusions.get(name, []):
        item = {
            "id": row.get("id"),
            "status": row.get("status"),
            "recalculation_status": row.get("recalculation_status"),
            "basis": row.get("basis"),
            "reason": row.get("reason"),
            "source_ref": row.get("source_ref"),
            "control_domain": row.get("control_domain"),
            "independence": row.get("independence"),
            "rule_id": (row.get("rule_ref") or {}).get("rule_id")
            if isinstance(row.get("rule_ref"), dict) else row.get("rule_id"),
            "rule_version": (row.get("rule_ref") or {}).get("version")
            if isinstance(row.get("rule_ref"), dict) else row.get("rule_version"),
        }
        if name == "Delivery Accepted":
            item["task_id"] = (evidence_map or {}).get(
                _text(row.get("source_ref")), None)
        rows.append(item)
    counts: Dict[str, int] = {}
    for row in rows:
        status = _text(row.get("status"))
        counts[status] = counts.get(status, 0) + 1
    return {
        "verdict": name,
        "status": _aggregate_status(rows),
        "counts": counts,
        "rows": rows,
        "limitations": limitations or [],
    }


def _adjustment(raw: Dict[str, Any]) -> Tuple[str, str]:
    return (_text(raw.get("adjustment_type")).lower(),
            _text(raw.get("adjustment_state")).lower())


def _line_basis(raw: Dict[str, Any], row: Dict[str, Any],
                rule_ref: Optional[Dict[str, Any]]) -> str:
    parts = ["invoice line"]
    if row.get("usage_source_ref"):
        parts.append("distinct usage source %s" % row["usage_source_ref"])
    if rule_ref:
        parts.append("approved rule %s@%s" % (
            _text(rule_ref.get("rule_id")), _text(rule_ref.get("version"))))
    return "; ".join(parts)


def _conclusion_status(conclusions: Dict[str, List[Dict[str, Any]]],
                       name: str, charge_id: str) -> Optional[str]:
    for row in conclusions.get(name, []):
        if _text(row.get("id")) == charge_id:
            return _text(row.get("status")) or None
    return None


def _finding(line_ref: str, kind: str, detail: str, *, amount: Optional[Decimal],
             candidate_causes: List[str], next_evidence: str, owner: str,
             evidence_refs: List[str], state: str = "unresolved") -> Dict[str, Any]:
    return {
        "line_ref": line_ref,
        "kind": kind,
        "detail": detail,
        "impact_amount": _money(amount),
        "impact_currency": None,
        "candidate_causes": candidate_causes,
        "confirmed_cause": None,
        "qualification": "unresolved",
        "state": state,
        "owner": owner,
        "next_evidence": next_evidence,
        "evidence_refs": evidence_refs,
    }


def _source_evidence_index(manifest: Dict[str, Any], canonical: Path,
                           rule_map: Dict[str, Any],
                           lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    sources = []
    for role, entries in _manifest_sources(manifest).items():
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            source_path = canonical / _text(entry.get("path"))
            sources.append({
                "role": role,
                "path": _text(entry.get("path")),
                "sha256": _sha_file(source_path) if source_path.is_file() else None,
                "rule_id": entry.get("rule_id"),
                "rule_version": entry.get("rule_version"),
                "control_domain": entry.get("control_domain"),
                "independence": entry.get("independence"),
            })
    rules = []
    for rule in rule_map.get("rules", []):
        if not isinstance(rule, dict):
            continue
        rules.append({
            "rule_id": rule.get("rule_id"),
            "kind": rule.get("kind"),
            "version": rule.get("version"),
            "method": rule.get("method"),
            "unit_price": rule.get("unit_price"),
            "currency": rule.get("currency"),
            "unit": rule.get("unit"),
            "valid_from": rule.get("valid_from"),
            "valid_to": rule.get("valid_to"),
            "confirmed_by": rule.get("confirmed_by"),
            "confirmed_at": rule.get("confirmed_at"),
            "source_ref": rule.get("source_ref"),
        })
    documents = []
    for document in manifest.get("documents") or []:
        if isinstance(document, dict):
            documents.append({
                "path": document.get("path"),
                "sha256": document.get("sha256"),
                "document_ref": document.get("document_ref"),
                "review_status": document.get("review_status"),
                "booking_status": document.get("booking_status"),
            })
    return {
        "schema": EVIDENCE_SCHEMA,
        "sources": sources,
        "rule_map": {"rules": rules},
        "lines": [{key: line.get(key) for key in (
            "line_ref", "charge_id", "source_ref", "usage_source_ref",
            "quantity", "unit_price", "rule_id", "rule_version", "basis",
            "finding_ids")}
            for line in lines],
        "documents": documents,
        "boundary": ("Evidence index is a local index of observed inputs and approved "
                     "rules; it is not a cryptographic proof of supplier data."),
    }


def _load_review(case_dir: Path) -> Optional[Dict[str, Any]]:
    path = Path(case_dir) / "review.json"
    if not path.is_file():
        return None
    review = _read_json(path, "review.json")
    return review


def _review_projection(review: Optional[Dict[str, Any]],
                       open_items: List[Dict[str, Any]],
                       signoff: Optional[Dict[str, Any]] = None,
                       revision_id: str = "r1") -> Dict[str, Any]:
    unresolved = [item for item in open_items
                  if item.get("state") not in ("resolved", "closed",
                                               "not_applicable")]
    if not review:
        return {
            "state": "not_signed",
            "by": None,
            "at": None,
            "approved_scope": None,
            "reservations": [],
            "next_review": None,
            "signoff": {"state": "not_signed", "by": None, "at": None},
            "unresolved_items": [item["id"] for item in unresolved],
            "payment_authorization": False,
        }
    if signoff:
        return {
            "state": _text(signoff.get("state")),
            "by": signoff.get("reviewer"),
            "at": signoff.get("decided_at"),
            "approved_scope": signoff.get("approved_scope"),
            "reservations": list(signoff.get("reservations") or []),
            "next_review": (review or {}).get("next_review"),
            "signoff": {
                "state": _text(signoff.get("state")), "by": signoff.get("reviewer"),
                "at": signoff.get("decided_at"), "revision_id": signoff.get("revision_id") or revision_id,
            },
            "unresolved_items": [item["id"] for item in signoff.get("unresolved_items") or []],
            "payment_authorization": False,
            "attestation": signoff.get("attestation"),
        }
    return {
        "state": ("reviewed_not_signed"
                  if _text((review.get("signoff") or {}).get("state"))
                  in ("", "not_signed") else _text(review.get("state")) or "reviewed"),
        "by": review.get("reviewer"),
        "at": review.get("reviewed_at"),
        "approved_scope": review.get("approved_scope"),
        "reservations": list(review.get("reservations") or []),
        "next_review": review.get("next_review"),
        "signoff": dict(review.get("signoff") or {
            "state": "not_signed", "by": None, "at": None}),
        "unresolved_items": [item["id"] for item in unresolved],
        "payment_authorization": False,
        "attestation": review.get("attestation"),
    }


def _line_reconciliation(outcome: Dict[str, Any], bills: Dict[str, Dict[str, Any]],
                        conclusions: Dict[str, List[Dict[str, Any]]],
                        findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    report_currency = _text(
        (outcome.get("case") or {}).get("currency")).upper()
    for row in outcome["intake_report"].get("reconciliation_rows", []):
        charge_id = _text(row.get("charge_id"))
        raw = bills.get(charge_id, {})
        amount = _dec(row.get("bill_amount") or raw.get("amount"),
                      "bill %s amount" % charge_id)
        usage_amount = _dec(row.get("usage_amount"),
                            "usage amount for %s" % charge_id)
        adjustment_type, adjustment_state = _adjustment(raw)
        recalc = row.get("recalculation") or {}
        bill_recalc = recalc.get("bill") or {}
        rule_ref = bill_recalc.get("rule_ref") or {}
        expected = _dec(bill_recalc.get("expected_amount"),
                        "expected amount for %s" % charge_id)
        rule_difference = (_dec(bill_recalc.get("difference"),
                                "rule difference for %s" % charge_id)
                           if bill_recalc else None)

        included = True
        matched = Decimal("0")
        adjustment = Decimal("0")
        sign_mismatch = False
        line_currency = _text(
            row.get("bill_currency") or raw.get("currency")).upper() or None
        if adjustment_type in ("credit", "refund") and adjustment_state \
                not in APPLIED_STATES:
            classification = "excluded_pending_adjustment"
            included = False
        elif adjustment_type in ADJUSTMENT_TYPES and adjustment_state in APPLIED_STATES:
            # Credit/refund reduce the amount; tax/discount add to it.  A
            # reversed sign is not silently accepted as an adjustment.
            sign_mismatch = bool(
                amount is not None and (
                    (adjustment_type in ("credit", "refund") and amount > 0)
                    or (adjustment_type in ("tax", "discount") and amount < 0)))
            if sign_mismatch:
                classification = "residual"
            else:
                classification = "explained_adjustment"
                adjustment = amount or Decimal("0")
        elif adjustment_type and adjustment_type not in ADJUSTMENT_TYPES:
            classification = "residual"
        else:
            if amount is not None and usage_amount is not None \
                    and _text(row.get("usage_id")):
                if amount >= 0 and usage_amount >= 0:
                    matched = min(amount, usage_amount)
            classification = "matched_posting" if (
                amount is not None and matched == amount and amount != 0
            ) else "residual"
        currency_incomplete = bool(
            included and (not line_currency
                          or line_currency != report_currency))
        if currency_incomplete:
            classification = "excluded_currency_unknown"
            included = False
        residual = (amount - matched - adjustment) \
            if included and amount is not None else (
                Decimal("0") if not included else None)
        line_ref = _text(row.get("source_ref")) or "charge:%s" % charge_id
        line_findings = [finding["id"] for finding in findings
                         if finding["line_ref"] == line_ref]
        rows.append({
            "line_ref": line_ref,
            "charge_id": charge_id,
            "source_ref": row.get("source_ref"),
            "usage_id": row.get("usage_id"),
            "usage_source_ref": row.get("usage_source_ref"),
            "amount": _money(amount),
            "currency": line_currency,
            "currency_incomplete": currency_incomplete,
            "quantity": _text(row.get("quantity")) or _text(
                raw.get("quantity")) or None,
            "unit_price": _text(row.get("unit_price")) or _text(
                raw.get("unit_price")) or None,
            "classification": classification,
            "included_in_invoice_total": included,
            "matched_amount": _money(matched),
            "adjustment_amount": _money(adjustment),
            "residual_amount": _money(residual),
            "source_difference": _money(
                (amount - matched) if amount is not None else None),
            "expected_amount": _money(expected),
            "rule_difference": _money(rule_difference),
            "difference": _money(rule_difference if rule_difference is not None
                                 else ((amount - matched)
                                       if amount is not None else None)),
            "recalculation_status": row.get("recalculation_status"),
            "invoice_reconciled": _conclusion_status(
                conclusions, "Invoice Reconciled", charge_id),
            "vendor_reconciled": _conclusion_status(
                conclusions, "Vendor Reconciled", charge_id),
            "buyer_verified": _conclusion_status(conclusions, "Buyer Verified",
                                                 charge_id),
            "rule_id": _text(rule_ref.get("rule_id")) or _text(
                raw.get("rate_rule")) or None,
            "rule_version": _text(rule_ref.get("version")) or None,
            "adjustment_type": adjustment_type or None,
            "adjustment_state": adjustment_state or None,
            "adjustment_sign_mismatch": sign_mismatch,
            "state": ("unresolved" if (
                amount is None or not included or sign_mismatch
                or _conclusion_status(conclusions, "Invoice Reconciled",
                                      charge_id) == "UNPROVABLE"
                or _text(row.get("recalculation_status")) == "unknown") else
                "matched" if classification == "matched_posting" else
                "adjustment" if classification == "explained_adjustment" else
                "difference"),
            "basis": _line_basis(raw, row, rule_ref),
            "finding_ids": line_findings,
        })
    return rows


def _collect_findings(lines: List[Dict[str, Any]],
                      outcome: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []

    def add(line: Dict[str, Any], kind: str, detail: str,
            amount: Optional[Decimal], candidates: List[str],
            next_evidence: str, owner: str) -> None:
        if any(item["line_ref"] == line["line_ref"] and item["kind"] == kind
               for item in findings):
            return
        findings.append(_finding(
            line["line_ref"], kind, detail, amount=amount,
            candidate_causes=candidates, next_evidence=next_evidence,
            owner=owner, evidence_refs=[
                ref for ref in (line.get("source_ref"),
                                line.get("usage_source_ref")) if ref]))

    for line in lines:
        amount = _dec(line.get("amount"), "line amount")
        if line.get("currency_incomplete"):
            add(line, "currency_unknown",
                "the invoice line has no known period currency; it is excluded "
                "from USD totals instead of being assumed to be USD",
                amount, ["unexplained"],
                "currency evidence for the invoice line", "finance")
            line["state"] = "unresolved"
            continue
        if amount is None:
            add(line, "missing_invoice_amount",
                "the invoice line has no amount; invoice total and open "
                "difference remain unknown, never zero",
                None, ["unexplained"],
                "invoice line amount with source evidence", "finance")
            line["state"] = "unresolved"
            continue
        if not line["included_in_invoice_total"]:
            add(line, "credit_or_refund_not_applied",
                "credit/refund is issued or pending and is excluded from invoice "
                "total; it is open exposure, not a realized saving",
                amount, ["credit_or_commitment"],
                "credit application record + period revision", "finance")
            line["state"] = "unresolved"
            continue
        if line.get("adjustment_sign_mismatch"):
            add(line, "adjustment_sign_mismatch",
                "the adjustment sign contradicts its type; it stays in the "
                "residual bucket instead of becoming a known adjustment",
                amount, ["unexplained"],
                "signed credit/tax line with source evidence", "finance")
        if line["classification"] == "explained_adjustment":
            continue
        invoice_status = line.get("invoice_reconciled")
        rule_status = line.get("recalculation_status")
        if invoice_status == "UNPROVABLE":
            add(line, "invoice_reconciled_unprovable",
                "no distinct second ledger record; invoice reconciliation stays "
                "UNPROVABLE even if rule repricing is possible",
                amount, ["unexplained"],
                "independent usage/cost export for the same charge", "finance")
        elif invoice_status == "FAIL":
            add(line, "invoice_reconciled_fail",
                "the distinct ledger records disagree; neither amount is selected "
                "as truth",
                _dec(line.get("residual_amount"), "residual") or amount,
                ["usage_mismatch", "price_rule_mismatch"],
                "line-level bill/usage source evidence", "finance")
        if rule_status == "difference" or (line.get("rule_difference") not in (None, "", "0", "0.00")):
            add(line, "rule_repriced_fail",
                "the bill or usage amount differs from the approved rule "
                "recalculation; the difference is retained, not reclassified as a "
                "vendor error",
                _dec(line.get("rule_difference"), "rule difference"),
                ["price_rule_mismatch", "rounding_or_precision"],
                "approved contract price, unit and precision evidence", "procurement")
        elif rule_status == "unknown":
            add(line, "rule_repriced_unknown",
                "the approved rule could not decide this line", None,
                ["unexplained"], "applicable price rule and measured quantity",
                "procurement")
        if line.get("adjustment_type") and line.get("adjustment_type") not in ADJUSTMENT_TYPES:
            add(line, "unsupported_adjustment_type",
                "adjustment type %r is not supported; it stays in the residual "
                "bucket" % line.get("adjustment_type"), amount,
                ["unexplained"], "explicit tax/credit/refund line classification",
                "finance")
    return findings


def _open_items(lines: List[Dict[str, Any]], findings: List[Dict[str, Any]],
                outcome: Dict[str, Any], manifest: Dict[str, Any],
                draft: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen = set()

    def add(kind: str, detail: str, *, line_ref: str = "", amount: Any = None,
            currency: Any = None, owner: str = "finance",
            candidates: Optional[List[str]] = None, next_evidence: str = "",
            state: str = "unresolved", evidence_refs: Optional[List[str]] = None,
            finding_id: Optional[str] = None) -> None:
        # One review item per (kind, line): several findings can reference the
        # same line, but the queue must not duplicate the request or its amount.
        key = (kind, line_ref)
        if key in seen:
            return
        seen.add(key)
        items.append({
            "id": "D-%d" % (len(items) + 1),
            "kind": kind,
            "detail": detail,
            "line_ref": line_ref or None,
            "amount": _money(_dec(amount, "open item amount")) if amount not in (None, "") else None,
            "currency": _text(currency) or None,
            "state": state,
            "candidate_causes": candidates or [],
            "confirmed_cause": None,
            "owner": owner,
            "next_evidence": next_evidence,
            "evidence_refs": evidence_refs or [],
            "finding_id": finding_id,
        })

    if not lines:
        add("missing_invoice_lines",
            "no invoice/bill line is available; invoice_total, matched postings "
            "and the open difference remain unknown rather than zero",
            owner="finance", next_evidence="invoice or bill export for this period")

    for row in ((draft or {}).get("validation") or {}).get("period_outliers", []):
        if not isinstance(row, dict):
            continue
        row_id = _text(row.get("id")) or "outlier"
        add("period_outlier",
            "charge %s is outside the half-open period and was excluded from "
            "invoice_total: %s" % (row_id, _text(row.get("reason"))),
            line_ref="period:%s" % row_id,
            amount=row.get("amount"), currency=row.get("currency"),
            owner="finance",
            next_evidence="move the row to the correct period or correct its "
                          "service date; do not book it in this period",
            evidence_refs=[_text(row.get("source"))])

    for finding in findings:
        add(finding["kind"], finding["detail"], line_ref=finding["line_ref"],
            amount=finding["impact_amount"], owner=finding["owner"],
            candidates=finding["candidate_causes"],
            next_evidence=finding["next_evidence"],
            evidence_refs=finding["evidence_refs"], finding_id=finding["id"])

    for item in outcome.get("case", {}).get("open_items", []):
        if not isinstance(item, dict):
            continue
        if _text(item.get("status")) in ("resolved", "closed"):
            continue
        add(_text(item.get("kind")) or "open_item",
            _text(item.get("detail")) or "open item",
            line_ref=_text(item.get("source")) or "",
            amount=item.get("amount"), owner="finance",
            next_evidence="review the named source and record the resolution",
            state="unresolved")

    economics = outcome.get("economics") or {}
    decisive_failure_tasks = {
        _text(row.get("task_id")) for row in economics.get("outcomes", [])
        if isinstance(row, dict) and row.get("verified") is True
        and _text(row.get("status")).lower() in
        ("failure", "failed", "rejected", "cancelled")}
    for item in economics.get("findings", []):
        if not isinstance(item, dict):
            continue
        code = _text(item.get("code")) or "economics_finding"
        task_id = _text(item.get("task_id"))
        if code == "unverified_outcome" and task_id and \
                task_id in decisive_failure_tasks:
            # A buyer-verified FAIL is a decision, not an open evidence gap.
            continue
        add(code, _text(item.get("message")) or "economics finding",
            line_ref=task_id or _text(item.get("id")) or "",
            amount=item.get("amount"), owner="operations",
            next_evidence="task/run/outcome evidence", state="unresolved")
    for item in economics.get("unallocated", []):
        if not isinstance(item, dict):
            continue
        add(_text(item.get("kind")) or "unallocated",
            _text(item.get("reason")) or "unallocated cost",
            line_ref=_text(item.get("id")) or "", amount=item.get("amount"),
            currency=item.get("currency"), owner="operations",
            next_evidence="task mapping or approved allocation driver",
            state="unresolved")

    for document in manifest.get("documents") or []:
        if not isinstance(document, dict):
            continue
        if document.get("review_status") == "reviewed" and document.get("booking_status") == "booked":
            continue
        add("invoice_draft",
            "invoice/OCR document is unreviewed and unbooked: %s"
            % _text(document.get("path")),
            state="unresolved", owner="finance",
            next_evidence="human review of the invoice document",
            evidence_refs=[_text(document.get("document_ref"))])

    domains = _source_domains(manifest)
    if not any(domain == "buyer" for domain in domains.values()):
        add("buyer_verified_unprovable",
            "all ledger sources are vendor-controlled; amount agreement is not "
            "buyer-independent verification",
            state="unresolved", owner="finance",
            next_evidence="buyer-controlled usage or cost record")
    return items


_OPEN_ITEM_ROOT_CAUSES = {
    "missing_invoice_lines": "账单行缺失",
    "missing_charge_id": "账单行缺少 charge_id",
    "missing_invoice_amount": "账单行缺少金额",
    "currency_unknown": "账单行币种未知",
    "period_outlier": "行落在账期之外",
    "credit_or_refund_not_applied": "信用/退款未实际应用",
    "invoice_reconciled_unprovable": "缺少第二来源，无法跨源对账",
    "invoice_reconciled_fail": "两个来源的金额不一致",
    "rule_repriced_fail": "与合同规则重算不一致",
    "rule_repriced_unknown": "规则无法判定该行",
    "adjustment_sign_mismatch": "调整项符号与其类型矛盾",
    "unsupported_adjustment_type": "不支持的调整类型",
    "invoice_draft": "发票/OCR 文档未审阅",
    "buyer_verified_unprovable": "缺少买方控制的来源",
    "unverified_outcome": "任务结果未验证",
}

_OPEN_ITEM_MISSING_FIELDS = {
    "missing_invoice_lines": "账单行",
    "missing_charge_id": "charge_id",
    "missing_invoice_amount": "金额",
    "currency_unknown": "币种",
    "period_outlier": "服务日期与账期",
    "credit_or_refund_not_applied": "信用应用记录",
    "invoice_reconciled_unprovable": "独立用量或成本来源",
    "invoice_reconciled_fail": "一致的行金额",
    "rule_repriced_fail": "合同价格与数量证据",
    "rule_repriced_unknown": "适用价格规则",
    "adjustment_sign_mismatch": "带符号的调整凭据",
    "unsupported_adjustment_type": "调整类型分类",
    "invoice_draft": "人工审阅结果",
    "buyer_verified_unprovable": "买方控制的用量或成本记录",
    "unverified_outcome": "任务/运行/结果证据",
}


def _open_item_source(item: Dict[str, Any]) -> str:
    refs = [item.get("line_ref")] + list(item.get("evidence_refs") or [])
    for ref in refs:
        text = _text(ref)
        if not text or text.startswith(("period:", "charge:")):
            continue
        if "#" in text:
            return text.split("#", 1)[0]
        if text.endswith((".csv", ".jsonl", ".json")):
            return text
    return "general"


def _group_open_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group identical causes so a 20-row gap is one request, not 20.

    The detail list keeps every item; the group only names the shared root
    cause, the affected count, two representative item ids and one action.
    """
    groups: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for item in items:
        kind = _text(item.get("kind")) or "open_item"
        source = _open_item_source(item)
        missing_field = _OPEN_ITEM_MISSING_FIELDS.get(kind, "")
        key = (kind, source, missing_field)
        group = groups.get(key)
        if group is None:
            group = {
                "root_cause": _OPEN_ITEM_ROOT_CAUSES.get(kind, kind),
                "kind": kind,
                "source": source,
                "missing_field": missing_field or None,
                "count": 0,
                "samples": [],
                "action": _text(item.get("next_evidence")) or "人工复核",
            }
            groups[key] = group
        group["count"] += 1
        if len(group["samples"]) < 2:
            group["samples"].append(item.get("id"))
    return list(groups.values())


def _build_report_body(case_dir: Path, calculation: Dict[str, Any],
                       review: Optional[Dict[str, Any]], revision_id: str) -> Dict[str, Any]:
    case_dir = Path(case_dir)
    canonical = _canonical_dir(case_dir)
    job = load_period_job(case_dir / "period-job.json")
    manifest = _read_json(canonical / "manifest.json", "canonical/manifest.json")
    rule_map, rule_hash = _read_rule_map(canonical)
    outcome = calculation["outcome"]
    conclusions = outcome.get("evidence_conclusions") or {}
    bills = _read_bills(canonical)
    draft_path = case_dir / "intake" / "mapping-draft.json"
    draft = (_read_json(draft_path, "intake/mapping-draft.json")
             if draft_path.is_file() else None)
    period_outliers = ((draft or {}).get("validation") or {}).get(
        "period_outliers", [])

    # First pass: line classifications and finding references.
    draft_lines = _line_reconciliation(outcome, bills, conclusions, [])
    findings = _collect_findings(draft_lines, outcome)
    for index, finding in enumerate(findings, start=1):
        finding["id"] = "F-%d" % index
    lines = _line_reconciliation(outcome, bills, conclusions, findings)

    included = [line for line in lines if line["included_in_invoice_total"]]
    currency_incomplete = any(line.get("currency_incomplete") for line in lines)
    amount_incomplete = any(line["amount"] is None for line in included)
    has_amounts = bool(lines) and not amount_incomplete and not currency_incomplete
    case_open_kinds = {
        _text(item.get("kind"))
        for item in (outcome.get("case") or {}).get("open_items", [])
        if isinstance(item, dict)}
    missing_charge = "missing_charge_id" in case_open_kinds
    duplicate_rows = bool((outcome.get("intake_report") or {}).get("duplicates"))
    invoice_notes = []
    if missing_charge:
        invoice_notes.append("invoice row(s) without charge_id were not booked")
    if duplicate_rows:
        invoice_notes.append("duplicate invoice representations were not counted twice")
    if period_outliers:
        invoice_notes.append("out-of-period rows were excluded from the period total")
    if currency_incomplete:
        invoice_notes.append("unknown-currency rows were excluded from the USD total")
    if amount_incomplete:
        invoice_notes.append("invoice rows with no amount leave the total unknown")
    if has_amounts:
        invoice_total = sum((_dec(line["amount"], "line amount") or Decimal("0")
                             for line in included), Decimal("0"))
        matched_postings = sum((_dec(line["matched_amount"], "matched amount")
                                or Decimal("0") for line in included), Decimal("0"))
        known_adjustments = sum((_dec(line["adjustment_amount"], "adjustment amount")
                                 or Decimal("0") for line in included), Decimal("0"))
        residual = sum((_dec(line["residual_amount"], "residual amount")
                        or Decimal("0") for line in included), Decimal("0"))
        if invoice_total != matched_postings + known_adjustments + residual:
            raise CloseError(
                "INTERNAL_ERROR",
                "report line buckets do not close: invoice %s != matched %s + "
                "adjustments %s + residual %s"
                % (invoice_total, matched_postings, known_adjustments, residual))

        tax_applied = sum((_dec(line["adjustment_amount"], "tax") or Decimal("0")
                           for line in included
                           if line.get("adjustment_type") == "tax"), Decimal("0"))
        credits_applied = sum((_dec(line["adjustment_amount"], "credit")
                               or Decimal("0") for line in included
                               if line.get("adjustment_type") in
                               ("credit", "refund", "discount")), Decimal("0"))
        credits_pending = sum((_dec(line["amount"], "pending credit")
                               or Decimal("0") for line in lines
                               if line["classification"] ==
                               "excluded_pending_adjustment"), Decimal("0"))
        rule_lines = [line for line in included
                      if line["classification"] != "explained_adjustment"]
        rule_missing = [line for line in rule_lines
                        if line.get("expected_amount") in (None, "")]
        if rule_lines and not rule_missing:
            rule_repriced = sum(
                (_dec(line["expected_amount"], "expected") or Decimal("0")
                 for line in rule_lines), Decimal("0"))
            rule_difference = sum(
                (_dec(line["rule_difference"], "rule difference") or Decimal("0")
                 for line in rule_lines if line.get("rule_difference") not in
                 (None, "")), Decimal("0"))
        else:
            rule_repriced = None
            rule_difference = None
        rule_missing_lines = [line["line_ref"] for line in rule_missing]
        rule_status = ("complete" if rule_lines and not rule_missing else
                       "partial" if rule_missing else "not_applicable")
        buyer_independent = sum(
            (_dec(line["matched_amount"], "buyer matched") or Decimal("0")
             for line in included if line.get("buyer_verified") == "PASS"),
            Decimal("0"))
        negative_included = any(
            (_dec(line["amount"], "line amount") or Decimal("0")) < 0
            for line in included)
    else:
        # Missing invoice lines/amounts are unknown, never zero.  A partial
        # report can still list gaps without inventing a total.
        invoice_total = matched_postings = known_adjustments = residual = None
        tax_applied = credits_applied = credits_pending = None
        rule_repriced = rule_difference = buyer_independent = None
        negative_included = False
        rule_missing_lines = []
        rule_status = "unknown"

    amount_coverage = None
    coverage_status = "comparable"
    coverage_reason = None
    if not lines:
        coverage_status = "not_comparable"
        coverage_reason = ("no invoice/bill line is available; invoice_total and "
                           "the amount denominator remain unknown, not zero")
    elif not has_amounts:
        coverage_status = "not_comparable"
        if currency_incomplete:
            coverage_reason = ("one or more invoice lines have no known period "
                               "currency; the total is unknown, not the sum of "
                               "the known lines")
        else:
            coverage_reason = ("one or more invoice lines have no amount; the total "
                               "is unknown, not the sum of the known lines")
    elif invoice_total <= 0 or negative_included:
        coverage_status = "not_comparable"
        coverage_reason = ("negative credit/tax or a non-positive invoice total makes "
                           "the net denominator inapplicable; gross/net are shown "
                           "separately")
    else:
        amount_coverage = _ratio(matched_postings, invoice_total)
        if matched_postings > invoice_total:
            coverage_status = "not_comparable"
            coverage_reason = "matched postings exceed the invoice total; the ratio is refused"
            amount_coverage = None
    invoice_lines = [line for line in included
                     if line["classification"] != "explained_adjustment"]
    matched_lines = [line for line in invoice_lines
                     if (_dec(line["matched_amount"], "matched") or Decimal("0")) > 0]
    gross = (sum((_dec(line["amount"], "line amount") or Decimal("0")
                  for line in included
                  if (_dec(line["amount"], "line amount") or Decimal("0")) > 0),
                 Decimal("0")) if has_amounts else None)
    coverage = {
        "record": {
            "numerator": len(matched_lines),
            "denominator": len(invoice_lines),
            "ratio": _ratio(Decimal(len(matched_lines)),
                            Decimal(len(invoice_lines))) if invoice_lines else None,
        },
        "amount": {
            "numerator": _money(matched_postings),
            "denominator": _money(invoice_total),
            "ratio": amount_coverage,
            "status": coverage_status,
            "reason": coverage_reason,
            "basis": "matched amount jointly supported by distinct bill and usage rows",
        },
        "gross_net": {
            "gross_invoice_total": _money(gross),
            "net_invoice_total": _money(invoice_total),
            "credits_applied": _money(credits_applied),
            "credits_pending": _money(credits_pending),
        },
        "boundary": ("record coverage and amount coverage have different "
                     "denominators and are never combined into one percentage"),
    }

    evidence_map: Dict[str, str] = {}
    for outcome_row in outcome.get("case", {}).get("outcomes", []):
        if isinstance(outcome_row, dict):
            ref = _text(outcome_row.get("source_ref"))
            if ref:
                evidence_map[ref] = _text(outcome_row.get("task_id")) or None

    verdicts = {
        "Rule Repriced": _verdict(
            "Rule Repriced", conclusions,
            limitations=["approved-rule recalculation only; not cross-source "
                         "reconciliation"]),
        "Invoice Reconciled": _verdict(
            "Invoice Reconciled", conclusions,
            limitations=["distinct source comparison only; equal amounts do not "
                         "prove correctness"]),
        "Vendor Reconciled": _verdict(
            "Vendor Reconciled", conclusions,
            limitations=["same-vendor comparison only; not buyer verification"]),
        "Buyer Verified": _verdict(
            "Buyer Verified", conclusions,
            limitations=["procedural provenance only; not cryptographic identity "
                         "proof; vendor-only sources cannot qualify"]),
        "Delivery Accepted": _verdict(
            "Delivery Accepted", conclusions, evidence_map=evidence_map,
            limitations=["buyer acceptance evidence only; a FAIL is a decision, "
                         "not an incomplete axis"]),
    }

    open_items = _open_items(lines, findings, outcome, manifest, draft)
    from . import close_actions
    open_items, difference_actions = close_actions.project_open_items(
        case_dir, open_items)
    open_item_groups = _group_open_items(open_items)
    signoff_path = case_dir / "signoff.json"
    signoff = _read_json(signoff_path, "signoff.json") if signoff_path.is_file() else None
    review_projection = _review_projection(review, open_items, signoff=signoff, revision_id=revision_id)
    economics = outcome.get("economics")
    if economics is None:
        task_economics = {
            "status": "not_applicable",
            "reason": "no outcomes source was ingested; Task Economics is not "
                      "applicable to this finance-only period",
            "outcome_source_present": False,
            "fully_loaded_cost": None,
            "attributed_cost_subtotal": None,
            "cost_status": None,
            "cost_per_verified_success": None,
            "roi": None,
            "unallocated": [],
            "findings": [],
            "tasks": [],
        }
    else:
        totals = economics.get("totals") or {}
        economics_axis = outcome.get("economics_status")
        task_economics = {
            "status": (economics_axis if economics_axis in
                       ("not_applicable", "partial", "complete") else "partial"),
            "fully_loaded_cost": totals.get("fully_loaded_cost"),
            "attributed_cost_subtotal": totals.get("attributed_cost_subtotal"),
            "cost_status": totals.get("cost_status"),
            "cost_per_verified_success": totals.get("cost_per_verified_success"),
            "roi": totals.get("roi"),
            "reason": totals.get("cost_per_verified_success_reason"),
            "roi_reason": totals.get("roi_reason"),
            "outcome_source_present": True,
            "unallocated": economics.get("unallocated") or [],
            "findings": economics.get("findings") or [],
            "tasks": economics.get("tasks") or [],
        }

    authority_raw = outcome.get("acceptance_authority") or {}
    authority_observed = authority_raw.get("observed") or []
    qualified_signers = sorted({_text(row.get("accepted_by")) for row in authority_observed
                                if row.get("qualified") and _text(row.get("accepted_by"))})
    authority_org = _text(authority_raw.get("org_id"))
    if qualified_signers:
        headline = "%s（受控组织 %s；来源：manifest.buyer.acceptance_signers）" % (
            "、".join(qualified_signers), authority_org)
    elif authority_org:
        headline = ("未识别（受控组织 %s 下无合格验收记录；本次不认定任何验收）"
                    % authority_org)
    else:
        headline = "未识别（未配置受控买方组织；本次不认定任何验收）"
    acceptance_authority = {
        "configured": bool(authority_raw.get("configured")),
        "org_id": authority_org or None,
        "org_name": _text(authority_raw.get("org_name")) or None,
        "source": authority_raw.get("source"),
        "qualified_signers": qualified_signers,
        "authorized_signers": [_text(item) for item in authority_raw.get("signers") or [] if _text(item)],
        "display": "、".join(qualified_signers) if qualified_signers else "未识别",
        "headline": headline,
        "observed": authority_observed,
    }

    limitations = [
        "single currency; no FX conversion and no cross-currency summation",
        "unreviewed invoice/OCR documents are not booked as cost",
        "credits/refunds are open exposure until applied and independently "
        "evidenced; no realized savings are claimed",
        "tax lines are not re-derived; tax treatment requires human review",
        "canonical finance slice covers one vendor/account/currency",
        "rule applicability is human-confirmed; this report does not re-sign rules",
        "this report is not a payment authorization",
    ]
    if outcome.get("delivery_status") != "not_applicable" and not qualified_signers:
        limitations.append(
            "no qualified controlled-buyer acceptance record; Delivery Accepted "
            "stays UNPROVABLE and this report does not confirm delivery")
    domains = _source_domains(manifest)
    if not any(domain == "buyer" for domain in domains.values()):
        limitations.append(
            "no buyer-controlled ledger source; Buyer Verified remains UNPROVABLE")
    pending_documents = [item for item in manifest.get("documents") or []
                         if item.get("review_status") != "reviewed" or item.get("booking_status") != "booked"]
    if pending_documents:
        limitations.append("%d invoice/OCR document(s) remain unreviewed"
                           % len(pending_documents))

    report = {
        "schema": REPORT_SCHEMA,
        "job_id": job["job_id"],
        "revision_id": revision_id,
        "generated_at": _now_iso(),
        "scope": {
            "buyer": job.get("buyer") or None,
            "vendor": job["vendor"],
            "account": job["account"],
            "period": {"start": job["period"]["from"],
                       "end": job["period"]["to"],
                       "to_exclusive": True,
                       "timezone": job["period"]["timezone"]},
            "currency": job["currency"],
        },
        **{axis: outcome.get(axis) for axis in AXES},
        "acceptance_authority": acceptance_authority,
        "axes_semantics": {
            "finance_status": "financial records, reconciliation gaps and cost "
                              "allocation are closed as an operating state; it does "
                              "not mean the difference is zero, the supplier is "
                              "correct, or payment is authorized",
            "verification_status": "task outcomes have buyer-procedural verification",
            "delivery_status": "every task has a decisive Delivery Accepted PASS/FAIL",
            "economics_status": "applicable outcome economics are complete",
        },
        "verdicts": verdicts,
        "amounts": {
            "currency": job["currency"],
            "invoice_total": _money(invoice_total),
            "invoice_total_status": (
                "unknown" if not has_amounts else
                "partial" if invoice_notes else "complete"),
            "invoice_total_notes": invoice_notes,
            "rule_repriced": _money(rule_repriced),
            "rule_repriced_subtotal": _money(rule_repriced),
            "rule_repricing_status": rule_status,
            "rule_repricing_missing_lines": rule_missing_lines,
            "rule_repricing_difference": _money(rule_difference),
            "matched_postings": _money(matched_postings),
            "distinct_source_reconciled": _money(matched_postings),
            "buyer_independent_attributable": _money(buyer_independent),
            "known_adjustments": _money(known_adjustments),
            "tax_applied": _money(tax_applied),
            "credits_applied": _money(credits_applied),
            "credits_pending": _money(credits_pending),
            "open_difference": _money(residual),
            "realized_savings": "0.00",
            "amount_basis_note": ("Different calibers are not summed. invoice_total = "
                                  "matched_postings + known_adjustments + "
                                  "open_difference, using mutually exclusive invoice "
                                  "lines; findings never add a second amount."),
            "identity": {
                "invoice_total": _money(invoice_total),
                "matched_postings": _money(matched_postings),
                "known_adjustments": _money(known_adjustments),
                "open_difference": _money(residual),
                "holds": ((invoice_total == matched_postings + known_adjustments +
                           residual) if has_amounts else None),
            },
        },
        "realized_savings": {
            "amount": "0.00",
            "claims": [],
            "basis": ("Applied credits reduce the amount due but are not claimed as "
                      "realized savings; issued/unapplied credits and refunds are "
                      "open exposure only."),
        },
        "coverage": coverage,
        "line_reconciliation": lines,
        "findings": findings,
        "findings_policy": {
            "findings_never_summed": True,
            "basis": ("line_reconciliation is the only amount denominator; multiple "
                      "findings on one line share the same line and never create a "
                      "second amount"),
        },
        "open_items": open_items,
        "open_item_groups": open_item_groups,
        "difference_actions": difference_actions,
        "signoff": dict(review_projection.get("signoff") or {
            "state": "not_signed", "by": None, "at": None}),
        "source_hashes": ["sha256:%s" % item["sha256"]
                          for item in calculation["canonical_snapshot"]["files"]],
        "rule_map_hash": "sha256:%s" % rule_hash,
        "review": review_projection,
        "task_economics": task_economics,
        "limitations": limitations,
        "disclaimer": ("This report is a buyer-side review artifact: it is not an "
                       "accounting voucher, not a payment authorization, not a "
                       "supplier dispute, and not a statement that the supplier "
                       "overcharged. Whether to settle, adjust or pay remains a "
                       "human buyer decision."),
        "replay": {
            "canonical_files": calculation["canonical_snapshot"]["files"],
            "canonical_fingerprint": calculation["canonical_snapshot"]["fingerprint"],
            "period_job_sha256": _sha_file(case_dir / "period-job.json"),
            "mapping_confirmed_sha256": _sha_file(case_dir / "intake" /
                                                  "mapping-confirmed.json"),
            "rule_map_sha256": rule_hash,
            "rule_versions": calculation.get("rule_versions") or _rule_versions(rule_map),
            "code_version": CODE_VERSION,
            "algorithm_versions": dict(ALGORITHM_VERSIONS),
            "calculation_fingerprint": calculation.get("calculation_fingerprint"),
            "output_snapshot_fingerprint": None,
            "lineage": [{
                "line_ref": line["line_ref"],
                "source_ref": line["source_ref"],
                "usage_source_ref": line["usage_source_ref"],
                "rule_id": line["rule_id"],
                "rule_version": line["rule_version"],
                "basis": line["basis"],
            } for line in lines],
        },
    }
    report["amount_layers"] = _money_layers(report)
    return report


def _money_layers(report: Dict[str, Any]) -> Dict[str, Any]:
    """Name what is known, unknown and who must supply the missing input.

    A missing economics source or incomplete invoice is not a row of dashes:
    each layer says why the number is absent and what changes that.
    """
    amounts = report["amounts"]
    economics = report.get("task_economics") or {}
    open_items = report.get("open_items") or []
    currency = report["scope"]["currency"]

    confirmed = ["%s %s %s" % (label, amounts[key], currency)
                 for label, key in (
                     ("供应商账单总额", "invoice_total"),
                     ("按已批准规则重新计价", "rule_repriced_subtotal"),
                     ("来自独立来源的可归属金额", "distinct_source_reconciled"),
                     ("已解释调整项", "known_adjustments"),
                     ("未解释差额", "open_difference"))
                 if amounts.get(key) not in (None, "")]

    unallocated = [row for row in (economics.get("unallocated") or [])
                   if isinstance(row, dict)]
    if unallocated:
        samples = []
        for row in unallocated[:2]:
            samples.append("%s %s %s" % (
                _text(row.get("kind")) or "未归属",
                _text(row.get("amount")) or "金额未知",
                _text(row.get("currency")) or currency))
        unattributed = "%d 笔未归属成本：%s" % (len(unallocated),
                                              "；".join(samples))
    elif economics.get("status") == "not_applicable":
        unattributed = ("不适用：本期未纳入 outcomes 来源，没有任务成本需要归因"
                        "（不是 0）")
    else:
        unattributed = "无：没有登记未归属成本"

    if open_items:
        samples = ["%s（%s）" % (
            item.get("id"),
            _OPEN_ITEM_ROOT_CAUSES.get(_text(item.get("kind")),
                                       _text(item.get("kind"))))
            for item in open_items[:2]]
        missing_records = "%d 条待处理项，例如 %s" % (
            len(open_items), "、".join(samples))
    else:
        missing_records = "无"

    unknown_fields = []
    if amounts.get("invoice_total") in (None, ""):
        unknown_fields.append("供应商账单总额（缺完整的账单行或金额）")
    for label, key in (("完全加载成本", "fully_loaded_cost"),
                       ("cost per verified success", "cost_per_verified_success"),
                       ("ROI", "roi")):
        if economics.get(key) in (None, ""):
            unknown_fields.append(label)
    if unknown_fields:
        if economics.get("status") == "not_applicable":
            reason = "缺 outcomes 来源与已验证成功数"
        else:
            reason = "；".join(value for value in (
                _text(economics.get("reason")), _text(economics.get("roi_reason"))) if value) \
                or "缺已验证结果或成本归因"
        not_computable = "%s（%s）" % ("、".join(unknown_fields), reason)
    else:
        not_computable = "无：核心经济指标均已计算"

    inputs = []
    if amounts.get("invoice_total") in (None, ""):
        inputs.append("财务补齐账单行（charge_id、金额、币种）")
    if economics.get("status") == "not_applicable":
        inputs.append("运营提供 outcomes 导出（task_id、status、verified、value）")
    elif economics.get("cost_per_verified_success") in (None, ""):
        inputs.append("运营补齐 outcomes 的验收人、验收时间、verified 或来源字段")
    if economics.get("fully_loaded_cost") in (None, ""):
        inputs.append("财务与运营确认完全加载成本口径")
    if economics.get("roi") in (None, "") and economics.get("outcome_source_present"):
        inputs.append("运营补充 outcomes.value 与对应币种；缺失不是 0，也不等于文件未提供")
    needed_inputs = "；".join(inputs) or "无：指标已完整，无需补充"

    return {
        "confirmed": {"items": confirmed,
                      "text": "；".join(confirmed) or
                      "无：账单行或金额缺失，总额是未知而不是 0"},
        "unattributed": {"count": len(unallocated), "text": unattributed},
        "missing_records": {"count": len(open_items), "text": missing_records},
        "not_computable": {"text": not_computable},
        "needed_inputs": {"items": inputs, "text": needed_inputs},
    }


def _needs_money_layers(report: Dict[str, Any]) -> bool:
    economics = report.get("task_economics") or {}
    return (economics.get("status") == "not_applicable"
            or report["amounts"].get("invoice_total") in (None, "")
            or economics.get("cost_per_verified_success") in (None, "")
            or economics.get("roi") in (None, ""))


def _unresolved_items(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [item for item in report.get("open_items") or []
            if item.get("state") not in ("resolved", "closed", "not_applicable")]


_BUSINESS_ROOT_CAUSES = {
    "charge_amount_mismatch": "账单金额与来源记录不一致",
    "usage": "费用尚未归集到具体任务",
    "unverified_value": "任务价值尚未核验",
}

_BUSINESS_ACTIONS = {
    "missing_invoice_lines": "由财务补齐账单行",
    "missing_charge_id": "由财务补齐账单行标识",
    "missing_invoice_amount": "由财务补齐账单金额",
    "currency_unknown": "由财务确认币种",
    "period_outlier": "由财务核对服务日期与账期",
    "credit_or_refund_not_applied": "由财务核对信用或退款的应用记录",
    "invoice_reconciled_unprovable": "补充独立的用量或成本来源",
    "invoice_reconciled_fail": "核对账单与用量来源，确认差额原因",
    "rule_repriced_fail": "核对合同价格、数量和计算精度",
    "rule_repriced_unknown": "由采购确认适用的价格规则",
    "adjustment_sign_mismatch": "由财务核对调整凭据与金额符号",
    "unsupported_adjustment_type": "由财务确认调整类型",
    "invoice_draft": "由财务完成账单或 OCR 文档审阅",
    "buyer_verified_unprovable": "补充买方控制的用量或成本记录",
    "unverified_outcome": "由运营补齐任务结果证据",
    "unverified_value": "由运营补齐任务价值记录",
    "charge_amount_mismatch": "由财务核对来源金额并登记处理结果",
    "usage": "由运营补齐任务映射或已批准的费用分摊依据",
}


def _business_group_view(group: Dict[str, Any]) -> Tuple[str, str]:
    kind = _text(group.get("kind"))
    cause = _BUSINESS_ROOT_CAUSES.get(kind) or _OPEN_ITEM_ROOT_CAUSES.get(kind) or _text(group.get("root_cause")) or "待处理差异"
    action = _BUSINESS_ACTIONS.get(kind)
    if not action:
        candidate = _text(group.get("action"))
        action = candidate if any("\u4e00" <= char <= "\u9fff" for char in candidate) else "由负责人补齐处理材料并登记结果"
    return cause, action


def _business_authority(authority: Dict[str, Any]) -> str:
    signers = [str(value) for value in authority.get("qualified_signers") or []]
    if not signers:
        return "未识别"
    return "%s（买方 %s）" % ("、".join(signers), authority.get("org_id") or "未识别")


def _business_report_main(report: Dict[str, Any]) -> List[str]:
    amounts = report["amounts"]
    currency = report["scope"]["currency"]
    unresolved = _unresolved_items(report)
    economics = report.get("task_economics") or {}
    delivery = report["verdicts"].get("Delivery Accepted") or {}
    accepted_count = len(delivery.get("rows") or []) if delivery.get("status") == "PASS" else 0
    if unresolved:
        decision = "当前不建议直接付款或续约：还有 %d 项未决事项。" % len(unresolved)
    else:
        decision = "当前没有未决事项；是否付款或续约仍由买方负责人决定。"
    lines = [
        "## 本期业务结论",
        "",
        f"- **{decision}**",
        "- 供应商账单 %s %s；按已批准规则重算 %s %s；未解释差额 %s %s。" % (
            _display(amounts["invoice_total"]), currency,
            _display(amounts["rule_repriced_subtotal"]), currency,
            _display(amounts["open_difference"]), currency),
        "- 买方验收：%s。" % (
            "已有 %d 条任务验收记录通过" % accepted_count
            if accepted_count else "任务交付尚不能确认"),
        "- 每个已验证成功任务成本：%s；投资回报：%s。" % (
            _display(economics.get("cost_per_verified_success"))
            if economics.get("cost_per_verified_success") not in (None, "") else "暂时无法计算",
            _display(economics.get("roi"))
            if economics.get("roi") not in (None, "") else "暂时无法计算"),
        "",
        "## 怎么判断这笔支出是否划算",
        "",
    ]
    if not economics.get("outcome_source_present"):
        lines.append("- 本期未接入任务结果，不能把费用与业务成效关联起来。")
    elif economics.get("roi") not in (None, ""):
        lines.append("- 当前投资回报按已登记的任务价值计算，只反映本期账本，不证明新增收益由该工具造成。")
    else:
        lines.append("- 任务价值或成本口径仍不完整，当前不能判断这笔支出是否划算；缺失不按 0 处理。")
    groups = report.get("open_item_groups") or []
    if groups:
        lines.extend(["", "## 需要先处理什么", ""])
        for group in groups[:3]:
            cause, action = _business_group_view(group)
            lines.append("- %s：影响 %d 条；%s" % (
                cause, group.get("count", 0), action))
    lines.append("")
    return lines


def _priority_summary(report: Dict[str, Any]) -> str:
    unresolved = _unresolved_items(report)
    if not unresolved:
        return "> 优先处置：无未决项"
    causes = [_business_group_view(group)[0]
              for group in report.get("open_item_groups") or []]
    if not causes:
        causes = list(dict.fromkeys(
            _OPEN_ITEM_ROOT_CAUSES.get(_text(item.get("kind")), _text(item.get("kind")) or "待处理差异")
            for item in unresolved))
    difference = _display(report["amounts"].get("open_difference"))
    currency = report["scope"].get("currency") or "币种未明"
    return "> 优先处置：%d 项未决；未解释差额 %s %s；主要原因：%s" % (
        len(unresolved), difference, currency, "；".join(causes[:3]) or "待确认")


def _signoff_summary(report: Dict[str, Any]) -> str:
    signoff = report.get("signoff") or {}
    if _text(signoff.get("state")) in ("", "not_signed"):
        return ""
    return "- 签核状态：%s；签核版本：%s；签核人：%s" % (
        signoff.get("state"), signoff.get("revision_id") or report.get("revision_id") or "r1",
        signoff.get("reviewer") or "—")


def _report_markdown(report: Dict[str, Any]) -> str:
    amounts = report["amounts"]
    authority = report.get("acceptance_authority") or {}
    lines = [
        "# 买方可签核月结报告 · %s" % report["job_id"],
        "",
        "> 实际验收方：%s" % _business_authority(authority),
        "> 本报告用于买方复核，不是付款授权；是否结算、调整或续约由买方负责人决定。",
        *_business_report_main(report),
        _priority_summary(report),
        "",
        "## 附录：核验与逐行明细",
        "",
        "## 一、核验范围",
        "",
        "- 买方：%s" % (report["scope"]["buyer"] or "—"),
        "- 供应商：%s" % report["scope"]["vendor"],
        "- 账户：%s" % report["scope"]["account"],
        "- 账期：%s → %s（半开区间 [from, to)，%s）" % (
            report["scope"]["period"]["start"], report["scope"]["period"]["end"],
            report["scope"]["period"]["timezone"]),
        "- 币种：%s" % report["scope"]["currency"],
        "",
        "## 二、五个核心金额（口径独立，不可简单相加）",
        "",
        "| 项目 | 金额 | 说明 |",
        "|---|---:|---|",
        "| 供应商账单总额 | %s | 账单行互斥分类后的合计 |" % _display(amounts["invoice_total"]),
        "| 按已批准规则重新计价 | %s | 独立重算，不是跨源对账 |" % _display(amounts["rule_repriced_subtotal"]),
        "| 来自独立来源的可归属金额 | %s | 账单与用量共同支持的金额 |" % _display(amounts["distinct_source_reconciled"]),
        "| 已解释调整项 | %s | 已应用的 tax/credit/discount |" % _display(amounts["known_adjustments"]),
        "| 未解释差额 | %s | 账单总额 − 共同支持金额 − 已解释调整 |" % _display(amounts["open_difference"]),
        "",
        "> %s" % amounts["amount_basis_note"],
        "",
        "## 三、完成轴与证据裁决（两个维度，不互相覆盖）",
        "",
        "| 完成轴 | 状态 |",
        "|---|---|",
    ]
    for axis in AXES:
        lines.append("| %s（%s） | %s |" % (
            axis, AXIS_LABELS_ZH.get(axis, ""), report[axis]))
    lines.extend(["", "| 裁决 | 状态 | 行数 | 限制 |", "|---|---|---:|---|"])
    for name in VERDICT_NAMES:
        verdict = report["verdicts"][name]
        label = VERDICT_LABELS_ZH.get(name)
        display = "%s（%s）" % (name, label) if label else name
        lines.append("| %s | %s | %d | %s |" % (
            display, verdict["status"], len(verdict["rows"]),
            "；".join(verdict["limitations"])))
    lines.extend([
        "",
        "## 四、覆盖（记录口径与金额口径分开）",
        "",
        "- 记录覆盖：%s/%s（%s）" % (
            report["coverage"]["record"]["numerator"],
            report["coverage"]["record"]["denominator"],
            report["coverage"]["record"]["ratio"]),
        "- 金额覆盖：%s/%s（%s，%s）" % (
            _display(report["coverage"]["amount"]["numerator"]),
            _display(report["coverage"]["amount"]["denominator"]),
            _display(report["coverage"]["amount"]["ratio"]),
            report["coverage"]["amount"]["status"]),
        "- 总额口径：gross %s / net %s" % (
            _display(report["coverage"]["gross_net"]["gross_invoice_total"]),
            _display(report["coverage"]["gross_net"]["net_invoice_total"])),
        "",
        "## 五、逐行差异",
        "",
        "| 行 | 账单金额 | 共同支持 | 未解释 | 分类 | 裁决依据 |",
        "|---|---:|---:|---:|---|---|",
    ])
    for line in report["line_reconciliation"]:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            line["line_ref"], line["amount"], line["matched_amount"],
            line["residual_amount"], line["classification"], line["basis"]))
    lines.extend(["", "## 六、处理清单", ""])
    action_summary = report.get("difference_actions") or {}
    lines.append("- 已登记处置：%d；显式解决：%d；仍未解决：%d" % (
        action_summary.get("total", 0),
        len(action_summary.get("resolved_item_refs") or []),
        len(action_summary.get("unresolved_item_refs") or [])))
    lines.append("- 处置记录不是供应商回执核验，也不是付款授权；只有显式 resolved 才关闭该项。")
    lines.append("")
    if report.get("open_item_groups"):
        lines.append("**根因分组（同一原因只处理一次）**")
        lines.append("")
        lines.append("| 根因 | 影响条数 | 代表样例 | 处理动作 |")
        lines.append("|---|---:|---|---|")
        for group in report["open_item_groups"]:
            lines.append("| %s | %d | %s | %s |" % (
                group["root_cause"], group["count"],
                "、".join(str(sample) for sample in group["samples"]),
                group["action"]))
        lines.append("")
        lines.append("**逐条明细**")
        lines.append("")
    if report["open_items"]:
        lines.append("| ID | 类型 | 状态 | 最新处置 | 来源凭据 | 行 | 金额 | 责任方 |")
        lines.append("|---|---|---|---|---|---|---:|---|")
        for item in report["open_items"]:
            latest = item.get("difference_action") or {}
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                item["id"], item["kind"], item["state"],
                latest.get("action") or "—", latest.get("source_ref") or "—",
                item["line_ref"] or "—", item["amount"] or "—", item["owner"]))
    else:
        lines.append("（无 open items）")
    layers = report.get("amount_layers") or {}
    lines.extend(["", "## 七、Task Economics", ""])
    if _needs_money_layers(report) and layers:
        lines.extend([
            "- 状态：%s" % report["task_economics"]["status"],
            "- 已确认金额：%s" % layers["confirmed"]["text"],
            "- 未归属金额：%s" % layers["unattributed"]["text"],
            "- 缺失记录：%s" % layers["missing_records"]["text"],
            "- 无法计算：%s" % layers["not_computable"]["text"],
            "- 需要补充：%s" % layers["needed_inputs"]["text"],
        ])
    else:
        lines.extend([
            "- 状态：%s" % report["task_economics"]["status"],
            "- fully loaded cost：%s" % _display(report["task_economics"]["fully_loaded_cost"]),
            "- cost per verified success：%s" % _display(report["task_economics"]["cost_per_verified_success"]),
            "- ROI：%s" % _display(report["task_economics"]["roi"]),
        ])
    tasks = report["task_economics"].get("tasks") or []
    if tasks:
        lines.extend([
            "",
            "**按任务归集（成本与结果分别列示，不等于付款授权）**",
            "",
            "| Task | 直接成本 | 分摊成本 | 完全成本 | 已验证成功 | 已验证价值 |",
            "|---|---:|---:|---:|---|---:|",
        ])
        for task in tasks:
            success = task.get("verified_success")
            lines.append("| %s | %s | %s | %s | %s | %s |" % (
                task.get("task_id"), _display(task.get("direct_cost")),
                _display(task.get("allocated_cost")), _display(task.get("fully_loaded_cost")),
                "未知" if success is None else success, _display(task.get("verified_value"))))
    lines.extend([
        "",
        "## 八、审核区",
        "",
        "- 审阅状态：%s" % report["review"]["state"],
        _signoff_summary(report),
        "- 说明：审阅状态只表示复核进度，不等于签核；已签核也不等于所有差异已解决。",
        "- 复核人：%s（%s）" % (report["review"]["by"] or "—",
                               report["review"]["at"] or "—"),
        "- 批准范围：%s" % (report["review"]["approved_scope"] or "—"),
        "- 保留意见：%s" % ("；".join(report["review"]["reservations"]) or "—"),
        "- 下次复核：%s" % (report["review"]["next_review"] or "—"),
        "- 未闭合项：%d；付款授权：%s" % (
            len(report["review"]["unresolved_items"]),
            report["review"]["payment_authorization"]),
        "",
        "## 九、重放元数据与限制",
        "",
        "- 输入快照指纹：`%s`" % report["replay"]["canonical_fingerprint"],
        "- 规则表 hash：`%s`" % report["replay"]["rule_map_sha256"],
        "- 代码版本：`%s`" % report["replay"]["code_version"],
        "- 输出快照指纹：%s" % (
            "`%s`" % report["replay"]["output_snapshot_fingerprint"]
            if report["replay"]["output_snapshot_fingerprint"]
            else "未生成（calculate 未写入输出快照）"),
        "",
    ])
    lines.extend("- %s" % item for item in report["limitations"])
    lines.append("")
    return "\n".join(lines)


def _write_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, Any]],
               money_fields: Tuple[str, ...] = ()) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore",
                            lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _csv_safe(row.get(field),
                                          money=field in money_fields)
                         for field in fieldnames})
    _write_text(path, buffer.getvalue())


def build_report(case_dir: Path, *, revision_id: str = None) -> Dict[str, Any]:
    """Build the six report artifacts from a current calculation."""
    case_dir = Path(case_dir)
    if revision_id is None:
        revision_id = case_dir.name if case_dir.parent.name == "revisions" else "r1"
    _ensure_unsigned(case_dir)
    calculation = load_calculation(case_dir)
    expected_calculation = _fingerprint(
        _calculation_projection(calculation.get("outcome") or {}))
    if calculation.get("calculation_fingerprint") != expected_calculation:
        raise CloseError(
            "STALE_SOURCE",
            "calculation.json no longer matches its own fingerprint; run close "
            "calculate again instead of trusting an edited calculation")
    stored_inputs = calculation.get("input_hashes") or {}
    current_inputs = _input_hashes(case_dir)
    if not stored_inputs:
        raise CloseError(
            "STALE_SOURCE",
            "calculation.json has no input hashes; run close calculate again")
    for key, value in current_inputs.items():
        if stored_inputs.get(key) != value:
            raise CloseError(
                "STALE_SOURCE",
                "%s changed after calculation; run close calculate again before "
                "reporting" % key)
    snapshot = _canonical_snapshot(case_dir)
    if snapshot["fingerprint"] != calculation["canonical_snapshot"]["fingerprint"]:
        raise CloseError("STALE_SOURCE",
                         "the canonical bundle changed after calculation; run "
                         "close calculate again before reporting")
    review = _load_review(case_dir)
    report = _build_report_body(case_dir, calculation, review, revision_id)
    # Fingerprint the report body before adding the fingerprint itself and the
    # volatile generation timestamp.
    fingerprint_body = dict(report)
    fingerprint_body.pop("generated_at", None)
    fingerprint_body["replay"] = dict(fingerprint_body.get("replay") or {})
    fingerprint_body["replay"].pop("output_snapshot_fingerprint", None)
    report["replay"]["output_snapshot_fingerprint"] = _fingerprint(fingerprint_body)

    report_dir = case_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    _write_text(report_dir / "report.md", _report_markdown(report))
    _write_json(report_dir / "report.json", report)

    line_fields = [
        "line_ref", "charge_id", "source_ref", "usage_id", "usage_source_ref",
        "amount", "currency", "quantity", "unit_price", "classification",
        "included_in_invoice_total",
        "matched_amount", "adjustment_amount", "residual_amount",
        "source_difference", "difference", "expected_amount",
        "rule_difference", "recalculation_status",
        "invoice_reconciled", "vendor_reconciled", "buyer_verified",
        "rule_id", "rule_version", "adjustment_type", "adjustment_state",
        "state", "basis", "finding_ids",
    ]
    line_rows = []
    for row in report["line_reconciliation"]:
        copy = dict(row)
        copy["finding_ids"] = "|".join(row.get("finding_ids") or [])
        line_rows.append(copy)
    _write_csv(report_dir / "line-reconciliation.csv", line_fields, line_rows,
               money_fields=("amount", "matched_amount", "adjustment_amount",
                             "residual_amount", "source_difference",
                             "difference", "expected_amount", "rule_difference"))

    queue_fields = [
        "id", "kind", "line_ref", "amount", "currency", "state", "owner",
        "latest_action", "action_source_ref", "action_by", "action_occurred_at",
        "candidate_causes", "confirmed_cause", "next_evidence", "detail",
        "finding_id",
    ]
    queue_rows = []
    for item in report["open_items"]:
        row = dict(item)
        latest = item.get("difference_action") or {}
        row["latest_action"] = latest.get("action")
        row["action_source_ref"] = latest.get("source_ref")
        row["action_by"] = latest.get("by")
        row["action_occurred_at"] = latest.get("occurred_at")
        row["candidate_causes"] = "|".join(item.get("candidate_causes") or [])
        queue_rows.append(row)
    _write_csv(report_dir / "review-queue.csv", queue_fields, queue_rows,
               money_fields=("amount",))

    canonical = _canonical_dir(case_dir)
    manifest = _read_json(canonical / "manifest.json", "canonical/manifest.json")
    rule_map, rule_hash = _read_rule_map(canonical)
    evidence = _source_evidence_index(manifest, canonical, rule_map,
                                      report["line_reconciliation"])
    _write_json(report_dir / "evidence-index.json", evidence)

    artifacts = {}
    for name in ("report.md", "report.json", "line-reconciliation.csv",
                 "review-queue.csv", "evidence-index.json"):
        path = report_dir / name
        artifacts[name] = {"sha256": _sha_file(path), "bytes": path.stat().st_size}
    report_manifest = {
        "schema": REPORT_MANIFEST_SCHEMA,
        "job_id": report["job_id"],
        "revision_id": report["revision_id"],
        "generated_at": report["generated_at"],
        "code_version": CODE_VERSION,
        "algorithm_versions": dict(ALGORITHM_VERSIONS),
        "inputs": {
            "canonical_files": calculation["canonical_snapshot"]["files"],
            "canonical_fingerprint": calculation["canonical_snapshot"]["fingerprint"],
            "period_job_sha256": report["replay"]["period_job_sha256"],
            "mapping_confirmed_sha256": report["replay"]["mapping_confirmed_sha256"],
            "rule_map_sha256": rule_hash,
            "difference_actions_sha256": (
                _sha_file(case_dir / "difference-actions.jsonl")
                if (case_dir / "difference-actions.jsonl").is_file() else None),
        },
        "rule_versions": report["replay"]["rule_versions"],
        "calculation_fingerprint": report["replay"]["calculation_fingerprint"],
        "output_snapshot_fingerprint": report["replay"]["output_snapshot_fingerprint"],
        "artifacts": artifacts,
    }
    _write_json(report_dir / "manifest.json", report_manifest)
    return report


def record_review(case_dir: Path, *, reviewer: str, approved_scope: str = "",
                  reservations: List[str] = None, next_review: str = "",
                  revision_id: str = "") -> Dict[str, Any]:
    case_dir = Path(case_dir)
    if (case_dir / "revision.json").is_file():
        from . import close_revision
        root_case = case_dir.parent.parent
        with close_revision._case_lock(root_case):
            return _record_review_unlocked(
                case_dir, reviewer=reviewer, approved_scope=approved_scope,
                reservations=reservations, next_review=next_review, revision_id=revision_id)
    return _record_review_unlocked(
        case_dir, reviewer=reviewer, approved_scope=approved_scope,
        reservations=reservations, next_review=next_review, revision_id=revision_id)


def _record_review_unlocked(case_dir: Path, *, reviewer: str, approved_scope: str = "",
                            reservations: List[str] = None, next_review: str = "",
                            revision_id: str = "") -> Dict[str, Any]:
    """Record a named human review; never promote unresolved items to closed."""
    case_dir = Path(case_dir)
    _ensure_unsigned(case_dir)
    reviewer = _text(reviewer)
    if not reviewer:
        raise CloseError("INPUT_REQUIRED",
                         "a review needs a named reviewer; no signature is inferred")
    report_path = case_dir / "report" / "report.json"
    report = _read_json(report_path, "report.json")
    revision_path = case_dir / "revision.json"
    stored_revision = _read_json(revision_path, "revision.json") if revision_path.is_file() else {}
    resolved_revision_id = _text(revision_id) or _text(stored_revision.get("revision_id")) or "r1"
    if _text(next_review):
        try:
            date.fromisoformat(next_review)
        except ValueError as exc:
            raise CloseError("INPUT_REQUIRED",
                             "next_review must be an ISO date (YYYY-MM-DD)") from exc
    unresolved = [item for item in report.get("open_items", [])
                  if item.get("state") not in ("resolved", "closed",
                                               "not_applicable")]
    review = {
        "schema": REVIEW_SCHEMA,
        "state": "reviewed",
        "revision_id": resolved_revision_id,
        "reviewer": reviewer,
        "reviewed_at": _now_iso(),
        "attestation": "attested_by_declaration",
        "approved_scope": _text(approved_scope),
        "reservations": [item for item in (reservations or []) if _text(item)],
        "next_review": _text(next_review) or None,
        "signoff": {"state": "not_signed", "by": None, "at": None,
                    "revision_id": resolved_revision_id},
        "payment_authorization": False,
        "unresolved_items": [{
            "id": item["id"], "kind": item["kind"],
            "line_ref": item["line_ref"], "state": item["state"],
            "amount": item.get("amount"), "currency": item.get("currency"),
            "owner": item.get("owner"), "next_evidence": item.get("next_evidence"),
            "detail": item["detail"],
        } for item in unresolved],
        "boundary": ("Review records a named declaration, not cryptographic "
                     "identity proof; it does not close unresolved items, sign off "
                     "the period, or authorize payment."),
    }
    _write_json(case_dir / "review.json", review)
    try:
        refreshed = build_report(case_dir, revision_id=resolved_revision_id)
    except Exception:
        # A review must not survive as a signature over a report that could
        # not be rebuilt; keep the retry path open.
        (case_dir / "review.json").unlink()
        raise
    if revision_path.is_file():
        from . import close_revision
        revision = close_revision._read_json(revision_path, "revision.json")
        revision["report_fingerprint"] = (refreshed.get("replay") or {}).get(
            "output_snapshot_fingerprint")
        close_revision._write_json(revision_path, revision)
    return review
