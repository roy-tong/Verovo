#!/usr/bin/env python3
"""Build an Audit Case from a period bundle and a confirmed rule catalog.

The builder is deliberately narrow. It does not interpret contract prose: a
human confirms each rule (``rule_id`` plus version) in ``contract-rules.json``
together with the numeric parameters the rule needs, and this script implements
that confirmed rule as a deterministic check. A new vendor contract therefore
needs a new rule entry (with parameters), not a code change and not a model
call. If a confirmed parameter is missing the builder refuses to fall back to
any built-in default.

All money arithmetic uses :class:`decimal.Decimal` (parsed from the source
value's string form, quantized to cents with ROUND_HALF_UP, matching
``verovo_healthcheck.task_economics``) and serializes amounts as two-decimal
strings. Binary floats never touch a computation.

Settlement is evidenced, not asserted: a credit note counts as settlement only
when the register shows it was applied (``applied`` true plus ``applied_at``).
An issued-but-unapplied credit keeps the finding open and is cited as evidence.

Coverage never double counts: each invoice line contributes its worst single
open finding to ``unverified_amount`` (the maximum, never a sum over findings on
the same line), and a line with an UNPROVABLE finding contributes its billed
amount once. Allocation-only and outcome findings stay outside invoice coverage.

Inputs are a directory with this shape (paths come from ``manifest.json``)::

    manifest.json          case identity, sources, clean lines
    contract-rules.json    confirmed rules, versions, confirmers and parameters
    tasks.csv              task_id,type,status,owner,opened_at
    runs.jsonl             run_id,task_id,...
    outcomes.csv           outcome_id,task_id,status,verified,value,...
    invoices/*.json        invoice lines with charge_id, task_id, amount, ...
    credits/*.json         credit notes with charge_id, amount, applied, ...

Output is one Audit Case, excluding the workflow statuses that only a reviewer
can advance (``validated``/``accepted``/``dismissed``). A finding starts as
``identified``; only an applied, matching credit note starts a duplicate at
``realized``, because settlement is evidenced, not asserted.

Usage:
    python3 scripts/build_audit_case.py examples/audit-golden-case --out /tmp/case.json

Exit codes: 0 = built and written, 2 = unreadable or incomplete input.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

CENT = Decimal("0.01")
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
# Findings that change attribution or claim verification, not the invoice
# total, never contribute to coverage exposure.
EXCLUDED_FROM_EXPOSURE = ("shared_cost_allocation", "unverified_outcome")


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


def _dec(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise BuildError(f"{field}: not a decimal number: {value!r}") from exc


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _s(value: Decimal) -> str:
    return str(_money(value))


def _plain(value: Decimal) -> str:
    """Render a ratio for humans: 10.00 -> "10", 12.50 -> "12.5"."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _split_equal(total: Decimal, count: int) -> List[Decimal]:
    """Split *total* into *count* cent amounts that sum back to *total*.

    Floor each share, then hand the leftover cents to the first shares, the
    same policy as ``task_economics._allocate``. The sum identity is exact.
    """
    total = _money(total)
    if count <= 0:
        return []
    share = (total / Decimal(count)).quantize(CENT, rounding=ROUND_DOWN)
    shares = [share] * count
    leftover = int(((total - share * count) / CENT).to_integral_value())
    for index in range(leftover):
        shares[index] += CENT
    return shares


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


def _rule_param(rules: Dict[str, Dict[str, Any]], rule_id: str, key: str,
                kind: str = "decimal") -> Any:
    """Read a confirmed rule parameter. Missing parameters never fall back."""
    rule = rules.get(rule_id)
    if not rule:
        raise BuildError(f"rule {rule_id} is not confirmed in contract-rules.json")
    params = rule.get("parameters") if isinstance(rule.get("parameters"), dict) else {}
    if key not in params:
        raise BuildError(
            f"rule {rule_id} has no confirmed parameter {key!r}; the builder "
            "refuses to fall back to a built-in default")
    raw = params[key]
    if kind == "decimal":
        return _dec(raw, f"contract-rules rule {rule_id} parameter {key!r}")
    if kind == "int":
        try:
            return int(str(raw))
        except (TypeError, ValueError) as exc:
            raise BuildError(
                f"contract-rules rule {rule_id} parameter {key!r}: "
                f"not an integer: {raw!r}") from exc
    text = str(raw).strip()
    if not text:
        raise BuildError(
            f"contract-rules rule {rule_id} parameter {key!r}: empty string")
    return text


