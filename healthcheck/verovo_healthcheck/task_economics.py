"""Task economics: connect technical usage, financial cost and business outcome.

This module deliberately does not parse contracts, call providers or decide what a
business "should" value. It computes a reproducible ledger from an explicit local
case file:

    BusinessTask <- AgentRun <- UsageEvent (financialized only)
    BusinessTask <- CostItem (direct or allocated shared)
    BusinessTask <- BusinessOutcome (verified only for success/value)

Rules that are intentionally strict:
- currencies are never added together;
- a usage row is not a monetary cost unless it is explicitly financialized and
  carries a cost/currency/source reference;
- duplicate cost rows are reported, not silently summed;
- missing task linkage stays unallocated (no timestamp guessing);
- shared cost requires an allocation rule with a version;
- unverified outcomes never enter the "cost per verified success" denominator;
- ROI is null unless verified business value exists in the case currency.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_DOWN
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class EconomicsError(ValueError):
    """Invalid case structure or an unsafe financial assumption."""


def _dec(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise EconomicsError("%s must be a monetary/quantity value" % field)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise EconomicsError("%s is not a valid number: %r" % (field, value))
    if not number.is_finite():
        raise EconomicsError("%s must be finite" % field)
    return number


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise EconomicsError("%s must be a non-empty string" % field)
    return text


def _list(case: Dict[str, Any], field: str) -> List[Any]:
    value = case.get(field)
    if value is None:
        return []
    if not isinstance(value, list):
        raise EconomicsError("%s must be an array" % field)
    return value


def _index(items: List[Any], field: str, label: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise EconomicsError("%s[%d] must be an object" % (label, index))
        key = _text(item.get(field), "%s[%d].%s" % (label, index, field))
        if key in out:
            raise EconomicsError("duplicate %s %r" % (label, key))
        out[key] = item
    return out


def _amount(item: Dict[str, Any], field: str, label: str) -> Decimal:
    return _dec(item.get(field), "%s.%s" % (label, field))


def _currency(item: Dict[str, Any], label: str, case_currency: str) -> str:
    raw = item.get("currency")
    if raw is None or not str(raw).strip():
        raise EconomicsError("%s currency is required; never assume the case currency" % label)
    currency = str(raw).strip().upper()
    if currency != case_currency:
        raise EconomicsError(
            "%s currency %s differs from case currency %s; cross-currency "
            "aggregation is forbidden" % (label, currency, case_currency))
    return currency


def buyer_evidence(item: Dict[str, Any]) -> bool:
    """Check observer and collection provenance without assuming independence."""
    method = str(item.get("collection_method") or "").strip().lower()
    return bool(
        str(item.get("source_party") or "").strip().lower() == "buyer"
        and str(item.get("independence") or "").strip().lower() == "buyer_independent"
        and method not in ("", "unknown", "ocr", "invoice2data")
        and not method.startswith("vendor")
    )


def _buyer_verified(item: Dict[str, Any]) -> bool:
    """Return whether a row carries explicit buyer-independent verification.

    ``verified=true`` is only an assertion until its observer and collection
    path are known.  In particular, two exports controlled by the same vendor
    remain vendor evidence, however many rows or files they contain.
    """
    return bool(item.get("verified") is True
                and str(item.get("source_ref") or "").strip()
                and buyer_evidence(item))


def _approval(rule: Dict[str, Any]) -> Tuple[str, str]:
    return (
        str(rule.get("confirmed_by") or rule.get("approved_by") or "").strip(),
        str(rule.get("confirmed_at") or rule.get("approved_at") or "").strip(),
    )


def _driver_error(rule: Dict[str, Any]) -> str:
    driver = rule.get("driver")
    if not isinstance(driver, dict):
        return "shared cost needs a versioned allocation driver"
    for field in ("id", "version", "source_ref"):
        if not str(driver.get(field) or "").strip():
            return "allocation driver needs %s" % field
    confirmed_by, confirmed_at = _approval(rule)
    if not confirmed_by or not confirmed_at:
        return "allocation rule and driver need confirmed_by/confirmed_at"
    driver_by, driver_at = _approval(driver)
    if (driver_by or driver_at) and (driver_by != confirmed_by or driver_at != confirmed_at):
        return "allocation driver approval must match the rule approval"
    return ""


def _allocate(total: Decimal, tasks: List[str], rule: Dict[str, Any]) -> Dict[str, Decimal]:
    """Allocate a shared cost without losing cents; remainder stays explicit.

    equal       equal split across tasks
    headcount   basis.weights task_id -> non-negative weight
    revenue     basis.values task_id -> non-negative verified revenue
    """
    method = str(rule.get("method") or "")
    basis = rule.get("basis") if isinstance(rule.get("basis"), dict) else {}
    if not tasks:
        return {}
    if method == "equal":
        weights = {task: Decimal("1") for task in tasks}
    elif method in ("headcount", "revenue"):
        source = basis.get("weights" if method == "headcount" else "values")
        if not isinstance(source, dict):
            raise EconomicsError("allocation rule %s needs basis.%s" % (
                rule.get("id"), "weights" if method == "headcount" else "values"))
        weights = {}
        for task in tasks:
            if task not in source or source.get(task) in (None, ""):
                raise EconomicsError(
                    "allocation driver %s has no explicit weight for task %s; "
                    "missing weights are unknown, not zero" % (rule.get("id"), task))
            value = source.get(task)
            weight = _dec(value, "allocation weight %s" % task)
            if weight < 0:
                raise EconomicsError("allocation weight cannot be negative: %s" % task)
            weights[task] = weight
    else:
        raise EconomicsError("unsupported allocation method: %r" % method)
    denominator = sum(weights.values(), Decimal("0"))
    if denominator <= 0:
        raise EconomicsError("allocation weights sum to zero")
    total = _money(total)
    # Largest remainder: preserve cents without negative shares or charging zero weights.
    exact = {task: total * weights[task] / denominator for task in tasks}
    allocated = {task: value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                 for task, value in exact.items()}
    cents = int((total - sum(allocated.values())) * 100)
    ranked = sorted(tasks, key=lambda task: exact[task] - allocated[task], reverse=True)
    for task in ranked[:cents]:
        allocated[task] += Decimal("0.01")
    return allocated


def recalculate_charge(item: Dict[str, Any], pricing_rules: Dict[str, Dict[str, Any]],
                       case_currency: str) -> Dict[str, Any]:
    """Recalculate one explicitly mapped per-unit charge, or stay UNKNOWN.

    This is the same Decimal rule evaluation used by the canonical chain; it
    never substitutes zero for a missing amount, currency or quantity.
    """
    field = "cost" if "usage_id" in item else "amount"
    row_id = str(item.get("usage_id") or item.get("cost_id") or item.get("charge_id") or "")
    actual_raw = item.get(field)
    actual: Optional[Decimal] = None
    if actual_raw not in (None, ""):
        actual = _money(_dec(actual_raw, "%s %s" % (row_id, field)))
    item_currency = str(item.get("currency") or "").strip().upper()
    rule_id = str(item.get("rate_rule") or item.get("pricing_rule") or "").strip()
    result: Dict[str, Any] = {
        "id": row_id, "charge_id": str(item.get("charge_id") or ""),
        "status": "unknown", "actual_amount": str(actual) if actual is not None else None,
        "expected_amount": None, "difference": None, "currency": item_currency or None,
        "rule_id": rule_id or None, "reason": "",
    }
    if actual is None:
        result["reason"] = "actual amount is unknown"
        return result
    if not item_currency:
        result["reason"] = "currency is unknown"
        return result
    if item_currency != case_currency:
        result["reason"] = "cross-currency recalculation is outside this case"
        return result
    rule = pricing_rules.get(rule_id)
    if rule is None:
        result["reason"] = "no approved pricing rule is mapped"
        return result
    confirmed_by, confirmed_at = _approval(rule)
    result["rule_ref"] = {"rule_id": rule_id, "version": rule.get("version"),
                          "confirmed_by": confirmed_by, "confirmed_at": confirmed_at}
    if not str(rule.get("version") or "").strip() or not confirmed_by or not confirmed_at:
        result["reason"] = "pricing rule version or approval is missing"
        return result
    if rule.get("method") != "per_unit":
        result["reason"] = "only approved per_unit pricing is supported"
        return result
    if item.get("quantity") in (None, ""):
        result["reason"] = "quantity is unknown, not zero"
        return result
    quantity = _dec(item.get("quantity"), "%s quantity" % row_id)
    if quantity < 0:
        raise EconomicsError("quantity cannot be negative: %s" % row_id)
    if rule.get("unit_price") in (None, ""):
        result["reason"] = "approved unit_price is missing"
        return result
    unit_price = _dec(rule.get("unit_price"), "pricing rule %s unit_price" % rule_id)
    rule_currency = str(rule.get("currency") or "").strip().upper()
    if not rule_currency:
        result["reason"] = "pricing rule currency is missing"
        return result
    if rule_currency != item_currency:
        result["status"] = "difference"
        result["reason"] = "pricing rule and charge currencies differ"
        return result
    expected = _money(quantity * unit_price)
    difference = _money(actual - expected)
    result.update({
        "expected_amount": str(expected), "difference": str(difference),
        "status": "matched" if difference == 0 else "difference",
        "reason": "approved per-unit rule recalculated" if difference == 0
                  else "actual amount differs from the approved per-unit recalculation",
    })
    return result


def evaluate(case: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(case, dict):
        raise EconomicsError("case must be an object")
    if case.get("schema_version") not in (None, "1.0"):
        raise EconomicsError("unsupported task-economics schema_version")
    case_id = _text(case.get("case_id"), "case_id")
    currency = _text(case.get("currency"), "currency").upper()
    period = case.get("period")
    if not isinstance(period, dict) or not period.get("start") or not period.get("end"):
        raise EconomicsError("period.start and period.end are required")

    tasks_raw = _list(case, "tasks")
    if not tasks_raw:
        raise EconomicsError("at least one task is required")
    tasks = _index(tasks_raw, "task_id", "tasks")
    task_ids = list(tasks)
    runs = _index(_list(case, "runs"), "run_id", "runs")
    usage = _index(_list(case, "usage"), "usage_id", "usage")
    documents = _index(_list(case, "documents"), "document_id", "documents")
    cost_items = _list(case, "cost_items")
    outcomes = _list(case, "outcomes")
    loss_events = _list(case, "loss_events")
    rules_raw = _list(case, "allocation_rules")
    rules: Dict[str, Dict[str, Any]] = {}
    for index, rule in enumerate(rules_raw):
        if not isinstance(rule, dict):
            raise EconomicsError("allocation_rules[%d] must be an object" % index)
        rid = _text(rule.get("id"), "allocation_rules[%d].id" % index)
        if rid in rules:
            raise EconomicsError("duplicate allocation rule %r" % rid)
        _text(rule.get("version"), "allocation_rules[%d].version" % index)
        if str(rule.get("method") or "") not in ("equal", "headcount", "revenue"):
            raise EconomicsError("allocation rule %s has unsupported method" % rid)
        rules[rid] = rule

    findings: List[Dict[str, Any]] = []
    direct: Dict[str, Decimal] = {task_id: Decimal("0") for task_id in task_ids}
    shared_items: List[Tuple[Decimal, str, str]] = []  # amount, rule_id, cost_id
    unallocated: List[Dict[str, Any]] = []
    unallocated_amount = Decimal("0")
    non_allocatable_amount = Decimal("0")
    coverage_unknown = False
    seen_cost = set()
    charge_owners = {}

    def record_unallocated(kind: str, row_id: str, reason: str,
                           amount: Optional[Decimal] = None,
                           non_allocatable: bool = False) -> None:
        nonlocal unallocated_amount, non_allocatable_amount, coverage_unknown
        unallocated.append({
            "kind": kind, "id": row_id, "reason": reason,
            "amount": str(_money(amount)) if amount is not None else None,
            "currency": currency if amount is not None else None,
            "status": "open",
        })
        if amount is None:
            coverage_unknown = True
        elif non_allocatable:
            non_allocatable_amount += amount
        else:
            unallocated_amount += amount

    # Conflicting representations of one charge make its cost unknown. Selecting
    # the first row would turn a reconciliation difference into a precise total.
    charge_values: Dict[str, set] = {}
    for item in list(usage.values()) + [x for x in cost_items if isinstance(x, dict)]:
        charge_id = str(item.get("charge_id") or "").strip()
        field = "cost" if "usage_id" in item else "amount"
        if not charge_id or item.get(field) in (None, "") or not str(item.get("currency") or "").strip():
            continue
        try:
            charge_values.setdefault(charge_id, set()).add(
                (str(_dec(item.get(field), field)), str(item["currency"]).upper()))
        except EconomicsError:
            pass
    conflicting_charges = {key for key, values in charge_values.items() if len(values) > 1}
    for charge_id in sorted(conflicting_charges):
        findings.append({"code": "charge_amount_mismatch", "severity": "review",
                         "id": charge_id,
                         "message": "conflicting amounts for charge %s; cost remains unknown" % charge_id})

    def claim_charge(item, row_id):
        # Shared identity must be supplied by the importer; never guess from time/amount.
        charge_id = str(item.get("charge_id") or "").strip()
        if not charge_id:
            return True
        if charge_id in charge_owners:
            findings.append({"code": "duplicate_charge", "severity": "review",
                             "id": row_id, "message": "charge %s already counted as %s" %
                             (charge_id, charge_owners[charge_id])})
            return False
        charge_owners[charge_id] = row_id
        return True

    # Technical usage is not a financial cost unless explicitly financialized.
    for usage_id, item in usage.items():
        if item.get("financialized") is not True or item.get("financial_status") == "represented_by_bill":
            continue
        amount: Optional[Decimal] = None
        if item.get("cost") not in (None, "") and str(item.get("currency") or "").strip():
            _currency(item, "usage %s" % usage_id, currency)
            amount = _amount(item, "cost", "usage %s" % usage_id)
            if amount < 0:
                raise EconomicsError("usage cost cannot be negative: %s" % usage_id)
        if item.get("cost") in (None, ""):
            record_unallocated("usage", usage_id, "financialized usage cost is unknown")
            continue
        if not str(item.get("currency") or "").strip():
            record_unallocated("usage", usage_id, "financialized usage currency is unknown")
            continue
        if item.get("financial_status") in ("unknown", "unpriced", "difference"):
            record_unallocated("usage", usage_id, "usage cost is open pending reconciliation",
                               amount, non_allocatable=True)
            continue
        charge_id = str(item.get("charge_id") or "").strip()
        if charge_id in conflicting_charges:
            record_unallocated("usage", usage_id, "charge has conflicting amounts",
                               amount, non_allocatable=True)
            continue
        source_ref = str(item.get("source_ref") or "").strip()
        if not source_ref:
            record_unallocated("usage", usage_id, "financialized usage needs source_ref",
                               amount, non_allocatable=True)
            continue
        run_id = str(item.get("run_id") or "").strip()
        run = runs.get(run_id)
        if run is None:
            record_unallocated("usage", usage_id, "run_id is unknown", amount)
            continue
        task_id = str(run.get("task_id") or "").strip()
        if not task_id or task_id not in tasks:
            record_unallocated("usage", usage_id, "run has no known task_id", amount)
            continue
        if item.get("allocatable") is False or item.get("allocation_eligible") is False:
            record_unallocated("usage", usage_id, "row is explicitly non-allocatable",
                               amount, non_allocatable=True)
            continue
        if claim_charge(item, usage_id):
            direct[task_id] += amount

    # Direct and shared cost items. Duplicate financial rows are findings, not sums.
    for index, item in enumerate(cost_items):
        if not isinstance(item, dict):
            raise EconomicsError("cost_items[%d] must be an object" % index)
        cost_id = _text(item.get("cost_id"), "cost_items[%d].cost_id" % index)
        source_ref = str(item.get("source_ref") or "").strip()
        if item.get("amount") in (None, ""):
            record_unallocated("cost_item", cost_id, "cost amount is unknown")
            continue
        amount = _amount(item, "amount", "cost_items[%d]" % index)
        if amount < 0:
            raise EconomicsError("cost item amount cannot be negative: %s" % cost_id)
        if not str(item.get("currency") or "").strip():
            record_unallocated("cost_item", cost_id, "cost currency is unknown")
            continue
        _currency(item, "cost item %s" % cost_id, currency)
        if not source_ref:
            record_unallocated("cost_item", cost_id, "financial source_ref is unknown",
                               amount, non_allocatable=True)
            continue
        charge_id = str(item.get("charge_id") or "").strip()
        if charge_id in conflicting_charges or item.get("financial_status") in ("unknown", "difference"):
            record_unallocated("cost_item", cost_id, "charge has conflicting or open amounts",
                               amount, non_allocatable=True)
            continue
        dedupe = (
            source_ref,
            str(_money(amount)),
            currency,
            str(item.get("period") or "%s..%s" % (period["start"], period["end"])),
            str(item.get("vendor") or ""),
        )
        if cost_id in seen_cost or dedupe in seen_cost:
            findings.append({"code": "duplicate_cost_item", "severity": "review",
                             "id": cost_id, "source_ref": source_ref,
                             "message": "duplicate financial row was not added twice"})
            continue
        seen_cost.add(cost_id)
        seen_cost.add(dedupe)
        if item.get("allocatable") is False or item.get("allocation_eligible") is False:
            record_unallocated("cost_item", cost_id, "row is explicitly non-allocatable",
                               amount, non_allocatable=True)
            continue
        task_id = str(item.get("task_id") or "").strip()
        if task_id:
            if task_id not in tasks:
                record_unallocated("cost_item", cost_id, "unknown task_id %s" % task_id, amount)
                continue
            if claim_charge(item, cost_id):
                direct[task_id] += amount
            continue
        rule_id = str(item.get("allocation_rule") or "").strip()
        if not rule_id or rule_id not in rules:
            record_unallocated("cost_item", cost_id,
                               "shared cost needs a known allocation_rule", amount)
            continue
        driver_error = _driver_error(rules[rule_id])
        if driver_error:
            record_unallocated("cost_item", cost_id, driver_error, amount)
            continue
        if claim_charge(item, cost_id):
            shared_items.append((amount, rule_id, cost_id))

    allocated: Dict[str, Decimal] = {task_id: Decimal("0") for task_id in task_ids}
    allocation_details: List[Dict[str, Any]] = []
    for amount, rule_id, cost_id in shared_items:
        rule = rules[rule_id]
        try:
            shares = _allocate(amount, task_ids, rule)
        except EconomicsError as exc:
            record_unallocated("cost_item", cost_id, str(exc), amount)
            continue
        for task_id, share in shares.items():
            allocated[task_id] += share
        confirmed_by, confirmed_at = _approval(rule)
        allocation_details.append({
            "cost_id": cost_id, "amount": str(_money(amount)),
            "rule_id": rule_id, "rule_version": rule.get("version"),
            "method": rule.get("method"), "driver": rule.get("driver"),
            "confirmed_by": confirmed_by, "confirmed_at": confirmed_at,
            "shares": {k: str(v) for k, v in shares.items()},
        })

    # Outcomes are separate evidence. Buyer verification, not a bare verified
    # flag, controls the success denominator and verified business value.
    outcome_rows: List[Dict[str, Any]] = []
    verified_success = set()
    verified_value: Dict[str, Decimal] = {}
    seen_outcomes = set()
    task_statuses = {}
    outcome_status: Dict[str, Optional[bool]] = {task_id: None for task_id in task_ids}
    denominator_unknown = set(task_ids)
    value_complete = True
    for item in outcomes:
        if isinstance(item, dict) and _buyer_verified(item):
            task_statuses.setdefault(item.get("task_id"), set()).add(item.get("status"))
    for index, item in enumerate(outcomes):
        if not isinstance(item, dict):
            raise EconomicsError("outcomes[%d] must be an object" % index)
        task_id = _text(item.get("task_id"), "outcomes[%d].task_id" % index)
        if task_id not in tasks:
            raise EconomicsError("outcome references unknown task: %s" % task_id)
        status = str(item.get("status") or "")
        source_ref = str(item.get("source_ref") or "").strip()
        identity = (task_id, source_ref, status)
        if source_ref and identity in seen_outcomes:
            findings.append({"code": "duplicate_outcome", "severity": "review",
                             "task_id": task_id, "message": "duplicate outcome excluded"})
            continue
        seen_outcomes.add(identity)
        buyer_verified = _buyer_verified(item)
        conflict = len(task_statuses.get(task_id, set())) > 1
        if conflict:
            buyer_verified = False
            findings.append({"code": "conflicting_outcome", "severity": "review",
                             "task_id": task_id, "message": "conflicting task states require reconciliation"})
        decisive = buyer_verified and not conflict
        if decisive:
            denominator_unknown.discard(task_id)
            outcome_status[task_id] = status == "success"
        if decisive and status == "success":
            verified_success.add(task_id)
        else:
            findings.append({"code": "unverified_outcome", "severity": "review",
                             "task_id": task_id,
                             "message": "outcome is not buyer-verified success; denominator remains unknown"})
        if item.get("verified") is True and not buyer_verified and \
                str(item.get("source_party") or "").lower() == "vendor":
            findings.append({"code": "vendor_outcome_not_independent", "severity": "review",
                             "task_id": task_id,
                             "message": "vendor assertions and repeated vendor exports are not buyer verification"})
        row = {"task_id": task_id, "status": status,
               "verified": True if decisive else None,
               "verification_status": "verified" if decisive else "unknown",
               "success_status": ("success" if outcome_status[task_id] is True else
                                  "not_successful" if outcome_status[task_id] is False else "unknown"),
               "source_ref": source_ref,
               "source_party": item.get("source_party") or "unknown",
               "collection_method": item.get("collection_method") or "unknown",
               "independence": item.get("independence") or "unknown"}
        if item.get("value") is not None:
            value = _dec(item.get("value"), "outcome value %s" % task_id)
            if value < 0:
                raise EconomicsError("outcome value cannot be negative: %s" % task_id)
            currency_known = bool(str(item.get("currency") or "").strip())
            if currency_known and item.get("value_status") != "unknown":
                _currency(item, "outcome %s" % task_id, currency)
            if (currency_known and item.get("value_status") != "unknown"
                    and item.get("value_verified") is True and decisive and status == "success"):
                verified_value[task_id] = verified_value.get(task_id, Decimal("0")) + value
            else:
                value_complete = False
                findings.append({"code": "unverified_value", "severity": "review",
                                 "task_id": task_id,
                                 "message": "business value or currency is not buyer-verified; ROI remains open"})
        elif item.get("value_verified") is True:
            value_complete = False
            findings.append({"code": "unverified_value", "severity": "review",
                             "task_id": task_id,
                             "message": "verified value flag has no amount; value remains unknown"})
        outcome_rows.append(row)

    cost_open_kinds = {
        "missing_amount", "missing_currency", "invalid_amount", "unallocated_cost",
        "charge_amount_mismatch", "invoice_draft", "invoice_pending",
        "unfinancialized_cost", "negative_amount", "currency_mismatch",
        "duplicate_charge", "missing_charge_id",
    }
    case_open_items = _list(case, "open_items")
    case_cost_open = case.get("financial_status") == "open" or any(
        isinstance(item, dict) and item.get("kind") in cost_open_kinds
        and item.get("domain", "finance") == "finance"
        and item.get("status", "open") != "resolved"
        for item in case_open_items)
    financial_candidates = sum(1 for item in usage.values() if item.get("financialized") is True) + len(cost_items)
    cost_complete = bool(financial_candidates) and not unallocated and not coverage_unknown and not case_cost_open

    rows: List[Dict[str, Any]] = []
    total_cost = Decimal("0")
    for task_id, task in tasks.items():
        direct_cost = _money(direct[task_id])
        shared_cost = _money(allocated[task_id])
        total = _money(direct_cost + shared_cost)
        total_cost += total
        success = outcome_status[task_id]
        rows.append({
            "task_id": task_id, "type": task.get("type", ""),
            "direct_cost": str(direct_cost), "allocated_cost": str(shared_cost),
            "fully_loaded_cost": str(total),
            "cost_status": "attributed_subtotal" if not cost_complete else "complete",
            "verified_success": success,
            "success_status": ("success" if success is True else
                               "not_successful" if success is False else "unknown"),
            "verified_value": str(_money(verified_value.get(task_id, Decimal("0"))))
            if task_id in verified_value else None,
        })

    success_count = len(verified_success)
    cost_per_success: Optional[Decimal] = None
    if not cost_complete:
        cost_per_success_reason = "cost is unknown or incomplete; open allocation/reconciliation items block the ratio"
    elif denominator_unknown:
        cost_per_success_reason = "buyer outcome evidence is missing or conflicting; success denominator is unknown"
    elif not success_count:
        cost_per_success_reason = "no buyer-verified success; no ratio is defined"
    else:
        cost_per_success = _money(total_cost / Decimal(success_count))
        cost_per_success_reason = "complete attributed cost divided by buyer-verified successful tasks"

    verified_value_total = sum(verified_value.values(), Decimal("0"))
    roi = None
    if not cost_complete:
        roi_reason = "cost is unknown or incomplete; reconcile allocation before ROI"
    elif not value_complete:
        roi_reason = "business value evidence is incomplete; unverified values never fill the gap"
    elif not verified_value:
        roi_reason = "no verified business value in the case currency"
    elif total_cost <= 0:
        roi_reason = "ROI denominator requires a positive complete cost"
    else:
        roi = str(((verified_value_total - total_cost) / total_cost).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP))
        roi_reason = "verified value and fully loaded cost in the same currency/period"

    loss_total = Decimal("0")
    loss_complete = True
    seen_losses = set()
    loss_rows = []
    for index, item in enumerate(loss_events):
        if not isinstance(item, dict):
            raise EconomicsError("loss_events[%d] must be an object" % index)
        amount: Optional[Decimal] = None
        if item.get("amount") not in (None, "") and str(item.get("currency") or "").strip():
            _currency(item, "loss event %d" % index, currency)
            amount = _dec(item.get("amount"), "loss amount")
            if amount < 0:
                raise EconomicsError("loss amount cannot be negative")
        else:
            if item.get("status") == "confirmed":
                loss_complete = False
            findings.append({"code": "unknown_loss_amount", "severity": "review",
                             "message": "confirmed loss amount/currency is open, never zero"})
        identity = item.get("loss_id") or item.get("source_ref")
        if (amount is not None and identity and identity not in seen_losses
                and item.get("status") == "confirmed"
                and item.get("source_ref") and item.get("task_id") in tasks):
            loss_total += amount
            seen_losses.add(identity)
        else:
            findings.append({"code": "unconfirmed_or_duplicate_loss", "severity": "review",
                             "message": "loss excluded from confirmed loss total"})
        loss_rows.append({
            "loss_id": item.get("loss_id", ""), "task_id": item.get("task_id", ""),
            "amount": str(_money(amount)) if amount is not None else None,
            "currency": item.get("currency") or None,
            "status": item.get("status", "unknown"), "source_ref": item.get("source_ref", ""),
        })

    coverage_amounts_known = not coverage_unknown and not case_cost_open
    allocatable = total_cost + unallocated_amount + non_allocatable_amount
    allocation_coverage = [{
        "currency": currency,
        "status": "open" if unallocated or not coverage_amounts_known else "complete",
        "allocatable_amount": str(_money(allocatable)) if coverage_amounts_known else None,
        "allocated_amount": str(_money(total_cost)) if coverage_amounts_known else None,
        "unallocated_amount": str(_money(unallocated_amount)) if coverage_amounts_known else None,
        "non_allocatable_amount": str(_money(non_allocatable_amount)) if coverage_amounts_known else None,
        "open_items": len(unallocated) + (1 if case_cost_open else 0),
        "identity": "allocatable = allocated + unallocated + non_allocatable",
    }]
    reported_success_count: Optional[int] = (
        None if denominator_unknown and not success_count else success_count)
    coverage = {
        "tasks": len(tasks), "runs": len(runs), "usage": len(usage),
        "financial_documents": len(documents), "cost_items": len(cost_items),
        "outcomes": len(outcomes),
        "verified_success_tasks": reported_success_count,
        "outcome_unknown_tasks": len(denominator_unknown),
        "unallocated_items": len(unallocated),
    }
    result = {
        "schema_version": "1.0", "kind": "verovo.task-economics",
        "case_id": case_id, "currency": currency, "period": period,
        "coverage": coverage,
        "totals": {
            "cost_status": "complete" if cost_complete else "unknown",
            "attributed_cost_subtotal": str(_money(total_cost)),
            "fully_loaded_cost": str(_money(total_cost)) if cost_complete else None,
            "verified_success_tasks": reported_success_count,
            "success_denominator_status": "unknown" if denominator_unknown else "complete",
            "cost_per_verified_success": str(cost_per_success) if cost_per_success is not None else None,
            "cost_per_verified_success_reason": cost_per_success_reason,
            "verified_business_value": str(_money(verified_value_total)) if verified_value else None,
            "roi": roi, "roi_reason": roi_reason,
            "loss_events": str(_money(loss_total)) if loss_complete else None,
        },
        "tasks": rows,
        "allocation": allocation_details,
        "allocation_coverage": allocation_coverage,
        "unallocated": unallocated,
        "findings": findings,
        "outcomes": outcome_rows,
        "loss_events": loss_rows,
        "boundary": (
            "Local deterministic calculation only. Missing amount/currency/quantity and "
            "unallocated costs remain open, never zero. Buyer-verified outcomes control "
            "Cost per Verified Success; ROI is a separate verified-value calculation. "
            "Vendor exports are not independent buyer evidence."
        ),
    }
    return result


def load_case(path: str) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise EconomicsError("case file must contain an object")
    return data


def report_text(result: Dict[str, Any]) -> str:
    total = result["totals"]
    lines = [
        "Verovo task economics",
        "case: %s | currency: %s | period: %s..%s" % (
            result["case_id"], result["currency"],
            result["period"].get("start"), result["period"].get("end")),
        "",
        "Coverage: %s" % json.dumps(result["coverage"], ensure_ascii=False, sort_keys=True),
        "Fully loaded cost: %s" % (
            ("%s %s" % (result["currency"], total["fully_loaded_cost"]))
            if total["fully_loaded_cost"] is not None else "unknown (open financial items)"),
        "Attributed cost subtotal: %s %s" % (result["currency"], total["attributed_cost_subtotal"]),
        "Verified successful tasks: %s" % total["verified_success_tasks"],
        "Cost per verified success: %s" % (
            ("%s %s" % (result["currency"], total["cost_per_verified_success"]))
            if total["cost_per_verified_success"] is not None
            else "unknown (%s)" % total["cost_per_verified_success_reason"]),
        "ROI: %s" % (total["roi"] if total["roi"] is not None else "null (%s)" % total["roi_reason"]),
        "Loss events: %s %s" % (result["currency"], total["loss_events"]),
        "",
        "Tasks:",
        "task_id\ttype\tdirect\tallocated\tfully_loaded\tverified_success\tverified_value",
    ]
    for row in result["tasks"]:
        lines.append("%s\t%s\t%s\t%s\t%s\t%s\t%s" % (
            row["task_id"], row["type"], row["direct_cost"], row["allocated_cost"],
            row["fully_loaded_cost"], row["verified_success"],
            row["verified_value"] if row["verified_value"] is not None else "unknown"))
    if result["unallocated"]:
        lines += ["", "Unallocated (not zero):"]
        for row in result["unallocated"]:
            lines.append("- %s %s: %s" % (row["kind"], row["id"], row["reason"]))
    if result["findings"]:
        lines += ["", "Findings:"]
        for row in result["findings"]:
            lines.append("- [%s] %s" % (row["code"], row["message"]))
    lines += ["", result["boundary"]]
    return "\n".join(lines)


def report_markdown(result: Dict[str, Any]) -> str:
    total = result["totals"]
    lines = [
        "# Task economics — %s" % result["case_id"], "",
        "- Currency: `%s`" % result["currency"],
        "- Period: `%s..%s`" % (result["period"].get("start"), result["period"].get("end")),
        "- Fully loaded cost: **%s**" % (
            ("%s %s" % (result["currency"], total["fully_loaded_cost"]))
            if total["fully_loaded_cost"] is not None else "unknown"),
        "- Attributed cost subtotal: **%s %s**" % (result["currency"], total["attributed_cost_subtotal"]),
        "- Verified success tasks: **%s**" % total["verified_success_tasks"],
        "- Cost per verified success: **%s**" % (
            ("%s %s" % (result["currency"], total["cost_per_verified_success"]))
            if total["cost_per_verified_success"] is not None else "unknown"),
        "- ROI: **%s**" % (total["roi"] if total["roi"] is not None else "null"),
        "- Loss events: **%s %s**" % (result["currency"], total["loss_events"]),
        "", "## Coverage", "", "| Item | Count |", "|---|---|",
    ]
    for key, value in result["coverage"].items():
        lines.append("| %s | %s |" % (key, value))
    lines += ["", "## Tasks", "",
              "| Task | Type | Direct | Allocated | Fully loaded | Verified success | Verified value |",
              "|---|---|---|---|---|---|---|"]
    for row in result["tasks"]:
        lines.append("| {task} | {type} | {direct} | {alloc} | {full} | {ok} | {value} |".format(
            task=row["task_id"], type=row["type"], direct=row["direct_cost"],
            alloc=row["allocated_cost"], full=row["fully_loaded_cost"],
            ok=row["verified_success"], value=row["verified_value"] or "unknown"))
    if result["unallocated"]:
        lines += ["", "## Unallocated (not zero)", ""]
        for row in result["unallocated"]:
            lines.append("- `%s/%s`: %s" % (row["kind"], row["id"], row["reason"]))
    if result["findings"]:
        lines += ["", "## Findings", ""]
        for row in result["findings"]:
            lines.append("- `%s`: %s" % (row["code"], row["message"]))
    lines += ["", "## Boundary", "", result["boundary"], ""]
    return "\n".join(lines)
