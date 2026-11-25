"""Reconcile local hold decisions, runtime readbacks and subsequent observed spend."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from . import budget_control, monitor_state

RECEIPT_SCHEMA = "verovo.spend-control-receipt/1"
VERIFICATION_SCHEMA = "verovo.spend-control-verification/1"


def _optional_instant(value):
    return budget_control._instant(value) if str(value or "").strip() else None


def _base(decision, receipt):
    return {
        "schema": VERIFICATION_SCHEMA,
        "budget_id": str(decision.get("budget_id") or ""),
        "receipt_id": str(receipt.get("receipt_id") or ""),
        "scope": dict(decision.get("scope") or {}),
        "currency": str(decision.get("currency") or ""),
        "control": {
            "requested_decision": str(decision.get("decision") or ""),
            "execution_applied": False,
            "readback_status": str((receipt.get("readback") or {}).get("status") or "unknown"),
            "external_runtime_contacted": False,
            "verification_scope": "local_receipt_and_monitor_consistency",
        },
    }


def evaluate(state_path, decision, receipt):
    if not isinstance(decision, dict) or decision.get("schema") != budget_control.DECISION_SCHEMA:
        raise ValueError("decision schema must be %s" % budget_control.DECISION_SCHEMA)
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        raise ValueError("receipt schema must be %s" % RECEIPT_SCHEMA)
    result = _base(decision, receipt)
    reasons = []
    scope = decision.get("scope") if isinstance(decision.get("scope"), dict) else {}
    if decision.get("decision") not in {"hold", "review"}:
        reasons.append("CONTROL_DECISION_DOES_NOT_REQUEST_RESTRICTION")
    if str(receipt.get("budget_id") or "") != str(decision.get("budget_id") or ""):
        reasons.append("CONTROL_BUDGET_MISMATCH")
    if receipt.get("requested_action") != decision.get("decision"):
        reasons.append("CONTROL_ACTION_MISMATCH")
    if receipt.get("scope") != scope:
        reasons.append("CONTROL_SCOPE_MISMATCH")
    if reasons:
        result.update({"status": "FAIL", "reason_codes": reasons,
                       "post_stop_spend": {"amount": None, "matched_records": 0}})
        return result

    readback = receipt.get("readback") if isinstance(receipt.get("readback"), dict) else {}
    stopped_at = _optional_instant(readback.get("observed_at"))
    evaluated_at = budget_control._instant(decision.get("evaluated_at"))
    readback_ok = (readback.get("status") == "stopped"
                   and readback.get("source_type") == "runtime_readback"
                   and bool(readback.get("source_ref")) and stopped_at is not None
                   and stopped_at >= evaluated_at)
    result["control"]["execution_applied"] = readback_ok
    if not readback_ok:
        reasons.append("STOP_READBACK_NOT_CONFIRMED")

    coverage = receipt.get("coverage") if isinstance(receipt.get("coverage"), dict) else {}
    coverage_start = _optional_instant(coverage.get("start"))
    coverage_end = _optional_instant(coverage.get("end"))
    state = monitor_state._load(Path(state_path))
    tracked_through = _optional_instant(state.get("last_track_at"))
    coverage_ok = (coverage.get("complete") is True and bool(coverage.get("source_ref"))
                   and None not in (coverage_start, coverage_end, stopped_at, tracked_through)
                   and coverage_start <= stopped_at and coverage_end > stopped_at
                   and tracked_through >= coverage_end)
    if not coverage_ok:
        reasons.append("POST_STOP_COVERAGE_INCOMPLETE")

    matched = []
    ambiguous = []
    for entry in state["records"]:
        if stopped_at is None or coverage_end is None:
            break
        record = entry["record"]
        if not budget_control._scope_matches(record, scope):
            continue
        if str(record.get("currency") or "").upper() != str(decision.get("currency") or "").upper():
            continue
        observed_at = budget_control._instant(record.get("observed_at"))
        if observed_at == stopped_at:
            ambiguous.append(record)
        elif stopped_at < observed_at < coverage_end:
            if budget_control._decimal(record.get("amount"), "amount") > 0:
                matched.append(record)
    if ambiguous:
        reasons.append("POST_STOP_EVENT_ORDER_AMBIGUOUS")
    amount = None
    if coverage_ok and not ambiguous:
        amount = budget_control._text(sum(
            (budget_control._decimal(row.get("amount"), "amount") for row in matched),
            Decimal("0")))
    result["post_stop_spend"] = {"amount": amount, "matched_records": len(matched)}
    result["coverage"] = {
        "start": coverage.get("start"), "end": coverage.get("end"),
        "end_exclusive": True, "complete": coverage_ok,
        "source_ref": coverage.get("source_ref"),
        "monitor_last_track_at": state.get("last_track_at"),
    }
    if coverage_ok and not ambiguous:
        if matched and budget_control._decimal(amount, "post-stop amount") != 0:
            reasons.append("POST_STOP_SPEND_DETECTED")
        else:
            reasons.append("NO_POST_STOP_SPEND_IN_COMPLETE_WINDOW")
    if "POST_STOP_SPEND_DETECTED" in reasons:
        status = "FAIL"
    elif not readback_ok or not coverage_ok or ambiguous:
        status = "UNPROVABLE"
    else:
        status = "PASS"
        reasons.append("STOP_READBACK_MATCHED")
    result.update({"status": status, "reason_codes": reasons})
    return result


def render(payload):
    lines = [
        "预算 %s 的控制核验：%s" % (payload["budget_id"], payload["status"]),
        "停止回执：%s；停止后费用：%s %s。" % (
            payload["control"]["readback_status"],
            payload["post_stop_spend"]["amount"] if payload["post_stop_spend"]["amount"] is not None else "未知",
            payload["currency"]),
        "原因：%s" % ", ".join(payload["reason_codes"]),
        "仅核对本地回执与监测记录；本次未联系外部运行时，也不证明外部记录的真实性。",
    ]
    return "\n".join(lines) + "\n"