def _evidence(path: str, record_id: str, field: str) -> Dict[str, str]:
    return {"path": path, "record_id": str(record_id), "field": field}


def _affected(path: str, line_id: str) -> Dict[str, str]:
    return {"path": path, "line_id": str(line_id)}


def _line_amount(line: Dict[str, Any]) -> Decimal:
    raw = line.get("amount")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return Decimal("0.00")
    return _dec(raw, f"invoice line {line.get('line_id')} amount")


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


def _credit_matches(credit: Dict[str, Any], charge_id: str,
                    amount: Decimal, currency: str) -> bool:
    if str(credit.get("charge_id")) != str(charge_id):
        return False
    if str(credit.get("currency")) != str(currency):
        return False
    try:
        credit_amount = _dec(credit.get("amount") or 0, "credit note amount")
    except BuildError:
        return False
    return _money(credit_amount) == amount


def _settled_credit(credits: Iterable[Dict[str, Any]], charge_id: str,
                    amount: Decimal, currency: str) -> Optional[Dict[str, Any]]:
    """A credit note that matches the charge and evidences settlement.

    Settlement means the register shows the credit was applied (``applied``
    true with ``applied_at``). An issued-but-unapplied credit is not
    settlement; the money has not moved.
    """
    for credit in credits:
        if not _credit_matches(credit, charge_id, amount, currency):
            continue
        if credit.get("applied") is not True:
            continue
        if not str(credit.get("applied_at") or "").strip():
            continue
        return credit
    return None


def _issued_credit(credits: Iterable[Dict[str, Any]], charge_id: str,
                   amount: Decimal, currency: str) -> Optional[Dict[str, Any]]:
    """Any credit note issued against the charge, applied or not."""
    for credit in credits:
        if _credit_matches(credit, charge_id, amount, currency):
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
        duplicate_amount = _money(sum((_line_amount(item) for item in repeats),
                                      Decimal("0.00")))
        currency = str(first.get("currency"))
        refs = [_evidence(first["_source"], first["line_id"], "amount")]
        refs.extend(_evidence(item["_source"], item["line_id"], "amount") for item in repeats)
        affected = [_affected(item["_source"], item["line_id"]) for item in repeats]
        settled = _settled_credit(credits, charge_id, duplicate_amount, currency)
        issued = _issued_credit(credits, charge_id, duplicate_amount, currency)
        finding: Dict[str, Any] = {
            "case_id": case_id,
            "classification": "duplicate_charge",
            "severity": SEVERITY["duplicate_charge"],
            "amount": {
                "currency": currency,
                "value": _s(duplicate_amount),
                "basis": (
                    f"{len(group)} invoice lines share charge {charge_id}; "
                    f"{_s(duplicate_amount)} {currency} repeats an earlier line."
                ),
            },
            "status": "identified",
            "evidence_refs": refs,
            "affected_lines": affected,
            "rule_ref": _rule_ref(rules, str(first.get("rate_rule") or "CR-001")),
            "provable": True,
        }
        if settled is not None:
            finding["status"] = "realized"
            finding["settlement_ref"] = f"credit-note:{settled.get('credit_id')}"
            finding["evidence_refs"].append(
                _evidence(settled["_source"], str(settled.get("credit_id")), "amount"))
            finding["evidence_refs"].append(
                _evidence(settled["_source"], str(settled.get("credit_id")), "applied"))
        elif issued is not None:
            finding["amount"]["basis"] += (
                f" Credit note {issued.get('credit_id')} was issued but is not "
                "evidenced as applied, so the duplicate is still open."
            )
            finding["evidence_refs"].append(
                _evidence(issued["_source"], str(issued.get("credit_id")), "applied"))
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
                    f"Invoice line {line['line_id']} bills {_s(billed)} {line.get('currency')} "
                    "but carries no task_id and no approved allocation rule."
                ),
            },
            "status": "identified",
            "evidence_refs": [_evidence(line["_source"], line["line_id"], "task_id")],
            "affected_lines": [_affected(line["_source"], line["line_id"])],
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
                "value": _s(_line_amount(line)),
                "basis": (
                    f"Invoice line {line['line_id']} is billed in {line_currency}; "
                    f"reporting currency is {currency} and no confirmed FX rate exists."
                ),
            },
            "status": "identified",
            "evidence_refs": [_evidence(line["_source"], line["line_id"], "currency")],
            "affected_lines": [_affected(line["_source"], line["line_id"])],
            "rule_ref": _rule_ref(rules, "CR-005"),
            "provable": True,
        })
    return findings


