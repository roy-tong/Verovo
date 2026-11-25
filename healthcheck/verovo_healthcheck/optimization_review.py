"""Read-only review of observed cost and quality after an optimization action."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from . import tool_value

CONTRACT_SCHEMA = "verovo.optimization-review/1"
RESULT_SCHEMA = "verovo.optimization-review-result/1"


def _dec(value, label="value"):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("%s must be a finite decimal" % label) from exc
    if not number.is_finite():
        raise ValueError("%s must be a finite decimal" % label)
    return number


def _money(value):
    return format(value.quantize(Decimal("0.01")), "f")


def _instant(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid timestamp %r" % value) from exc
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _validate_window(window, label):
    start = _instant((window or {}).get("start"))
    end = _instant((window or {}).get("end"))
    if end <= start:
        raise ValueError("%s must end after it starts" % label)
    return start, end


def _validate_contract(spec):
    if not isinstance(spec, dict) or spec.get("schema") != CONTRACT_SCHEMA:
        raise ValueError("optimization review schema must be %s" % CONTRACT_SCHEMA)
    for key in ("optimization_id", "action_ref", "task_type", "acceptance_rubric_ref",
                "currency", "quality_guardrail"):
        if not spec.get(key):
            raise ValueError("optimization review.%s is required" % key)
    minimum = int(spec.get("min_tasks_per_period", 30))
    if minimum <= 0:
        raise ValueError("min_tasks_per_period must be positive")
    drop = _dec(spec["quality_guardrail"].get("max_success_rate_drop"), "max_success_rate_drop")
    if drop < 0 or drop > 1:
        raise ValueError("max_success_rate_drop must be between 0 and 1")
    baseline_window = _validate_window(spec.get("baseline_window"), "baseline_window")
    final_window = _validate_window(spec.get("final_window"), "final_window")
    if final_window[0] <= baseline_window[1]:
        raise ValueError("final_window must start after baseline_window without overlap")
    if spec.get("change_effective_at"):
        effective = _instant(spec["change_effective_at"])
        if effective <= baseline_window[1] or effective > final_window[0]:
            raise ValueError("change_effective_at must follow baseline_window and precede final observations")
    return minimum, drop


def _period(tasks, spec, window_name):
    start, end = _validate_window(spec[window_name], window_name)
    selected = []
    for row in tasks:
        if row.get("group") != "treatment":
            continue
        if str(row.get("task_type") or "") != str(spec["task_type"]):
            raise ValueError("task_type differs from the optimization contract")
        observed = _instant(row.get("observed_at"))
        if observed < start or observed > end:
            raise ValueError("task %s is outside %s" % (row["task_id"], window_name))
        if str(row.get("currency") or "").upper() != str(spec["currency"]).upper():
            raise ValueError("task currency differs from the optimization contract")
        selected.append(row)
    return selected


def evaluate(baseline_tasks, final_tasks, spec):
    minimum, max_drop = _validate_contract(spec)
    baseline_rows = _period(baseline_tasks, spec, "baseline_window")
    final_rows = _period(final_tasks, spec, "final_window")
    baseline = tool_value._group(baseline_rows, "treatment")
    final = tool_value._group(final_rows, "treatment")
    comparability = spec.get("comparability") if isinstance(spec.get("comparability"), dict) else {}
    comparable = all(comparability.get(key) is True for key in
                     ("same_task_set", "same_acceptance_rubric", "same_currency"))
    baseline_groups = {row.get("comparable_group") for row in baseline_rows}
    final_groups = {row.get("comparable_group") for row in final_rows}
    if comparability.get("same_task_set") is True and (
            len(baseline_groups) != 1 or len(final_groups) != 1
            or "" in baseline_groups or "" in final_groups
            or baseline_groups != final_groups):
        raise ValueError("same_task_set requires one matching non-empty comparable_group")

    baseline_cost = _dec(baseline["cost_per_task"]) if baseline["cost_per_task"] else None
    final_cost = _dec(final["cost_per_task"]) if final["cost_per_task"] else None
    saving = baseline_cost - final_cost if baseline_cost is not None and final_cost is not None else None
    baseline_success_cost = (_dec(baseline["cost_per_success"])
                             if baseline["cost_per_success"] else None)
    final_success_cost = (_dec(final["cost_per_success"])
                          if final["cost_per_success"] else None)
    success_cost_change = (final_success_cost - baseline_success_cost
                           if baseline_success_cost is not None and final_success_cost is not None else None)
    baseline_rate = baseline["success_rate"]
    final_rate = final["success_rate"]
    rate_change = final_rate - baseline_rate if baseline_rate is not None and final_rate is not None else None
    quality_drop = -rate_change if rate_change is not None and rate_change < 0 else Decimal("0")

    reasons = []
    effective_known = bool(spec.get("change_effective_at"))
    if not effective_known:
        reasons.append("CHANGE_EFFECTIVE_AT_UNKNOWN")
    sufficient = baseline["n"] >= minimum and final["n"] >= minimum
    if not sufficient:
        reasons.append("INSUFFICIENT_TASKS")
    if not comparable:
        reasons.append("NOT_COMPARABLE")
    if success_cost_change is None:
        reasons.append("COST_PER_SUCCESS_UNKNOWN")
    if rate_change is not None and Decimal(str(quality_drop)) > max_drop:
        decision = "rollback"
        reasons.append("QUALITY_GUARDRAIL_BREACHED")
    elif sufficient and comparable and effective_known and saving is not None and saving > 0 \
            and success_cost_change is not None and success_cost_change <= 0:
        decision = "keep"
        reasons.append("OBSERVED_COST_IMPROVEMENT_WITHIN_QUALITY_GUARDRAIL")
    else:
        decision = "inconclusive"
        if saving is not None and saving <= 0:
            reasons.append("NO_OBSERVED_SAVING")
        if success_cost_change is not None and success_cost_change > 0:
            reasons.append("COST_PER_SUCCESS_NOT_IMPROVED")
        if not reasons:
            reasons.append("REVIEW_REQUIRED")
    review = spec.get("human_review") if isinstance(spec.get("human_review"), dict) else {}
    if decision == "keep" and review.get("accepted") is True \
            and str(review.get("reviewed_by") or "").strip():
        verification = "buyer_confirmed_observed_difference"
    elif decision == "keep":
        verification = "observed_unverified"
    else:
        verification = "not_verified"
    return {
        "schema": RESULT_SCHEMA,
        "optimization_id": spec["optimization_id"],
        "action_ref": spec["action_ref"],
        "decision": decision,
        "reason_codes": reasons,
        "observed_change": {
            "cost_per_task_saving": _money(saving) if saving is not None else None,
            "success_rate_change": round(rate_change, 4) if rate_change is not None else None,
            "cost_per_success_change": _money(success_cost_change) if success_cost_change is not None else None,
        },
        "baseline": baseline,
        "final": final,
        "coverage": {
            "min_tasks_per_period": minimum,
            "baseline_comparable_groups": sorted(str(value) for value in baseline_groups),
            "final_comparable_groups": sorted(str(value) for value in final_groups),
            "acceptance_rubric_ref": spec["acceptance_rubric_ref"],
            "currency": spec["currency"],
            "change_effective_at": spec.get("change_effective_at"),
        },
        "comparability": comparability,
        "quality_guardrail": spec["quality_guardrail"],
        "saving_verification": verification,
        "human_review": review,
        "causality": "not_claimed",
        "control": {
            "mode": "review_only",
            "rollback_applied": False,
            "external_runtime_contacted": False,
            "supplier_bill_reconciled": False,
        },
        "boundary": "Before/after observed differences are not causal attribution or realized invoice savings. Keep/rollback is a recommendation for a named buyer decision.",
    }


def render(payload):
    return "\n".join([
        "优化 %s：%s" % (payload["optimization_id"], payload["decision"].upper()),
        "单任务成本下降观察：%s；成功率变化：%s；每次成功成本变化：%s。" % (
            payload["observed_change"]["cost_per_task_saving"],
            payload["observed_change"]["success_rate_change"],
            payload["observed_change"]["cost_per_success_change"]),
        "原因：%s" % ", ".join(payload["reason_codes"]),
        "这是只读复核；未应用回滚，未联系外部运行时，未核验供应商账单。",
    ]) + "\n"
