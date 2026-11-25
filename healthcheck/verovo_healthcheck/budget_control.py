"""Advisory allow/warn/hold/review decisions from local monitoring state."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import monitor_state

DECISION_SCHEMA = "verovo.spend-control-decision/1"
BUDGET_SCHEMA = "verovo.spend-budget/1"
SEVERITY = {"low": 1, "medium": 2, "high": 3}


def _decimal(value, label):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("%s must be a finite decimal" % label) from exc
    if not number.is_finite():
        raise ValueError("%s must be a finite decimal" % label)
    return number


def _text(value):
    return format(value, "f")


def _instant(value):
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid timestamp %r" % value) from exc
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _scope_matches(record, scope):
    return all(not scope.get(key) or str(record.get(key) or "") == str(scope[key])
               for key in ("task_id", "tool", "model"))


def _anomaly_matches(anomaly, scope):
    for key in ("task_id", "tool", "model", "currency"):
        if scope.get(key) and anomaly.get(key) and str(anomaly[key]) != str(scope[key]):
            return False
    return True


def _validate(budget):
    if not isinstance(budget, dict) or budget.get("schema") != BUDGET_SCHEMA:
        raise ValueError("budget schema must be %s" % BUDGET_SCHEMA)
    for key in ("budget_id", "currency", "period"):
        if not budget.get(key):
            raise ValueError("budget.%s is required" % key)
    limit = _decimal(budget.get("limit"), "limit")
    warn = _decimal(budget.get("warn_at"), "warn_at")
    hold = _decimal(budget.get("hold_at"), "hold_at")
    if limit <= 0 or warn <= 0 or hold <= 0 or warn > hold:
        raise ValueError("warn_at and hold_at must be positive and warn_at <= hold_at")
    start = _instant(budget["period"].get("start"))
    end = _instant(budget["period"].get("end"))
    if end <= start:
        raise ValueError("budget period must end after it starts")
    severity = str(budget.get("review_on_severity") or "high")
    if severity != "none" and severity not in SEVERITY:
        raise ValueError("unknown review_on_severity")
    return limit, warn, hold, start, end, severity


def evaluate(state_path, budget, *, now=None):
    limit, warn, hold, start, end, severity = _validate(budget)
    state = monitor_state._load(Path(state_path))
    scope = budget.get("scope") if isinstance(budget.get("scope"), dict) else {}
    scope = dict(scope, currency=budget["currency"])
    records = [entry["record"] for entry in state["records"]]
    in_scope = []
    unknown_currency = 0
    for record in records:
        observed = _instant(record.get("observed_at"))
        if observed < start or observed >= end or not _scope_matches(record, scope):
            continue
        if not record.get("currency"):
            unknown_currency += 1
            continue
        if str(record["currency"]).upper() != str(budget["currency"]).upper():
            continue
        in_scope.append(record)
    spent = sum((_decimal(row.get("amount"), "amount") for row in in_scope), Decimal("0"))
    remaining = limit - spent
    utilization = spent / limit

    history = state.get("anomaly_history") or {}
    last_track = str(state.get("last_track_at") or "")
    if not last_track:
        last_track = max((str(row.get("last_seen_at") or "") for row in history.values()), default="")
    active = []
    for row in history.values():
        anomaly = row.get("anomaly") if isinstance(row, dict) else None
        if not isinstance(anomaly, dict) or str(row.get("last_seen_at") or "") != last_track:
            continue
        if _anomaly_matches(anomaly, scope):
            active.append(anomaly)
    risky = [row for row in active if severity != "none"
             and SEVERITY.get(str(row.get("severity") or ""), 0) >= SEVERITY[severity]]

    reasons = []
    if utilization >= hold:
        decision = "hold"
        reasons.append("BUDGET_HOLD_THRESHOLD_REACHED")
    elif unknown_currency or risky:
        decision = "review"
        if unknown_currency:
            reasons.append("CURRENCY_UNKNOWN")
        if risky:
            reasons.append("ACTIVE_HIGH_RISK_ANOMALY")
    elif utilization >= warn:
        decision = "warn"
        reasons.append("BUDGET_WARN_THRESHOLD_REACHED")
    else:
        decision = "allow"
        reasons.append("WITHIN_OBSERVED_BUDGET")
    return {
        "schema": DECISION_SCHEMA,
        "budget_id": str(budget["budget_id"]),
        "scope": {key: value for key, value in scope.items() if key != "currency"},
        "decision": decision,
        "reason_codes": reasons,
        "evaluated_at": str(now or datetime.now(timezone.utc).isoformat(timespec="seconds")),
        "currency": str(budget["currency"]).upper(),
        "limit": _text(limit),
        "observed_spend": _text(spent),
        "remaining": _text(remaining),
        "utilization": _text(utilization.quantize(Decimal("0.0001"))),
        "cost_state": "local_observed_not_final_bill",
        "coverage": {
            "matched_records": len(in_scope),
            "unknown_currency_records": unknown_currency,
            "active_anomalies": len(active),
            "period": {"start": budget["period"]["start"], "end": budget["period"]["end"],
                       "end_exclusive": True},
        },
        "control": {
            "mode": "advisory_only",
            "execution_applied": False,
            "external_runtime_contacted": False,
            "note": "A hold or review signal does not prove that external spending stopped.",
        },
    }


def render(payload):
    lines = [
        "预算 %s：%s" % (payload["budget_id"], payload["decision"].upper()),
        "已观察 %s %s / 预算 %s；剩余 %s。" % (
            payload["currency"], payload["observed_spend"],
            payload["limit"], payload["remaining"]),
        "原因：%s" % ", ".join(payload["reason_codes"]),
        "仅为本地控制建议，未对外部运行时应用任何控制。",
    ]
    return "\n".join(lines) + "\n"
