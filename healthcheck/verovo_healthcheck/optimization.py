"""ROI-driven scenario simulation and optimization proposals.

Two things, kept deliberately separate:

1. **Simulation** applies explicit assumptions to a copy of the case (scale a
   vendor's cost, drop a bucket) and re-runs the deterministic economics engine.
   The result is a *conditional* number: "if this assumption held, cost would
   move by X" — never a forecast, saving or causal claim.
2. **Proposals** read the cost drivers and the baseline economics and suggest
   what to verify or test next. Every proposal carries its evidence, a
   verification plan and ``automatic_apply: false``.
"""
from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from . import cost_drivers, task_economics


def _dec(value: Any) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")
    return number if number.is_finite() else Decimal("0")


def _money(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(_dec(value).quantize(Decimal("0.01")))


def _task_type(item: Dict[str, Any], tasks: Dict[str, Dict[str, Any]],
               runs: Dict[str, Dict[str, Any]]) -> str:
    task_id = str(item.get("task_id") or "")
    if not task_id and item.get("run_id"):
        task_id = str(runs.get(str(item["run_id"]), {}).get("task_id") or "")
    return str(tasks.get(task_id, {}).get("type") or "")


def _retry_bucket(item: Dict[str, Any]) -> str:
    if item.get("retry") is True or str(item.get("attempt") or "") not in ("", "1"):
        return "retry"
    if item.get("retry") is False or str(item.get("attempt") or "") == "1":
        return "first-attempt"
    return "not-separable"


def _matches(item: Dict[str, Any], match: Dict[str, Any], tasks, runs) -> bool:
    for key, expected in (match or {}).items():
        if key == "task_type":
            actual = _task_type(item, tasks, runs)
        elif key == "retry":
            actual = _retry_bucket(item)
        else:
            actual = str(item.get(key) or "")
        if str(expected) != actual:
            return False
    return True


def simulate(case: Dict[str, Any], scenario: Dict[str, Any]) -> Dict[str, Any]:
    """Apply one scenario's rules to a case copy and re-run economics."""
    scenario_id = str(scenario.get("id") or "scenario")
    rules = scenario.get("rules")
    if not isinstance(rules, list) or not rules:
        return {"scenario_id": scenario_id, "status": "invalid",
                "reason": "scenario needs a non-empty rules array"}
    clone = copy.deepcopy(case)
    tasks = {str(item.get("task_id")): item for item in clone.get("tasks") or []
             if isinstance(item, dict)}
    runs = {str(item.get("run_id")): item for item in clone.get("runs") or []
            if isinstance(item, dict)}
    applied = 0
    for rule in rules:
        if not isinstance(rule, dict):
            return {"scenario_id": scenario_id, "status": "invalid",
                    "reason": "each rule must be an object"}
        action = str(rule.get("action") or "")
        match = rule.get("match") if isinstance(rule.get("match"), dict) else {}
        if action not in ("scale_cost", "drop"):
            return {"scenario_id": scenario_id, "status": "invalid",
                    "reason": "unsupported action %r" % action}
        factor = _dec(rule.get("factor", 1))
        if action == "scale_cost" and (factor < 0 or factor > 10):
            return {"scenario_id": scenario_id, "status": "invalid",
                    "reason": "factor must be 0..10"}
        targets = []
        for key, field in (("cost_items", "amount"), ("usage", "cost")):
            for item in clone.get(key) or []:
                if not isinstance(item, dict):
                    continue
                if key == "usage" and item.get("financialized") is not True:
                    continue
                if _matches(item, match, tasks, runs):
                    targets.append((item, field))
        if action == "drop":
            for item, field in targets:
                item[field] = "0"
        else:
            for item, field in targets:
                item[field] = str(_dec(item.get(field)) * factor)
        applied += len(targets)
    try:
        evaluated = task_economics.evaluate(clone)
        baseline = task_economics.evaluate(case)
    except (task_economics.EconomicsError, TypeError, KeyError) as exc:
        return {"scenario_id": scenario_id, "status": "invalid", "reason": str(exc)}
    before = _dec((baseline.get("totals") or {}).get("fully_loaded_cost"))
    after = _dec((evaluated.get("totals") or {}).get("fully_loaded_cost"))
    delta = after - before
    return {
        "scenario_id": scenario_id,
        "description": str(scenario.get("description") or ""),
        "status": "computed",
        "applied_rows": applied,
        "assumptions": rules,
        "baseline": {
            "fully_loaded_cost": _money(before),
            "cost_per_verified_success": (baseline.get("totals") or {}).get("cost_per_verified_success"),
            "roi": (baseline.get("totals") or {}).get("roi"),
        },
        "scenario": {
            "fully_loaded_cost": _money(after),
            "cost_per_verified_success": (evaluated.get("totals") or {}).get("cost_per_verified_success"),
            "roi": (evaluated.get("totals") or {}).get("roi"),
        },
        "delta": {
            "fully_loaded_cost": _money(delta),
            "fully_loaded_cost_pct": round(float(delta / before), 4) if before else None,
            "verified_success_tasks": (evaluated.get("totals") or {}).get("verified_success_tasks"),
        },
        "boundary": ("模拟只重算显式假设下的成本；假设不成立时结果不成立，"
                     "这不是预测、节省承诺或因果证据。"),
    }


def _proposal(proposal_id: str, severity: str, statement: str, evidence: Dict[str, Any],
              next_step: str, verification: Dict[str, Any], *,
              kind: str = "test") -> Dict[str, Any]:
    return {
        "id": proposal_id,
        "kind": kind,
        "severity": severity,
        "statement": statement,
        "evidence": evidence,
        "next_step": next_step,
        "verification_plan": verification,
        "requires_human": True,
        "automatic_apply": False,
    }


def propose(case: Dict[str, Any], *, scenarios: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    drivers = cost_drivers.analyze(case)
    try:
        baseline = task_economics.evaluate(case)
    except (task_economics.EconomicsError, TypeError, KeyError) as exc:
        return {"schema_version": "1.0", "kind": "verovo.optimization",
                "status": "invalid", "reason": str(exc), "proposals": []}
    totals = baseline.get("totals") or {}
    proposals: List[Dict[str, Any]] = []

    unallocated = baseline.get("unallocated") or []
    if unallocated:
        proposals.append(_proposal(
            "reconcile-unallocated", "high",
            f"{len(unallocated)} 条成本未能归属到任务；在此之前 ROI 不可用",
            {"unallocated": len(unallocated), "samples": unallocated[:5]},
            "补齐 task_id 或 allocation_rule，再重新计算",
            {"metric": "unallocated_items", "target": 0},
            kind="data"))

    if not totals.get("verified_success_tasks"):
        proposals.append(_proposal(
            "verify-outcomes", "high",
            "没有已验证的成功任务；成本每成功和 ROI 都没有分母",
            {"verified_success_tasks": totals.get("verified_success_tasks")},
            "接入可核验的业务结果（CRM/工单/订单），并保留 source_ref",
            {"metric": "verified_success_tasks", "minimum": 1},
            kind="data"))

    vendor_rows = [item for item in drivers["drivers"] if item["dimension"] == "vendor"]
    vendor_rows = [item for item in vendor_rows if item["key"] != "not-recorded"]
    if vendor_rows and float(vendor_rows[0]["share"]) >= 0.4:
        top = vendor_rows[0]
        proposals.append(_proposal(
            f"vendor-concentration:{top['key']}", "medium",
            f"{top['key']} 占总成本 {top['share']:.0%}；先验证可替代性，不要直接切换",
            {"vendor": top["key"], "cost": top["cost"], "share": top["share"]},
            "选一个低风险任务样本，用同一质量门槛比较替代供应商",
            {"metric": "cost_per_verified_success", "baseline": totals.get("cost_per_verified_success"),
             "minimum_tasks": 5, "quality_gate": "verified success rate must not fall"},
            kind="experiment"))

    retry_rows = [item for item in drivers["drivers"] if item["dimension"] == "retry"]
    if any(item["key"] == "not-separable" and float(item["share"]) > 0 for item in retry_rows):
        proposals.append(_proposal(
            "instrument-retries", "low",
            "重试成本与首次尝试尚未分开；无法判断重试是不是主要成本驱动",
            {"retry": retry_rows},
            "在 usage 行记录 attempt/retry，再比较 first-attempt 与 retry 成本",
            {"metric": "retry_share_of_cost", "target": "measurable"},
            kind="data"))

    allocation_rows = [item for item in drivers["drivers"] if item["dimension"] == "allocation"]
    shared_share = sum(float(item["share"]) for item in allocation_rows
                       if item["key"].startswith("shared:") and item["key"] != "shared:unallocated")
    if shared_share >= 0.4:
        proposals.append(_proposal(
            "review-allocation", "medium",
            f"共享成本占 {shared_share:.0%}；分摊规则本身会改变单任务成本",
            {"shared_share": round(shared_share, 4), "rules": allocation_rows},
            "复核分摊规则与版本，比较 equal/headcount/revenue 的敏感性",
            {"metric": "allocation_rule_version", "change_requires": "documented rationale"},
            kind="review"))

    simulations = []
    for scenario in (scenarios or {}).get("scenarios") or []:
        if isinstance(scenario, dict):
            simulations.append(simulate(case, scenario))
    for item in simulations:
        if item.get("status") != "computed":
            continue
        proposals.append(_proposal(
            f"scenario:{item['scenario_id']}", "low",
            f"假设「{item.get('description') or item['scenario_id']}」下成本变化 "
            f"{item['delta']['fully_loaded_cost']}（{item['delta']['fully_loaded_cost_pct']}）",
            {"baseline": item["baseline"], "scenario": item["scenario"],
             "assumptions": item["assumptions"]},
            "把该假设变成可验证实验，而不是直接改预算",
            {"metric": "cost_per_verified_success", "requires": "quality parity"},
            kind="simulation"))

    proposals.sort(key=lambda item: ({"high": 0, "medium": 1, "low": 2}.get(item["severity"], 9),
                                     item["id"]))
    return {
        "schema_version": "1.0",
        "kind": "verovo.optimization",
        "status": "ok",
        "case_id": str(case.get("case_id") or ""),
        "baseline": totals,
        "drivers": drivers,
        "simulations": simulations,
        "proposals": proposals,
        "boundary": ("建议来自已登记成本与显式假设；不承诺节省、不证明因果，"
                     "全部需要人工决定并经过验证计划。"),
    }


def markdown(payload: Dict[str, Any]) -> str:
    if payload.get("status") != "ok":
        return "# ROI 优化建议\n\n- 无法计算：%s\n" % payload.get("reason")
    lines = [
        "# ROI 优化建议",
        "",
        f"- case：{payload['case_id']}",
        f"- 基线：总成本 {payload['baseline'].get('fully_loaded_cost')}；"
        f"每成功 {payload['baseline'].get('cost_per_verified_success')}；"
        f"ROI {payload['baseline'].get('roi')}",
        "",
        "## 建议",
        "",
    ]
    for proposal in payload["proposals"]:
        lines.append(f"### [{proposal['severity']}] {proposal['statement']}")
        lines.append(f"- 证据：`{proposal['evidence']}`")
        lines.append(f"- 下一步：{proposal['next_step']}")
        lines.append(f"- 验证计划：`{proposal['verification_plan']}`")
        lines.append("- 需要人工决定；不会自动执行。")
        lines.append("")
    if not payload["proposals"]:
        lines.append("- 没有可提出的建议。")
        lines.append("")
    lines.extend(["## 边界", "", payload["boundary"], ""])
    return "\n".join(lines)