def _discount_findings(lines: List[Dict[str, Any]], rules: Dict[str, Dict[str, Any]],
                       case_id: str, currency: str) -> List[Dict[str, Any]]:
    discount_rule = "CR-002"
    discount_rate = _rule_param(rules, "CR-002", "discount_rate")
    volume_threshold = _rule_param(rules, "CR-002", "volume_threshold", "int")
    by_task: Dict[str, int] = {}
    for line in lines:
        if line.get("duplicate_of") or line.get("discount_rule") != discount_rule:
            continue
        task_id = str(line.get("task_id") or "")
        by_task[task_id] = by_task.get(task_id, 0) + int(line.get("quantity") or 0)
    findings: List[Dict[str, Any]] = []
    for line in lines:
        if line.get("duplicate_of") or line.get("discount_rule") != discount_rule:
            continue
        task_id = str(line.get("task_id") or "")
        volume = by_task.get(task_id, 0)
        if volume < volume_threshold:
            continue
        if line.get("discount_applied"):
            continue
        if str(line.get("currency")) != currency:
            continue
        expected = _money(_line_amount(line) * discount_rate)
        findings.append({
            "case_id": case_id,
            "classification": "discount_not_applied",
            "severity": SEVERITY["discount_not_applied"],
            "amount": {
                "currency": currency,
                "value": _s(expected),
                "basis": (
                    f"Task {task_id} billable volume is {volume:,} requests "
                    f"(threshold {volume_threshold:,}), so line {line['line_id']} "
                    f"earns a {_plain(discount_rate * Decimal(100))}% discount."
                ),
            },
            "status": "identified",
            "evidence_refs": [_evidence(line["_source"], line["line_id"], "discount_applied")],
            "affected_lines": [_affected(line["_source"], line["line_id"])],
            "rule_ref": _rule_ref(rules, discount_rule),
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
        amount = _line_amount(line)
        if _settled_credit(credits, str(line.get("charge_id")), amount, currency) is not None:
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
        issued = _issued_credit(credits, str(line.get("charge_id")), amount, currency)
        basis = (
            f"Line {line['line_id']} bills a prepaid subscription cancelled on "
            f"{line['cancelled_at']}; no applied credit note exists for charge "
            f"{line.get('charge_id')}."
        )
        if issued is not None:
            basis += (
                f" Credit note {issued.get('credit_id')} was issued but is not "
                "evidenced as applied, so the refund is still open."
            )
            refs.append(_evidence(issued["_source"], str(issued.get("credit_id")), "applied"))
        findings.append({
            "case_id": case_id,
            "classification": "refund_missing",
            "severity": SEVERITY["refund_missing"],
            "amount": {
                "currency": currency,
                "value": _s(amount),
                "basis": basis,
            },
            "status": "identified",
            "evidence_refs": refs,
            "affected_lines": [_affected(line["_source"], line["line_id"])],
            "rule_ref": _rule_ref(rules, "CR-004"),
            "provable": True,
        })
    return findings


def _shared_cost_findings(lines: List[Dict[str, Any]], tasks: List[Dict[str, str]],
                          rules: Dict[str, Dict[str, Any]], case_id: str,
                          currency: str) -> List[Dict[str, Any]]:
    active = [task for task in tasks if str(task.get("status")) == "active"]
    findings: List[Dict[str, Any]] = []
    if not active:
        return findings
    allocation = _rule_param(rules, "CR-003", "allocation", "str")
    if allocation != "equal":
        raise BuildError(
            f"rule CR-003 confirms allocation {allocation!r}; the builder only "
            "implements 'equal'")
    for line in lines:
        if line.get("allocation_rule") != "equal":
            continue
        if str(line.get("currency")) != currency:
            continue
        total = _line_amount(line)
        shares = _split_equal(total, len(active))
        share = shares[0]
        misallocated = _money(total - share)
        if misallocated <= 0:
            continue
        findings.append({
            "case_id": case_id,
            "classification": "shared_cost_allocation",
            "severity": SEVERITY["shared_cost_allocation"],
            "amount": {
                "currency": currency,
                "value": _s(misallocated),
                "basis": (
                    f"Line {line['line_id']} assigns the whole {_s(total)} "
                    f"shared fee to {line.get('task_id')}; the confirmed rule splits it "
                    f"across {len(active)} active tasks ({_s(share)} each for the "
                    "first share)."
                ),
            },
            "status": "identified",
            "evidence_refs": [_evidence(line["_source"], line["line_id"], "amount")],
            "affected_lines": [_affected(line["_source"], line["line_id"])],
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
            claimed = _dec(outcome.get("value") or 0, "outcome value")
        except BuildError:
            claimed = Decimal("0.00")
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
                    f"Outcome {outcome['outcome_id']} claims {_s(claimed)} {currency} "
                    f"from {outcome.get('source_ref')} but has no buyer-owned verification."
                ),
            },
            "status": "identified",
            "evidence_refs": [_evidence("outcomes.csv", str(outcome["outcome_id"]), "verified")],
            "rule_ref": _rule_ref(rules, "CR-008"),
            "provable": False,
            "reason": (
                f"The claimed {_s(claimed)} {currency} value is unverified: "
                f"verified=false and the source is {outcome.get('source_ref')}, "
                "not a buyer-owned record; its value cannot enter ROI."
            ),
        })
    return findings


