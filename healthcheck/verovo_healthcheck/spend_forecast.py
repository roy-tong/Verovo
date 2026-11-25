"""Local task-spend forecast from observed cost and explicit execution progress."""
from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

from . import budget_control, monitor_state

SCHEMA = "verovo.spend-forecast/1"
RESULT_SCHEMA = "verovo.spend-forecast-result/1"


def _money(value):
    return format(value.quantize(Decimal("0.01")), "f")


def _validate(spec):
    if not isinstance(spec, dict) or spec.get("schema") != SCHEMA:
        raise ValueError("spend forecast schema must be %s" % SCHEMA)
    for key in ("forecast_id", "task_id", "currency", "period", "progress"):
        if not spec.get(key):
            raise ValueError("spend forecast.%s is required" % key)
    start = budget_control._instant(spec["period"].get("start"))
    end = budget_control._instant(spec["period"].get("end"))
    if end <= start:
        raise ValueError("forecast period must end after it starts")
    progress = spec["progress"]
    for key in ("unit", "source_ref", "observed_at"):
        if not str(progress.get(key) or "").strip():
            raise ValueError("progress.%s is required" % key)
    budget_control._instant(progress["observed_at"])
    planned = budget_control._decimal(progress.get("planned_units"), "planned_units")
    completed = budget_control._decimal(progress.get("completed_units"), "completed_units")
    if planned <= 0 or completed < 0 or completed > planned:
        raise ValueError("progress requires planned_units > 0 and 0 <= completed_units <= planned_units")
    return start, end, planned, completed


def evaluate(state_path, spec):
    start, end, planned, completed = _validate(spec)
    state = monitor_state._load(Path(state_path))
    records = [entry["record"] for entry in state["records"]]
    matched = []
    unknown_currency = 0
    other_currency = 0
    task_records = 0
    for record in records:
        if str(record.get("task_id") or "") != str(spec["task_id"]):
            continue
        observed = budget_control._instant(record.get("observed_at"))
        if observed < start or observed >= end:
            continue
        task_records += 1
        if not record.get("currency"):
            unknown_currency += 1
            continue
        if str(record["currency"]).upper() != str(spec["currency"]).upper():
            other_currency += 1
            continue
        matched.append(record)
    observed_spend = sum((budget_control._decimal(row.get("amount"), "amount") for row in matched),
                         Decimal("0"))
    remaining = planned - completed
    reasons = []
    if not matched:
        reasons.append("NO_MATCHING_OBSERVED_COST")
    if completed <= 0:
        reasons.append("NO_COMPLETED_PROGRESS")
    sufficient = bool(matched) and completed > 0
    per_unit = observed_spend / completed if sufficient else None
    projected_remaining = per_unit * remaining if sufficient else None
    projected_total = observed_spend + projected_remaining if sufficient else None
    return {
        "schema": RESULT_SCHEMA,
        "forecast_id": spec["forecast_id"],
        "task_id": spec["task_id"],
        "status": "estimated" if sufficient else "insufficient_data",
        "reason_codes": reasons,
        "currency": str(spec["currency"]).upper(),
        "period": {"start": spec["period"]["start"], "end": spec["period"]["end"],
                   "end_exclusive": True},
        "progress": copy.deepcopy(spec["progress"]),
        "remaining_units": format(remaining, "f"),
        "observed_spend": format(observed_spend, "f"),
        "observed_cost_per_completed_unit": _money(per_unit) if sufficient else None,
        "projected_remaining_cost": _money(projected_remaining) if sufficient else None,
        "projected_total_cost": _money(projected_total) if sufficient else None,
        "method": "constant_observed_cost_per_completed_unit",
        "forecast_state": "estimate_not_cap",
        "cost_state": "local_observed_not_final_bill",
        "coverage": {
            "task_records_in_period": task_records,
            "matched_records": len(matched),
            "unknown_currency_records": unknown_currency,
            "other_currency_records": other_currency,
            "progress_source_ref": spec["progress"]["source_ref"],
        },
        "control": {"mode": "estimate_only", "external_runtime_contacted": False,
                    "budget_or_limit_applied": False},
        "boundary": "A linear estimate from observed local cost and declared progress. Future retries, price changes, unfinished work, late bills and final supplier charges can differ; this is not a cap or causal prediction.",
    }


def render(payload):
    return "\n".join([
        "任务费用预测：%s" % payload["status"],
        "已观察 %s %s；预计剩余 %s；预计总计 %s。" % (
            payload["observed_spend"], payload["currency"],
            payload["projected_remaining_cost"], payload["projected_total_cost"]),
        "仅为按当前完成单元成本外推的估算，不是预算上限或最终账单。",
    ]) + "\n"
