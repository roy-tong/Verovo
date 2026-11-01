"""Cost drivers: where the money actually goes inside one case.

The economics engine answers "what did this task cost". This module answers the
next question without pretending to know more: which vendor, task type, model,
allocation rule and retry bucket the cost sits in, and which parts cannot be
attributed from the current records.

Everything is derived from the same case file; nothing is estimated, and a
missing dimension is reported as ``not-recorded`` rather than guessed.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from . import task_economics

DIMENSIONS = ("vendor", "category", "task_type", "model", "retry", "allocation")


def _dec(value: Any) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")
    return number if number.is_finite() else Decimal("0")


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _tasks(case: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(item.get("task_id")): item for item in case.get("tasks") or []
            if isinstance(item, dict) and item.get("task_id")}


def _runs(case: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(item.get("run_id")): item for item in case.get("runs") or []
            if isinstance(item, dict) and item.get("run_id")}


def _add(buckets: Dict[str, Decimal], key: str, amount: Decimal) -> None:
    buckets[key] = buckets.get(key, Decimal("0")) + amount


def analyze(case: Dict[str, Any]) -> Dict[str, Any]:
    """Return cost buckets per dimension plus explicit attribution gaps."""
    if not isinstance(case, dict):
        raise ValueError("case must be an object")
    currency = str(case.get("currency") or "").upper()
    tasks = _tasks(case)
    runs = _runs(case)
    buckets: Dict[str, Dict[str, Decimal]] = {name: {} for name in DIMENSIONS}
    total = Decimal("0")
    unattributed = Decimal("0")
    rule_ids = {str(rule.get("id")) for rule in case.get("allocation_rules") or []
                if isinstance(rule, dict)}
    unknowns: List[str] = []

    for index, item in enumerate(case.get("cost_items") or []):
        if not isinstance(item, dict):
            continue
        amount = _dec(item.get("amount"))
        if amount <= 0:
            continue
        total += amount
        task_type = "shared"
        task_id = str(item.get("task_id") or "")
        if task_id:
            task_type = str(tasks.get(task_id, {}).get("type") or "unknown")
        _add(buckets["vendor"], str(item.get("vendor") or "not-recorded"), amount)
        _add(buckets["category"], str(item.get("category") or "not-recorded"), amount)
        _add(buckets["task_type"], task_type, amount)
        _add(buckets["model"], str(item.get("model") or "not-recorded"), amount)
        _add(buckets["retry"], "not-separable", amount)
        _add(buckets["allocation"],
             "shared:" + str(item.get("allocation_rule") or "unallocated")
             if not task_id else "direct", amount)
        rule_id = str(item.get("allocation_rule") or "")
        if not task_id and rule_id not in rule_ids:
            unattributed += amount

    for item in case.get("usage") or []:
        if not isinstance(item, dict) or item.get("financialized") is not True:
            continue
        amount = _dec(item.get("cost"))
        if amount <= 0:
            continue
        total += amount
        run = runs.get(str(item.get("run_id") or ""), {})
        task_id = str(run.get("task_id") or "")
        task_type = str(tasks.get(task_id, {}).get("type") or "unknown") if task_id else "unlinked"
        _add(buckets["vendor"], str(item.get("vendor") or "not-recorded"), amount)
        _add(buckets["category"], str(item.get("category") or "usage"), amount)
        _add(buckets["task_type"], task_type, amount)
        _add(buckets["model"], str(item.get("model") or "not-recorded"), amount)
        retry = item.get("retry")
        if retry is True or str(item.get("attempt") or "") not in ("", "1"):
            _add(buckets["retry"], "retry", amount)
        elif retry is False or str(item.get("attempt") or "") == "1":
            _add(buckets["retry"], "first-attempt", amount)
        else:
            _add(buckets["retry"], "not-separable", amount)
        _add(buckets["allocation"], "direct" if task_id else "unlinked", amount)
        if not task_id or task_id not in tasks:
            unattributed += amount

    drivers: List[Dict[str, Any]] = []
    for dimension in DIMENSIONS:
        for key, amount in sorted(buckets[dimension].items(), key=lambda pair: (-pair[1], pair[0])):
            drivers.append({
                "dimension": dimension,
                "key": key,
                "cost": _money(amount),
                "share": round(float(amount / total), 4) if total else 0.0,
            })

    if total and any(key == "not-recorded" for key in buckets["vendor"]):
        unknowns.append("some costs carry no vendor; vendor mix is incomplete")
    if any(key == "not-recordable" for key in buckets["retry"]):
        unknowns.append("retry cost cannot be separated for part of the period")
    linked_costs = sum((_dec(item.get("amount")) for item in case.get("cost_items") or []
                        if isinstance(item, dict) and item.get("task_id")), Decimal("0"))
    if total and linked_costs + sum(
            (_dec(item.get("cost")) for item in case.get("usage") or []
             if isinstance(item, dict) and item.get("financialized") is True), Decimal("0")) < total:
        unknowns.append("some cost rows are not linked to a task")

    top = drivers[0] if drivers else None
    risks: List[Dict[str, Any]] = []
    if top and top["share"] >= 0.5 and total > 0:
        risks.append({"id": "concentration", "severity": "medium",
                      "detail": f"{top['dimension']} 的 {top['key']} 占成本 {top['share']:.0%}；"
                                "集中度是描述，不是优化理由"})
    if any(item["dimension"] == "retry" and item["key"] == "not-separable" and float(item["share"]) > 0
           for item in drivers):
        risks.append({"id": "retry_unobserved", "severity": "low",
                      "detail": "无法把重试成本与首次尝试分开；先记录 attempt/retry 再谈节省"})

    baseline: Dict[str, Any] = {}
    try:
        baseline = task_economics.evaluate(case)
    except (task_economics.EconomicsError, KeyError, TypeError) as exc:
        unknowns.append("economics evaluation failed: %s" % exc)

    return {
        "schema_version": "1.0",
        "kind": "verovo.cost-drivers",
        "case_id": str(case.get("case_id") or ""),
        "currency": currency,
        "total_recorded_cost": _money(total),
        "attributable_cost": _money(total - unattributed),
        "unattributed_cost": _money(unattributed),
        "drivers": drivers,
        "risks": risks,
        "unknowns": unknowns,
        "economics": {
            "fully_loaded_cost": (baseline.get("totals") or {}).get("fully_loaded_cost"),
            "verified_success_tasks": (baseline.get("totals") or {}).get("verified_success_tasks"),
            "cost_per_verified_success": (baseline.get("totals") or {}).get("cost_per_verified_success"),
            "roi": (baseline.get("totals") or {}).get("roi"),
            "roi_reason": (baseline.get("totals") or {}).get("roi_reason"),
        } if baseline else {},
        "boundary": ("成本分桶只重排已经登记的行；not-recorded 与 unlinked 不会被摊到别处，"
                     "占比不是节省潜力，也不是因果解释。"),
    }


def markdown(payload: Dict[str, Any]) -> str:
    lines = [
        "# 成本驱动分析",
        "",
        f"- case：{payload['case_id']}；币种：{payload['currency']}；"
        f"登记成本：{payload['total_recorded_cost']}；可归属：{payload['attributable_cost']}；"
        f"未归属：{payload['unattributed_cost']}",
        "",
    ]
    for dimension in DIMENSIONS:
        rows = [item for item in payload["drivers"] if item["dimension"] == dimension]
        if not rows:
            continue
        lines.append(f"## {dimension}")
        lines.append("")
        lines.extend(f"- {item['key']}：{item['cost']}（{item['share']:.0%}）" for item in rows[:8])
        lines.append("")
    lines.extend(["## 风险", ""])
    lines.extend(f"- [{item['severity']}] {item['detail']}" for item in payload["risks"]) if payload["risks"] \
        else lines.append("- 无")
    lines.extend(["", "## 未知", ""])
    lines.extend(f"- {item}" for item in payload["unknowns"]) if payload["unknowns"] else lines.append("- 无")
    lines.extend(["", "## 边界", "", payload["boundary"], ""])
    return "\n".join(lines)
