"""Named budget recovery with current-budget re-evaluation and runtime readback."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import budget_control, monitor_state

RECOVERY_SCHEMA = "verovo.spend-budget-recovery/1"
RESULT_SCHEMA = "verovo.spend-budget-recovery-result/1"


def _history(path):
    path = Path(path)
    if not path.is_file():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("recovery history line %d is unreadable" % number) from exc
        if not isinstance(row, dict) or row.get("schema") != RESULT_SCHEMA:
            raise ValueError("recovery history line %d has an unsupported schema" % number)
        rows.append(row)
    return rows


def _append(path, payload, recovery):
    with monitor_state._locked(Path(path)):
        rows = _history(path)
        source_hash = hashlib.sha256(json.dumps(
            recovery, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        outcome_hash = hashlib.sha256(json.dumps({
            "status": payload["status"], "reason_codes": payload["reason_codes"],
            "current_budget_decision": payload["current_budget_decision"],
            "readback_status": payload["control"]["readback_status"],
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        duplicates = 0
        for row in rows:
            if row.get("recovery_id") == recovery["recovery_id"]:
                if row.get("source_sha256") != source_hash:
                    raise ValueError("recovery_id conflicts with existing history")
                if row.get("outcome_sha256") == outcome_hash:
                    duplicates += 1
        if not duplicates:
            record = dict(payload)
            record["source_sha256"] = source_hash
            record["outcome_sha256"] = outcome_hash
            record["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            rows.append(record)
        return {"records": len(rows), "duplicate_records": duplicates}


def evaluate(state_path, budget, recovery, history_path):
    if not isinstance(recovery, dict) or recovery.get("schema") != RECOVERY_SCHEMA:
        raise ValueError("recovery schema must be %s" % RECOVERY_SCHEMA)
    for key in ("recovery_id", "budget_id", "confirmed_by", "confirmed_at", "confirmation_ref", "reason"):
        if not str(recovery.get(key) or "").strip():
            raise ValueError("recovery.%s is required" % key)
    current = budget_control.evaluate(state_path, budget)
    reasons = []
    if recovery["budget_id"] != current["budget_id"]:
        reasons.append("CONTROL_BUDGET_MISMATCH")
    if recovery.get("scope") != current.get("scope"):
        reasons.append("CONTROL_SCOPE_MISMATCH")
    if current["decision"] == "hold":
        reasons.append("CURRENT_BUDGET_STILL_HOLD")
    elif current["decision"] == "review":
        reasons.append("CURRENT_BUDGET_REQUIRES_REVIEW")
    readback = recovery.get("readback") if isinstance(recovery.get("readback"), dict) else {}
    observed_at = readback.get("observed_at")
    readback_ok = (readback.get("status") == "running"
                   and readback.get("source_type") == "runtime_readback"
                   and bool(readback.get("source_ref")) and bool(observed_at)
                   and budget_control._instant(observed_at) >= budget_control._instant(recovery["confirmed_at"]))
    if not readback_ok:
        reasons.append("RUNNING_READBACK_NOT_CONFIRMED")
    applied = readback_ok and not reasons
    status = "PASS" if not reasons else ("UNPROVABLE" if reasons == ["RUNNING_READBACK_NOT_CONFIRMED"] else "FAIL")
    payload = {
        "schema": RESULT_SCHEMA,
        "recovery_id": recovery["recovery_id"], "budget_id": current["budget_id"],
        "status": status, "reason_codes": reasons,
        "scope": current["scope"],
        "observed_spend_preserved": current["observed_spend"],
        "current_budget_decision": current["decision"],
        "confirmed_by": recovery["confirmed_by"], "confirmed_at": recovery["confirmed_at"],
        "confirmation_ref": recovery["confirmation_ref"], "reason": recovery["reason"],
        "control": {
            "execution_applied": applied, "readback_status": readback.get("status") or "unknown",
            "external_runtime_contacted": False, "budget_reset": False,
            "verification_scope": "local_recovery_and_runtime_readback_consistency",
        },
        "history": {"path": str(history_path), "records": 0, "duplicate_records": 0},
        "boundary": "Named confirmation and local readback consistency only. Historical spend is preserved; no external runtime was contacted and no budget was reset.",
    }
    payload["history"] = dict(payload["history"], **_append(history_path, payload, recovery))
    return payload


def render(payload):
    return "\n".join([
        "预算 %s 的恢复核验：%s" % (payload["budget_id"], payload["status"]),
        "当前预算决定：%s；保留已观察费用：%s。" % (
            payload["current_budget_decision"], payload["observed_spend_preserved"]),
        "原因：%s" % (", ".join(payload["reason_codes"]) or "确认、当前预算和 running readback 一致"),
        "仅追加本地恢复记录；未重置预算，也未联系外部运行时。",
    ]) + "\n"
