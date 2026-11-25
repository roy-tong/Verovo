"""Descriptive human-versus-agent task economics with fully loaded labor cost."""
from __future__ import annotations

import json
from decimal import Decimal

from . import optimization_review, tool_value

CONTRACT_SCHEMA = "verovo.human-baseline/1"
RESULT_SCHEMA = "verovo.human-baseline-result/1"


def _money(value):
    return optimization_review._money(value) if value is not None else None


def _load(path, arm, spec):
    hourly = optimization_review._dec(spec["human_hourly_cost"], "human_hourly_cost")
    if hourly < 0:
        raise ValueError("human_hourly_cost must be non-negative")
    rows = []
    seen = set()
    for index, raw in enumerate(tool_value._rows(path), start=1):
        if str(raw.get("group") or "").strip().lower() != arm:
            raise ValueError("%s file row %d has a different arm" % (arm, index))
        task_id = str(raw.get("task_id") or "").strip()
        if not task_id or task_id in seen:
            raise ValueError("task_id must be non-empty and unique within an arm")
        seen.add(task_id)
        if str(raw.get("task_type") or "") != spec["task_type"]:
            raise ValueError("task_type differs from the human-baseline contract")
        if str(raw.get("currency") or "").upper() != spec["currency"].upper():
            raise ValueError("task currency differs from the human-baseline contract")
        direct = tool_value._dec(raw.get("cost"))
        minutes = tool_value._dec(raw.get("human_minutes"))
        if direct is None or direct < 0 or minutes is None or minutes < 0:
            raise ValueError("cost and human_minutes must be finite and non-negative")
        rows.append({
            "task_id": task_id,
            "success": tool_value._bool(raw.get("success")),
            "direct_cost": direct,
            "human_minutes": minutes,
            "fully_loaded_cost": direct + minutes * hourly / Decimal("60"),
            "comparable_group": str(raw.get("comparable_group") or "").strip(),
        })
    return rows


def _arm(rows):
    n = len(rows)
    successes = sum(row["success"] for row in rows)
    direct = sum((row["direct_cost"] for row in rows), Decimal("0"))
    loaded = sum((row["fully_loaded_cost"] for row in rows), Decimal("0"))
    minutes = sum((row["human_minutes"] for row in rows), Decimal("0"))
    return {
        "tasks": n, "successes": successes,
        "success_rate": round(successes / n, 6) if n else None,
        "direct_cost_per_task": _money(direct / n) if n else None,
        "human_minutes_per_task": _money(minutes / n) if n else None,
        "fully_loaded_cost_per_task": _money(loaded / n) if n else None,
        "cost_per_verified_success": _money(loaded / successes) if successes else None,
    }


def _validate_contract(spec):
    if not isinstance(spec, dict) or spec.get("schema") != CONTRACT_SCHEMA:
        raise ValueError("human baseline schema must be %s" % CONTRACT_SCHEMA)
    for key in ("comparison_id", "task_type", "acceptance_rubric_ref", "currency",
                "human_cost_basis"):
        if not spec.get(key):
            raise ValueError("human baseline.%s is required" % key)
    minimum = int(spec.get("min_tasks_per_arm", 1))
    if minimum <= 0:
        raise ValueError("min_tasks_per_arm must be positive")
    basis = spec["human_cost_basis"]
    if not isinstance(basis, dict) or not basis.get("source_ref") or not basis.get("method") \
            or basis.get("includes_loaded_labor") is not True:
        raise ValueError("human_cost_basis needs source_ref, method and includes_loaded_labor=true")
    comparability = spec.get("comparability") if isinstance(spec.get("comparability"), dict) else {}
    if any(comparability.get(key) is not True for key in
           ("same_task_set", "same_acceptance_rubric", "same_currency", "same_quality_requirements")):
        raise ValueError("human baseline requires same tasks, acceptance rubric, currency and quality requirements")
    drop = optimization_review._dec(
        (spec.get("quality_guardrail") or {}).get("max_success_rate_drop"),
        "max_success_rate_drop")
    if drop < 0 or drop > 1:
        raise ValueError("max_success_rate_drop must be between 0 and 1")
    optimization_review._dec(spec.get("human_hourly_cost"), "human_hourly_cost")
    return minimum, drop


