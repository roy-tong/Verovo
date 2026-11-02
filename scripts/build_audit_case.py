#!/usr/bin/env python3
"""Build an Audit Case from a period bundle and a confirmed rule catalog.

The builder is deliberately narrow. It does not interpret contract prose: a
human confirms each rule (``rule_id`` plus version) in ``contract-rules.json``
and this script implements that confirmed rule as a deterministic check. A new
vendor contract therefore needs a new rule handler, not a new model call.

Inputs are a directory with this shape (paths come from ``manifest.json``)::

    manifest.json          case identity, sources, clean lines
    contract-rules.json    confirmed rules, versions and confirmers
    tasks.csv              task_id,type,status,owner,opened_at
    runs.jsonl             run_id,task_id,...
    outcomes.csv           outcome_id,task_id,status,verified,value,...
    invoices/*.json        invoice lines with charge_id, task_id, amount, ...
    credits/*.json         credit notes with charge_id, amount, ...

Output is one Audit Case, excluding the workflow statuses that only a reviewer
can advance (``validated``/``accepted``/``dismissed``). A finding starts as
``identified``; a duplicate with a matching credit note starts as ``realized``
because settlement is evidenced, not asserted.

Usage:
    python3 scripts/build_audit_case.py examples/audit-golden-case --out /tmp/case.json

Exit codes: 0 = built and written, 2 = unreadable or incomplete input.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

CLASSIFICATION_ORDER = (
    "duplicate_charge",
    "missing_evidence",
    "currency_mismatch",
    "discount_not_applied",
    "refund_missing",
    "shared_cost_allocation",
    "unverified_outcome",
    "rate_mismatch",
)
SEVERITY = {
    "duplicate_charge": "high",
    "missing_evidence": "high",
    "currency_mismatch": "medium",
    "discount_not_applied": "medium",
    "refund_missing": "high",
    "shared_cost_allocation": "medium",
    "unverified_outcome": "high",
    "rate_mismatch": "medium",
}
STATUS_ORDER = {"identified": 0, "validated": 1, "accepted": 2, "realized": 3}
VOLUME_DISCOUNT_THRESHOLD = 20000
VOLUME_DISCOUNT_RATE = 0.10
REQUEST_UNIT_PRICE = 0.02
PRIORITY_ADDON_PRICE = 150.0


class BuildError(ValueError):
    """The bundle cannot produce an audit case without guessing."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot read {path}: {exc}") from exc