def _rate_findings(lines: List[Dict[str, Any]], rules: Dict[str, Dict[str, Any]],
                   case_id: str, currency: str) -> List[Dict[str, Any]]:
    priced_rules = ("CR-001", "CR-006")
    contract_prices: Dict[str, Decimal] = {}
    for rule_id in priced_rules:
        contract_prices[rule_id] = _rule_param(rules, rule_id, "unit_price")
    findings: List[Dict[str, Any]] = []
    for line in lines:
        rule_id = str(line.get("rate_rule") or "")
        if rule_id not in contract_prices:
            continue
        if str(line.get("currency")) != currency:
            continue
        contract_price = contract_prices[rule_id]
        billed_price = _dec(line.get("unit_price") or 0,
                            f"invoice line {line.get('line_id')} unit_price")
        if billed_price == contract_price:
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
                "value": _s(difference),
                "basis": (
                    f"Line {line['line_id']} bills {billed_price} per unit; "
                    f"confirmed {rule_id} price is {contract_price}."
                ),
            },
            "status": "identified",
            "evidence_refs": [_evidence(line["_source"], line["line_id"], "unit_price")],
            "affected_lines": [_affected(line["_source"], line["line_id"])],
            "rule_ref": _rule_ref(rules, rule_id),
            "provable": True,
        })
    return findings


def _coverage(findings: List[Dict[str, Any]], lines: List[Dict[str, Any]],
              currency: str) -> Dict[str, Any]:
    """Coverage in the reporting currency, one contribution per invoice line.

    ``unverified_amount`` sums each affected line's worst single open finding
    (the maximum across findings on that line, never a sum, so two findings on
    one line cannot double count). A line whose finding is UNPROVABLE carries
    its billed amount. Allocation and outcome findings never touch the invoice
    total. ``verified_amount`` + ``unverified_amount`` + settled duplicates
    always equals the invoice total.
    """
    line_amounts: Dict[Tuple[str, str], Decimal] = {}
    total = Decimal("0.00")
    for line in lines:
        key = (str(line["_source"]), str(line["line_id"]))
        line_amounts[key] = _line_amount(line)
        if str(line.get("currency")) == currency:
            total += line_amounts[key]
    total = _money(total)

    settled = Decimal("0.00")
    exposure: Dict[Tuple[str, str], Decimal] = {}
    for finding in findings:
        classification = finding["classification"]
        if classification in EXCLUDED_FROM_EXPOSURE:
            continue
        if finding["amount"]["currency"] != currency:
            continue
        if classification == "duplicate_charge" and finding["status"] == "realized":
            settled += _dec(finding["amount"]["value"], "settled duplicate amount")
            continue
        value = finding["amount"]["value"]
        for line_ref in finding.get("affected_lines") or []:
            key = (str(line_ref["path"]), str(line_ref["line_id"]))
            if value is None:
                # UNPROVABLE: the line is billed but cannot be attributed.
                candidate = line_amounts.get(key, Decimal("0.00"))
            else:
                candidate = _dec(value, "finding amount")
            if candidate > exposure.get(key, Decimal("0.00")):
                exposure[key] = candidate
    settled = _money(settled)
    unverified = _money(sum(exposure.values(), Decimal("0.00")))
    verified = _money(total - settled - unverified)
    if verified < 0:
        raise BuildError(
            "coverage underflow: open findings exceed the invoice total in "
            f"{currency}; check affected_lines and finding amounts")
    return {
        "verified_amount": _s(verified),
        "unverified_amount": _s(unverified),
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
