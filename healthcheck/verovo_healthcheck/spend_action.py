"""Project an existing Audit Case into a buyer spend-action worklist."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import money


ACTION_ORDER = (
    "evidence_required",
    "review_required",
    "unreviewed",
    "ready_for_payment",
    "partially_settled",
    "settled",
)
EVIDENCE_CLASSIFICATIONS = {
    "missing_evidence",
    "currency_mismatch",
    "unverified_outcome",
}
OPEN_STATUSES = {"identified", "validated", "accepted"}
CLOSED_STATUSES = {"realized", "dismissed"}
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class SpendActionError(ValueError):
    pass


def _money(value: Any, field: str, currency: str) -> str:
    if value is None or isinstance(value, bool):
        raise SpendActionError("%s: amount is UNKNOWN, not zero" % field)
    try:
        number = money.parse_exact(value, field=field)
    except money.MoneyError as exc:
        raise SpendActionError(str(exc)) from exc
    return money.display_for_currency(number, currency)


def _currency(value: Any, field: str) -> str:
    if not isinstance(value, str) or not CURRENCY_RE.fullmatch(value):
        raise SpendActionError("%s: currency must be a three-letter code" % field)
    return value


def _inside(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise SpendActionError("invoice path must be a non-empty string")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise SpendActionError("invoice path escapes the case root: %s" % relative) from None
    if not candidate.is_file():
        raise SpendActionError("invoice path does not exist: %s" % relative)
    return candidate


def _source_digest(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".jsonl", ".csv", ".txt", ".md", ".yaml", ".yml"}:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass
        else:
            data = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _validate_case(case: Any) -> None:
    if not isinstance(case, dict):
        raise SpendActionError("audit case must be an object")
    for field in ("case_id", "buyer", "vendor", "period", "coverage", "findings", "status"):
        if field not in case:
            raise SpendActionError("audit case is missing %s" % field)
    if not isinstance(case["case_id"], str) or not case["case_id"].strip():
        raise SpendActionError("case_id must be a non-empty string")
    if not isinstance(case["findings"], list):
        raise SpendActionError("findings must be an array")
    coverage = case["coverage"]
    if not isinstance(coverage, dict):
        raise SpendActionError("coverage must be an object")
    _currency(coverage.get("currency"), "coverage.currency")
    finding_ids = [_finding_id(finding) for finding in case["findings"]]
    if len(set(finding_ids)) != len(finding_ids):
        raise SpendActionError("duplicate finding_id in audit case")


def _invoice_paths(case: Dict[str, Any], manifest: Optional[Dict[str, Any]]) -> List[str]:
    paths = set()
    if manifest:
        sources = manifest.get("sources", {})
        if isinstance(sources, dict):
            invoices = sources.get("invoices", [])
            if isinstance(invoices, str):
                invoices = [invoices]
            if not isinstance(invoices, list):
                raise SpendActionError("manifest sources.invoices must be an array")
            paths.update(invoices)
        clean = manifest.get("clean_charge_lines", [])
        if not isinstance(clean, list) or any(
            not isinstance(line_id, str) or not line_id for line_id in clean
        ):
            raise SpendActionError("manifest clean_charge_lines must be an array of strings")
    for finding in case["findings"]:
        if not isinstance(finding, dict):
            raise SpendActionError("each finding must be an object")
        affected = finding.get("affected_lines", [])
        if not isinstance(affected, list):
            raise SpendActionError("affected_lines must be an array")
        for line in affected:
            if isinstance(line, dict) and "path" in line:
                paths.add(line["path"])
        for ref in finding.get("evidence_refs", []):
            if isinstance(ref, dict) and str(ref.get("path", "")).endswith(".json"):
                paths.add(ref["path"])
    return sorted(paths)


def _load_invoice_lines(root: Path, paths: List[str]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    lines = {}
    for relative in paths:
        document_path = _inside(root, relative)
        try:
            document = json.loads(document_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SpendActionError("cannot read invoice %s: %s" % (relative, exc)) from exc
        if not isinstance(document, dict) or "lines" not in document:
            continue
        if not isinstance(document["lines"], list):
            raise SpendActionError("%s.lines must be an array" % relative)
        for line in document["lines"]:
            if not isinstance(line, dict) or not isinstance(line.get("line_id"), str):
                raise SpendActionError("%s contains a line without line_id" % relative)
            key = (relative, line["line_id"])
            if key in lines:
                raise SpendActionError("duplicate invoice line %s#%s" % key)
            currency = _currency(line.get("currency"), "%s#%s.currency" % key)
            amount = _money(line.get("amount"), "%s#%s.amount" % key, currency)
            lines[key] = {
                "source": {
                    "kind": "invoice_line",
                    "path": relative,
                    "line_id": line["line_id"],
                },
                "charge_id": line.get("charge_id"),
                "amount": {"currency": currency, "value": amount},
            }
    return lines


def _load_charge_reviews(root: Path, case: Dict[str, Any],
                         manifest: Optional[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    if not manifest:
        return {}
    raw_reviews = manifest.get("charge_reviews", [])
    if not isinstance(raw_reviews, list):
        raise SpendActionError("manifest charge_reviews must be an array")
    reviews = {}
    for raw in raw_reviews:
        if not isinstance(raw, dict):
            raise SpendActionError("each charge review must be an object")
        required = (
            "invoice_source_id", "line_id", "source_digest", "rule_version",
            "reviewed_by", "reviewed_at",
        )
        for field in required:
            if not isinstance(raw.get(field), str) or not raw[field].strip():
                raise SpendActionError("charge review needs %s" % field)
        key = (raw["invoice_source_id"], raw["line_id"])
        if key in reviews:
            raise SpendActionError("duplicate charge review for %s#%s" % key)
        source_path = _inside(root, raw["invoice_source_id"])
        actual_digest = _source_digest(source_path)
        valid = True
        reason = "Versioned clean review matches the invoice bytes and contract version."
        if not DIGEST_RE.fullmatch(raw["source_digest"]):
            raise SpendActionError("charge review source_digest must be 64 lowercase hex characters")
        if raw["source_digest"] != actual_digest:
            valid = False
            reason = "The invoice digest changed; the previous clean review is no longer valid."
        contract_version = case.get("contract_version")
        if contract_version and raw["rule_version"] != contract_version:
            valid = False
            reason = "The review rule_version does not match the case contract_version."
        try:
            datetime.fromisoformat(raw["reviewed_at"].replace("Z", "+00:00"))
        except ValueError:
            raise SpendActionError("charge review reviewed_at must be an ISO timestamp") from None
        reviews[key] = {
            "invoice_source_id": raw["invoice_source_id"],
            "line_id": raw["line_id"],
            "source_digest": actual_digest,
            "rule_version": raw["rule_version"],
            "reviewed_by": raw["reviewed_by"],
            "reviewed_at": raw["reviewed_at"],
            "valid": valid,
            "reason": reason,
        }
    return reviews


def _load_credit_records(root: Path, case: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    paths = sorted({
        ref["path"]
        for finding in case["findings"]
        for ref in finding.get("evidence_refs", [])
        if isinstance(ref, dict) and str(ref.get("path", "")).endswith(".json")
    })
    records = {}
    for relative in paths:
        document_path = _inside(root, relative)
        try:
            document = json.loads(document_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SpendActionError("cannot read credit evidence %s: %s" % (relative, exc)) from exc
        if not isinstance(document, dict) or "credits" not in document:
            continue
        if not isinstance(document["credits"], list):
            raise SpendActionError("%s.credits must be an array" % relative)
        for credit in document["credits"]:
            if not isinstance(credit, dict) or not isinstance(credit.get("credit_id"), str):
                raise SpendActionError("%s contains a credit without credit_id" % relative)
            credit_id = credit["credit_id"]
            if credit_id in records:
                raise SpendActionError("duplicate credit_id %s" % credit_id)
            records[credit_id] = dict(credit, source_path=relative)
    return records


def _finding_id(finding: Dict[str, Any]) -> str:
    finding_id = finding.get("finding_id")
    if not isinstance(finding_id, str) or not finding_id:
        raise SpendActionError("finding_id must be a non-empty string")
    return finding_id


def _finding_status(finding: Dict[str, Any]) -> str:
    status = finding.get("status")
    if status not in OPEN_STATUSES | CLOSED_STATUSES:
        raise SpendActionError("unsupported finding status: %r" % status)
    return status


def _classification(finding: Dict[str, Any]) -> str:
    classification = finding.get("classification")
    if not isinstance(classification, str) or not classification:
        raise SpendActionError("finding classification must be a non-empty string")
    return classification


def _reason(finding: Dict[str, Any]) -> str:
    reason = finding.get("reason")
    if isinstance(reason, str) and reason.strip():
        return reason
    amount = finding.get("amount", {})
    if isinstance(amount, dict) and isinstance(amount.get("basis"), str):
        return amount["basis"]
    return _classification(finding)


def _owner(action: str, classification: Optional[str]) -> str:
    if action == "ready_for_payment":
        return "accounts_payable"
    if action == "evidence_required":
        return "buyer_finance" if classification == "currency_mismatch" else "buyer_operations"
    if action == "review_required":
        return "buyer_finance" if classification == "shared_cost_allocation" else "procurement"
    return "buyer_finance"


def _action_for_finding(finding: Dict[str, Any]) -> str:
    if finding.get("provable") is False or _classification(finding) in EVIDENCE_CLASSIFICATIONS:
        return "evidence_required"
    return "review_required"


def _entry(entry_id: str, source: Dict[str, Any], action: str, reason: str,
           finding_ids: List[str], amount: Optional[Dict[str, str]],
           classification: Optional[str]) -> Dict[str, Any]:
    return {
        "entry_id": entry_id,
        "source": source,
        "charge_id": None,
        "action": action,
        "owner_role": _owner(action, classification),
        "reason": reason,
        "finding_ids": finding_ids,
        "amount": amount,
    }


def _settlement_for_line(finding: Dict[str, Any], line: Dict[str, Any],
                         credits: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    reference = finding.get("settlement_ref")
    finding_amount = finding.get("amount", {})
    currency = finding_amount.get("currency", line["amount"]["currency"])
    if not reference:
        return {
            "status": "resolution_claimed",
            "reference": None,
            "applied_amount": None,
            "detail": "The finding is marked realized, but no settlement_ref or verifiable credit/payment evidence was supplied.",
        }
    if not isinstance(reference, str) or not reference.startswith("credit-note:"):
        return {
            "status": "resolution_claimed",
            "reference": reference,
            "applied_amount": None,
            "detail": "The finding is marked realized, but the settlement reference is not a supported credit-note reference.",
        }
    credit_id = reference.split(":", 1)[1]
    credit = credits.get(credit_id)
    if credit is None:
        return {
            "status": "resolution_claimed",
            "reference": reference,
            "applied_amount": None,
            "detail": "The finding is marked realized, but the referenced credit note was not found in the supplied evidence.",
        }
    applied_amount = None
    if credit.get("amount") is not None:
        applied_amount = {
            "currency": _currency(credit.get("currency"), "%s.currency" % credit_id),
            "value": _money(credit.get("amount"), "%s.amount" % credit_id, currency),
        }
    if credit.get("applied") is not True or not str(credit.get("applied_at") or "").strip():
        return {
            "status": "credit_issued",
            "reference": reference,
            "applied_amount": applied_amount,
            "detail": "A credit note exists, but it has not been applied with an applied_at date.",
        }
    if applied_amount is None:
        return {
            "status": "settlement_mismatch",
            "reference": reference,
            "applied_amount": None,
            "detail": "The applied credit has no readable amount, so settlement cannot be verified.",
        }
    if applied_amount["currency"] != currency:
        return {
            "status": "settlement_mismatch",
            "reference": reference,
            "applied_amount": applied_amount,
            "detail": "The credit currency does not match the finding currency.",
        }
    line_charge_id = line.get("charge_id")
    credit_charge_id = credit.get("charge_id")
    if line_charge_id and credit_charge_id and line_charge_id != credit_charge_id:
        return {
            "status": "settlement_mismatch",
            "reference": reference,
            "applied_amount": applied_amount,
            "detail": "The credit charge_id does not match the affected invoice line.",
        }
    expected = Decimal(str(finding_amount.get("value")))
    applied = Decimal(applied_amount["value"])
    if applied < expected:
        return {
            "status": "partially_settled",
            "reference": reference,
            "applied_amount": applied_amount,
            "detail": "The applied credit covers only part of the finding amount; the line is not fully settled.",
        }
    if applied > expected:
        return {
            "status": "settlement_mismatch",
            "reference": reference,
            "applied_amount": applied_amount,
            "detail": "The applied credit exceeds the finding amount; the settlement needs reconciliation.",
        }
    return {
        "status": "settlement_verified",
        "reference": reference,
        "applied_amount": applied_amount,
        "detail": "The applied credit matches the finding amount, currency and charge identity.",
    }


def _settlement_action(settlement: Dict[str, Any]) -> str:
    if settlement["status"] == "settlement_verified":
        return "settled"
    if settlement["status"] == "partially_settled":
        return "partially_settled"
    return "review_required"


def _amount_coverage(actions: List[Dict[str, Any]], default_currency: str) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in actions:
        action = row["action"]
        amount = row["amount"]
        currency = amount["currency"] if amount else default_currency
        key = (action, currency)
        bucket = grouped.setdefault(key, {
            "action": action,
            "currency": currency,
            "known_value": Decimal("0.00"),
            "known_count": 0,
            "unknown_count": 0,
        })
        if amount is None:
            bucket["unknown_count"] += 1
        else:
            bucket["known_value"] += Decimal(amount["value"])
            bucket["known_count"] += 1
    amounts = []
    for key in sorted(grouped, key=lambda item: (ACTION_ORDER.index(item[0]), item[1])):
        bucket = grouped[key]
        known_count = bucket.pop("known_count")
        bucket["known_value"] = str(bucket["known_value"]) if known_count else None
        amounts.append(bucket)
    return amounts


def _summarize(actions: List[Dict[str, Any]], default_currency: str) -> Dict[str, Any]:
    counts = {action: 0 for action in ACTION_ORDER}
    for row in actions:
        counts[row["action"]] += 1
    invoice_actions = [row for row in actions if row["source"]["kind"] == "invoice_line"]
    case_actions = [row for row in actions if row["source"]["kind"] != "invoice_line"]
    return {
        "counts": {action: counts[action] for action in ACTION_ORDER if counts[action]},
        "amounts": _amount_coverage(invoice_actions, default_currency),
        "case_level_amounts": _amount_coverage(case_actions, default_currency),
    }


def build_action_list(case: Dict[str, Any], case_root: Path,
                      manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a read-only verovo.spend-action-list/0.1 projection."""
    _validate_case(case)
    root = Path(case_root).resolve()
    default_currency = _currency(case["coverage"]["currency"], "coverage.currency")
    lines = _load_invoice_lines(root, _invoice_paths(case, manifest))
    reviews = _load_charge_reviews(root, case, manifest)
    credits = _load_credit_records(root, case)
    open_by_line: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    realized_by_line: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    case_actions = []

    for finding in case["findings"]:
        finding_id = _finding_id(finding)
        status = _finding_status(finding)
        classification = _classification(finding)
        affected = finding.get("affected_lines", [])
        if not affected and status in OPEN_STATUSES:
            action = _action_for_finding(finding)
            finding_amount = finding.get("amount", {})
            value = finding_amount.get("value") if isinstance(finding_amount, dict) else None
            amount = None
            if value is not None:
                currency = _currency(finding_amount.get("currency"), "%s.currency" % finding_id)
                amount = {
                    "currency": currency,
                    "value": _money(value, "%s.amount.value" % finding_id, currency),
                }
            case_actions.append(_entry(
                "finding:%s" % finding_id,
                {"kind": "finding", "finding_id": finding_id},
                action,
                _reason(finding),
                [finding_id],
                amount,
                classification,
            ))
        for affected_line in affected:
            if not isinstance(affected_line, dict):
                raise SpendActionError("affected line must be an object")
            key = (affected_line.get("path"), affected_line.get("line_id"))
            if not all(isinstance(part, str) and part for part in key):
                raise SpendActionError("affected line needs path and line_id")
            if status in OPEN_STATUSES:
                open_by_line.setdefault(key, []).append(finding)
            elif status == "realized":
                realized_by_line.setdefault(key, []).append(finding)

    for key in set(open_by_line) | set(realized_by_line):
        if key not in lines:
            raise SpendActionError("affected line not found: %s#%s" % key)

    actions = list(case_actions)
    for key, line in lines.items():
        open_findings = open_by_line.get(key, [])
        realized_findings = realized_by_line.get(key, [])
        path, line_id = key
        settlement = None
        if open_findings:
            primary = sorted(
                open_findings,
                key=lambda finding: (
                    _action_for_finding(finding) != "evidence_required",
                    _finding_id(finding),
                ),
            )[0]
            action = _action_for_finding(primary)
            reason = "; ".join(_reason(finding) for finding in open_findings)
            classification = _classification(primary)
        elif realized_findings:
            primary = sorted(realized_findings, key=_finding_id)[0]
            settlement = _settlement_for_line(primary, line, credits)
            action = _settlement_action(settlement)
            reason = settlement["detail"]
            classification = None
        elif key in reviews and reviews[key]["valid"]:
            action = "ready_for_payment"
            reason = reviews[key]["reason"] + " The buyer still decides whether to pay."
            classification = None
        else:
            action = "unreviewed"
            reason = reviews[key]["reason"] if key in reviews else (
                "The line is not explicitly confirmed clean and has no affected-line finding; "
                "absence of a finding is not payment clearance."
            )
            classification = None
        finding_ids = sorted(
            _finding_id(finding) for finding in open_findings + realized_findings
        )
        row = _entry(
            "invoice:%s#%s" % key,
            line["source"],
            action,
            reason,
            finding_ids,
            line["amount"],
            classification,
        )
        row["charge_id"] = line["charge_id"]
        if settlement is not None:
            row["settlement"] = settlement
        if key in reviews:
            review = dict(reviews[key])
            review.pop("valid")
            review.pop("reason")
            row["charge_review"] = review
        actions.append(row)

    actions.sort(key=lambda row: (
        ACTION_ORDER.index(row["action"]),
        row["source"]["kind"],
        row["source"].get("path", ""),
        row["source"].get("line_id", row["source"].get("finding_id", "")),
    ))
    return {
        "schema": "verovo.spend-action-list/0.1",
        "case_id": case["case_id"],
        "buyer": case["buyer"],
        "vendor": case["vendor"],
        "period": case["period"],
        "actions": actions,
        "summary": _summarize(actions, default_currency),
        "executes_payment": False,
        "buyer_decision_required": True,
    }


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def action_markdown(result: Dict[str, Any]) -> str:
    """Render the worklist without turning it into a payment instruction."""
    output = [
        "# Spend action list — %s / %s" % (result["vendor"], result["case_id"]),
        "",
        "Buyer review worklist only. This report does not execute or authorize payment.",
        "",
        "| action | owner | source | amount | reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in result["actions"]:
        source = row["source"]
        label = (
            "%s#%s" % (source["path"], source["line_id"])
            if source["kind"] == "invoice_line"
            else source["finding_id"]
        )
        amount = row["amount"]
        amount_text = "%s %s" % (amount["currency"], amount["value"]) if amount else "UNKNOWN"
        output.append("| %s | %s | %s | %s | %s |" % (
            _md(row["action"]),
            _md(row["owner_role"]),
            _md(label),
            _md(amount_text),
            _md(row["reason"]),
        ))
    output.extend(["", "## Counts", ""])
    for action, count in result["summary"]["counts"].items():
        output.append("- **%s**: %d" % (action, count))
    output.extend([
        "",
        "Amounts remain separated by action and currency. UNKNOWN counts are not zero.",
        "Record the buyer's actual payment, hold, evidence request or vendor review separately.",
    ])
    return "\n".join(output) + "\n"