def _read_csv(path: Path) -> List[Dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except OSError as exc:
        raise BuildError(f"cannot read {path}: {exc}") from exc


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BuildError(f"cannot read {path}: {exc}") from exc
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BuildError(f"{path}:{number}: invalid JSON: {exc}") from exc
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _source_paths(manifest: Dict[str, Any], key: str) -> List[str]:
    sources = manifest.get("sources") if isinstance(manifest.get("sources"), dict) else {}
    value = sources.get(key)
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _inside(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if root.resolve() not in candidate.parents and candidate != root.resolve():
        raise BuildError(f"source {relative!r} escapes the bundle root")
    return candidate


def _rule_ref(rules: Dict[str, Dict[str, Any]], rule_id: str) -> Dict[str, Any]:
    rule = rules.get(rule_id)
    if not rule:
        raise BuildError(f"rule {rule_id} is not confirmed in contract-rules.json")
    return {
        "rule_id": rule_id,
        "version": str(rule.get("version")),
        "confirmed_by": rule.get("confirmed_by"),
        "confirmed_at": rule.get("confirmed_at"),
    }


def _evidence(path: str, record_id: str, field: str) -> Dict[str, str]:
    return {"path": path, "record_id": str(record_id), "field": field}


def _money(value: Any) -> float:
    return round(float(value), 2)


def _line_amount(line: Dict[str, Any]) -> float:
    return _money(line.get("amount") or 0)


def _load_lines(root: Path, manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    lines: List[Dict[str, Any]] = []
    for relative in _source_paths(manifest, "invoices"):
        invoice = _read_json(_inside(root, relative))
        for line in invoice.get("lines") or []:
            if not isinstance(line, dict):
                continue
            row = dict(line)
            row["_source"] = relative
            row["_invoice_id"] = invoice.get("invoice_id")
            lines.append(row)
    return lines


def _load_credits(root: Path, manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    credits: List[Dict[str, Any]] = []
    for relative in _source_paths(manifest, "credits"):
        register = _read_json(_inside(root, relative))
        for credit in register.get("credits") or []:
            if not isinstance(credit, dict):
                continue
            row = dict(credit)
            row["_source"] = relative
            row["_register_id"] = register.get("register_id")
            credits.append(row)
    return credits


def _matching_credit(credits: Iterable[Dict[str, Any]], charge_id: str,
                     amount: float, currency: str) -> Optional[Dict[str, Any]]:
    for credit in credits:
        if str(credit.get("charge_id")) != str(charge_id):
            continue
        if str(credit.get("currency")) != str(currency):
            continue
        if abs(_money(credit.get("amount") or 0) - amount) < 0.005:
            return credit
    return None


def _duplicate_findings(lines: List[Dict[str, Any]], credits: List[Dict[str, Any]],
                        rules: Dict[str, Dict[str, Any]], case_id: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for line in lines:
        grouped.setdefault(str(line.get("charge_id")), []).append(line)
    for charge_id, group in grouped.items():
        if len(group) < 2:
            continue
        first, repeats = group[0], group[1:]
        duplicate_amount = _money(sum(_line_amount(item) for item in repeats))
        currency = str(first.get("currency"))
        credit = _matching_credit(credits, charge_id, duplicate_amount, currency)
        refs = [_evidence(first["_source"], first["line_id"], "amount")]
        refs.extend(_evidence(item["_source"], item["line_id"], "amount") for item in repeats)
        finding: Dict[str, Any] = {
            "case_id": case_id,
            "classification": "duplicate_charge",
            "severity": SEVERITY["duplicate_charge"],
            "amount": {
                "currency": currency,
                "value": duplicate_amount,
                "basis": (
                    f"{len(group)} invoice lines share charge {charge_id}; "
                    f"{duplicate_amount:.2f} {currency} repeats an earlier line."
                ),
            },
            "status": "identified",
            "evidence_refs": refs,
            "rule_ref": _rule_ref(rules, str(first.get("rate_rule") or "CR-001")),
            "provable": True,
        }
        if credit is not None:
            finding["status"] = "realized"
            finding["settlement_ref"] = f"credit-note:{credit.get('credit_id')}"
            finding["evidence_refs"].append(
                _evidence(credit["_source"], str(credit.get("credit_id")), "amount"))
        findings.append(finding)
    return findings


def _missing_evidence_findings(lines: List[Dict[str, Any]], rules: Dict[str, Dict[str, Any]],
                               case_id: str, currency: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for line in lines:
        if str(line.get("task_id") or "").strip():
            continue
        if line.get("allocation_rule"):
            continue
        billed = _line_amount(line)
        findings.append({
            "case_id": case_id,
            "classification": "missing_evidence",
            "severity": SEVERITY["missing_evidence"],
            "amount": {
                "currency": currency,
                "value": None,
                "basis": (
                    f"Invoice line {line['line_id']} bills {billed:.2f} {line.get('currency')} "
                    "but carries no task_id and no approved allocation rule."
                ),
            },
            "status": "identified",
            "evidence_refs": [_evidence(line["_source"], line["line_id"], "task_id")],
            "rule_ref": _rule_ref(rules, "CR-007"),
            "provable": False,
            "reason": (
                f"Billing line {line['line_id']} has no task_id and no allocation rule, "
                "so the charge cannot be attributed to a task."
            ),
        })
    return findings


def _currency_findings(lines: List[Dict[str, Any]], rules: Dict[str, Dict[str, Any]],
                       case_id: str, currency: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for line in lines:
        line_currency = str(line.get("currency") or "")
        if line_currency == currency:
            continue
        findings.append({
            "case_id": case_id,
            "classification": "currency_mismatch",
            "severity": SEVERITY["currency_mismatch"],
            "amount": {
                "currency": line_currency,
                "value": _line_amount(line),
                "basis": (
                    f"Invoice line {line['line_id']} is billed in {line_currency}; "
                    f"reporting currency is {currency} and no confirmed FX rate exists."
                ),
            },
            "status": "identified",
            "evidence_refs": [_evidence(line["_source"], line["line_id"], "currency")],
            "rule_ref": _rule_ref(rules, "CR-005"),
            "provable": True,
        })
    return findings


def _discount_findings(lines: List[Dict[str, Any]], rules: Dict[str, Dict[str, Any]],
                       case_id: str, currency: str) -> List[Dict[str, Any]]:
    by_task: Dict[str, int] = {}
    for line in lines:
        if line.get("duplicate_of") or line.get("discount_rule") != "CR-002":
            continue
        task_id = str(line.get("task_id") or "")
        by_task[task_id] = by_task.get(task_id, 0) + int(line.get("quantity") or 0)
    findings: List[Dict[str, Any]] = []
    for line in lines:
        if line.get("duplicate_of") or line.get("discount_rule") != "CR-002":
            continue
        task_id = str(line.get("task_id") or "")
        if by_task.get(task_id, 0) < VOLUME_DISCOUNT_THRESHOLD:
            continue
        if line.get("discount_applied"):
            continue
        if str(line.get("currency")) != currency:
            continue
        expected = _money(_line_amount(line) * VOLUME_DISCOUNT_RATE)
        findings.append({
            "case_id": case_id,
            "classification": "discount_not_applied",
            "severity": SEVERITY["discount_not_applied"],
            "amount": {
                "currency": currency,
                "value": expected,
                "basis": (
                    f"Task {task_id} billable volume is {by_task[task_id]:,} requests, "
                    f"so line {line['line_id']} earns a {int(VOLUME_DISCOUNT_RATE * 100)}% discount."
                ),
            },
            "status": "identified",
            "evidence_refs": [_evidence(line["_source"], line["line_id"], "discount_applied")],
            "rule_ref": _rule_ref(rules, "CR-002"),
            "provable": True,
        })
    return findings


def _refund_findings(lines: List[Dict[str, Any]], credits: List[Dict[str, Any]],
                     outcomes: List[Dict[str, str]], rules: Dict[str, Dict[str, Any]],
                     case_id: str, currency: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for line in lines:
        if not line.get("cancelled_at"):
            continue
        if str(line.get("currency")) != currency:
            continue
        credit = _matching_credit(credits, str(line.get("charge_id")), _line_amount(line), currency)
        if credit is not None:
            continue
        refs = [_evidence(line["_source"], line["line_id"], "cancelled_at")]
        task_id = str(line.get("task_id") or "")
        for outcome in outcomes:
            if str(outcome.get("task_id")) != task_id:
                continue
            if str(outcome.get("status")) != "cancelled":
                continue
            refs.append(_evidence("outcomes.csv", str(outcome["outcome_id"]), "status"))
        seen_registers = set()
        for credit in credits:
            register_id = str(credit.get("_register_id") or "")
            key = (credit["_source"], register_id)
            if not register_id or key in seen_registers:
                continue
            seen_registers.add(key)
            refs.append(_evidence(credit["_source"], register_id, "credits"))
        findings.append({
            "case_id": case_id,
            "classification": "refund_missing",
            "severity": SEVERITY["refund_missing"],
            "amount": {
                "currency": currency,
                "value": _line_amount(line),
                "basis": (
                    f"Line {line['line_id']} bills a prepaid subscription cancelled on "
                    f"{line['cancelled_at']}; no credit note exists for charge {line.get('charge_id')}."
                ),
            },
            "status": "identified",
            "evidence_refs": refs,
            "rule_ref": _rule_ref(rules, "CR-004"),
            "provable": True,
        })
    return findings


def _shared_cost_findings(lines: List[Dict[str, Any]], tasks: List[Dict[str, str]],
                          rules: Dict[str, Dict[str, Any]], case_id: str, currency: str) -> List[Dict[str, Any]]:
    active = [task for task in tasks if str(task.get("status")) == "active"]
    findings: List[Dict[str, Any]] = []
    if not active:
        return findings
    for line in lines:
        if line.get("allocation_rule") != "equal":
            continue
        if str(line.get("currency")) != currency:
            continue
        share = _money(_line_amount(line) / len(active))
        misallocated = _money(_line_amount(line) - share)
        if misallocated <= 0:
            continue
        findings.append({
            "case_id": case_id,
            "classification": "shared_cost_allocation",
            "severity": SEVERITY["shared_cost_allocation"],
            "amount": {
                "currency": currency,
                "value": misallocated,
                "basis": (
                    f"Line {line['line_id']} assigns the whole {_line_amount(line):.2f} "
                    f"shared fee to {line.get('task_id')}; the confirmed rule splits it "
                    f"across {len(active)} active tasks ({share:.2f} each)."
                ),
            },
            "status": "identified",
            "evidence_refs": [_evidence(line["_source"], line["line_id"], "amount")],
            "rule_ref": _rule_ref(rules, "CR-003"),
            "provable": True,
        })
    return findings


def _outcome_findings(outcomes: List[Dict[str, str]], rules: Dict[str, Dict[str, Any]],
                      case_id: str, currency: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for outcome in outcomes:
        if str(outcome.get("verified")).strip().lower() == "true":
            continue
        try:
            claimed = _money(outcome.get("value") or 0)
        except (TypeError, ValueError):
            claimed = 0.0
        if claimed <= 0:
            continue
        findings.append({
            "case_id": case_id,
            "classification": "unverified_outcome",
            "severity": SEVERITY["unverified_outcome"],
            "amount": {
                "currency": currency,
                "value": None,
                "basis": (
                    f"Outcome {outcome['outcome_id']} claims {claimed:.2f} {currency} "
                    f"from {outcome.get('source_ref')} but has no buyer-owned verification."
                ),
            },
            "status": "identified",
            "evidence_refs": [_evidence("outcomes.csv", str(outcome["outcome_id"]), "verified")],
            "rule_ref": _rule_ref(rules, "CR-008"),
            "provable": False,
            "reason": (
                f"Outcome {outcome['outcome_id']} is not verified by a buyer-owned record; "
                "its value cannot enter ROI."
            ),
        })
    return findings


def _rate_findings(lines: List[Dict[str, Any]], rules: Dict[str, Dict[str, Any]],
                   case_id: str, currency: str) -> List[Dict[str, Any]]:
    expected = {"CR-001": REQUEST_UNIT_PRICE, "CR-006": PRIORITY_ADDON_PRICE}
    findings: List[Dict[str, Any]] = []
    for line in lines:
        rule_id = str(line.get("rate_rule") or "")
        if rule_id not in expected:
            continue
        if str(line.get("currency")) != currency:
            continue
        contract_price = expected[rule_id]
        billed_price = float(line.get("unit_price") or 0)
        if abs(billed_price - contract_price) < 0.0005:
            continue
        quantity = int(line.get("quantity") or 0)
        difference = _money((billed_price - contract_price) * quantity)
        if difference <= 0:
            continue
        findings.append({
            "case_id": case_id,
            "classification": "rate_mismatch",
            "severity": SEVERITY["rate_mismatch"],
            "amount": {
                "currency": currency,
                "value": difference,
                "basis": (
                    f"Line {line['line_id']} bills {billed_price:.3f} per unit; "
                    f"confirmed {rule_id} price is {contract_price:.3f}."
                ),
            },
            "status": "identified",
            "evidence_refs": [_evidence(line["_source"], line["line_id"], "unit_price")],
            "rule_ref": _rule_ref(rules, rule_id),
            "provable": True,
        })
    return findings


def _coverage(findings: List[Dict[str, Any]], lines: List[Dict[str, Any]],
              currency: str) -> Dict[str, Any]:
    total = _money(sum(_line_amount(line) for line in lines
                       if str(line.get("currency")) == currency))
    settled = _money(sum(
        finding["amount"]["value"] for finding in findings
        if finding["classification"] == "duplicate_charge"
        and finding["status"] == "realized"
        and finding["amount"]["currency"] == currency
    ))
    disputed = 0.0
    for finding in findings:
        classification = finding["classification"]
        if finding["amount"]["currency"] != currency:
            continue
        if classification == "shared_cost_allocation":
            continue  # allocation changes attribution, not the invoice total
        if classification == "unverified_outcome":
            continue  # no invoice line carries this amount
        if classification == "duplicate_charge" and finding["status"] == "realized":
            continue
        if classification == "missing_evidence":
            basis = finding["amount"]["basis"]
            digits = basis.split("bills ", 1)[-1].split(" ", 1)[0]
            try:
                disputed += _money(digits)
            except ValueError:
                pass
            continue
        disputed += _money(finding["amount"]["value"] or 0)
    unverified = _money(disputed)
    verified = _money(total - settled - unverified)
    return {
        "verified_amount": verified,
        "unverified_amount": unverified,
        "currency": currency,
    }


def build_case(case_dir: Path) -> Dict[str, Any]:
    case_dir = Path(case_dir).resolve()
    manifest = _read_json(case_dir / "manifest.json")
    contract = _read_json(_inside(case_dir, str(manifest.get("contract_rules") or "contract-rules.json")))
    case_id = str(manifest.get("case_id") or "")
    if not case_id:
        raise BuildError("manifest.json has no case_id")
    currency = str(contract.get("currency") or manifest.get("currency") or "")
    if len(currency) != 3:
        raise BuildError("contract currency must be a three-letter code")
    rules = {str(rule.get("rule_id")): rule for rule in contract.get("rules") or [] if isinstance(rule, dict)}
    tasks = _read_csv(_inside(case_dir, _source_paths(manifest, "tasks")[0]))
    outcomes = _read_csv(_inside(case_dir, _source_paths(manifest, "outcomes")[0]))
    lines = _load_lines(case_dir, manifest)
    credits = _load_credits(case_dir, manifest)

    collected: Dict[str, List[Dict[str, Any]]] = {
        "duplicate_charge": _duplicate_findings(lines, credits, rules, case_id),
        "missing_evidence": _missing_evidence_findings(lines, rules, case_id, currency),
        "currency_mismatch": _currency_findings(lines, rules, case_id, currency),
        "discount_not_applied": _discount_findings(lines, rules, case_id, currency),
        "refund_missing": _refund_findings(lines, credits, outcomes, rules, case_id, currency),
        "shared_cost_allocation": _shared_cost_findings(lines, tasks, rules, case_id, currency),
        "unverified_outcome": _outcome_findings(outcomes, rules, case_id, currency),
        "rate_mismatch": _rate_findings(lines, rules, case_id, currency),
    }
    findings: List[Dict[str, Any]] = []
    for classification in CLASSIFICATION_ORDER:
        for finding in collected[classification]:
            finding["finding_id"] = f"F-{len(findings) + 1:03d}"
            findings.append(finding)

    period = manifest.get("period") if isinstance(manifest.get("period"), dict) else {}
    return {
        "case_id": case_id,
        "buyer": str(manifest.get("buyer") or ""),
        "vendor": str(manifest.get("vendor") or ""),
        "period": {"start": period.get("start"), "end": period.get("end")},
        "contract_version": str(manifest.get("contract_version") or contract.get("contract_version") or ""),
        "scope": str(manifest.get("scope") or ""),
        "coverage": _coverage(findings, lines, currency),
        "findings": findings,
        "status": "in_review",
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build an Audit Case from a confirmed-rule period bundle")
    parser.add_argument("case_dir", help="bundle directory containing manifest.json")
    parser.add_argument("--out", default="", help="output path (default: <case_dir>/generated-audit-case.json)")
    args = parser.parse_args(argv)
    try:
        case = build_case(Path(args.case_dir))
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    target = Path(args.out) if args.out else Path(args.case_dir) / "generated-audit-case.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(case['findings'])} findings -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