def evaluate(human_tasks, agent_tasks, spec):
    minimum, max_drop = _validate_contract(spec)
    human_rows = _load_tasks_or_rows(human_tasks, "human", spec)
    agent_rows = _load_tasks_or_rows(agent_tasks, "agent", spec)
    human_groups = {row["comparable_group"] for row in human_rows}
    agent_groups = {row["comparable_group"] for row in agent_rows}
    if len(human_groups) != 1 or len(agent_groups) != 1 or not next(iter(human_groups), "") \
            or human_groups != agent_groups:
        raise ValueError("same_task_set requires one matching non-empty comparable_group")
    human = _arm(human_rows)
    agent = _arm(agent_rows)
    task_reduction = None
    success_reduction = None
    if human["fully_loaded_cost_per_task"] is not None and agent["fully_loaded_cost_per_task"] is not None:
        task_reduction = Decimal(human["fully_loaded_cost_per_task"]) - Decimal(agent["fully_loaded_cost_per_task"])
    if human["cost_per_verified_success"] is not None and agent["cost_per_verified_success"] is not None:
        success_reduction = Decimal(human["cost_per_verified_success"]) - Decimal(agent["cost_per_verified_success"])
    minutes_reduction = None
    if human["human_minutes_per_task"] is not None and agent["human_minutes_per_task"] is not None:
        minutes_reduction = Decimal(human["human_minutes_per_task"]) - Decimal(agent["human_minutes_per_task"])
    rate_change = None
    if human["success_rate"] is not None and agent["success_rate"] is not None:
        rate_change = Decimal(str(agent["success_rate"])) - Decimal(str(human["success_rate"]))
    reasons = []
    sufficient = human["tasks"] >= minimum and agent["tasks"] >= minimum
    if not sufficient:
        reasons.append("INSUFFICIENT_TASKS")
    if success_reduction is None:
        reasons.append("COST_PER_SUCCESS_UNKNOWN")
    quality_breach = rate_change is not None and -rate_change > max_drop
    if quality_breach:
        reasons.append("QUALITY_GUARDRAIL_BREACHED")
    if not quality_breach and sufficient and task_reduction is not None and task_reduction > 0 \
            and success_reduction is not None and success_reduction >= 0:
        decision = "lower_observed_cost"
        reasons.append("LOWER_FULLY_LOADED_COST_WITHIN_QUALITY_GUARDRAIL")
    elif not quality_breach and sufficient and task_reduction is not None and task_reduction < 0:
        decision = "higher_observed_cost"
        reasons.append("HIGHER_FULLY_LOADED_COST")
    else:
        decision = "inconclusive"
        if not reasons:
            reasons.append("REVIEW_REQUIRED")
    return {
        "schema": RESULT_SCHEMA, "comparison_id": spec["comparison_id"],
        "decision": decision, "reason_codes": reasons,
        "human": human, "agent": agent,
        "observed_change": {
            "cost_per_task_reduction": _money(task_reduction),
            "cost_per_success_reduction": _money(success_reduction),
            "success_rate_change": round(float(rate_change), 6) if rate_change is not None else None,
            "human_minutes_reduction_per_task": (int(minutes_reduction)
                if minutes_reduction is not None and minutes_reduction == int(minutes_reduction)
                else float(minutes_reduction) if minutes_reduction is not None else None),
        },
        "coverage": {
            "min_tasks_per_arm": minimum, "comparable_group": sorted(human_groups)[0],
            "acceptance_rubric_ref": spec["acceptance_rubric_ref"], "currency": spec["currency"],
        },
        "human_cost_basis": spec["human_cost_basis"],
        "human_hourly_cost": str(spec["human_hourly_cost"]),
        "comparability": spec["comparability"],
        "quality_guardrail": spec["quality_guardrail"],
        "causality": "not_claimed", "saving_verification": "not_reconciled",
        "boundary": "Fully loaded observed task costs include direct cost and human minutes at the declared rate. Descriptive differences are not causal, realized invoice savings or a procurement decision.",
    }


def _load_tasks_or_rows(value, arm, spec):
    if isinstance(value, (str, bytes)) or hasattr(value, "__fspath__"):
        return _load(value, arm, spec)
    rows = []
    for row in value:
        if not isinstance(row, dict) or row.get("group") != arm:
            raise ValueError("in-memory task rows need group=%s" % arm)
        rows.append(dict(row))
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "tasks.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return _load(path, arm, spec)


def render(payload):
    return "\n".join([
        "人工对照 %s：%s" % (payload["comparison_id"], payload["decision"].upper()),
        "人工/Agent 每任务完全成本：%s → %s；每次成功成本下降：%s。" % (
            payload["human"]["fully_loaded_cost_per_task"],
            payload["agent"]["fully_loaded_cost_per_task"],
            payload["observed_change"]["cost_per_success_reduction"]),
        "原因：%s" % ", ".join(payload["reason_codes"]),
        "这是描述性对照；未证明因果、实际账单节省或采购结论。",
    ]) + "\n"
