"""Deduplicated local JSONL handoff for active spend-monitoring anomalies."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from . import monitor_state

SCHEMA = "verovo.spend-alert/1"
SEVERITY = {"low": 1, "medium": 2, "high": 3}


def _stamp(value=None):
    if value is None:
        return monitor_state._now()
    text = str(value).strip()
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("alert timestamp must be ISO-8601") from exc
    return text


def _load_outbox(path):
    if not path.is_file():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("alert outbox line %d is unreadable: %s" % (number, exc.msg)) from exc
        if not isinstance(row, dict) or row.get("schema") != SCHEMA or not row.get("alert_id"):
            raise ValueError("alert outbox line %d has an unsupported record" % number)
        rows.append(row)
    return rows


def _alert_id(anomaly_id):
    digest = hashlib.sha256(str(anomaly_id).encode("utf-8")).hexdigest()
    return "alert-" + digest[:24]


def emit(state_path, outbox_path, *, min_severity="low", now=None):
    min_severity = str(min_severity or "low")
    if min_severity not in SEVERITY:
        raise ValueError("min_severity must be low, medium or high")
    state_path = Path(state_path)
    outbox_path = Path(outbox_path)
    stamp = _stamp(now)
    with monitor_state._locked(state_path):
        state = monitor_state._load(state_path)
        last_track = str(state.get("last_track_at") or "")
        active = []
        for anomaly_id, history in sorted(state.get("anomaly_history", {}).items()):
            if not isinstance(history, dict) or str(history.get("last_seen_at") or "") != last_track:
                continue
            anomaly = history.get("anomaly")
            if not isinstance(anomaly, dict):
                raise ValueError("active anomaly history is structurally invalid")
            active.append((anomaly_id, history, anomaly))
        eligible = [row for row in active
                    if SEVERITY.get(str(row[2].get("severity") or ""), 0) >= SEVERITY[min_severity]]
        with monitor_state._locked(outbox_path):
            existing = _load_outbox(outbox_path)
            known = {str(row.get("anomaly_id") or "") for row in existing}
            created = []
            for anomaly_id, history, anomaly in eligible:
                if anomaly_id in known:
                    continue
                alert = {
                    "schema": SCHEMA,
                    "alert_id": _alert_id(anomaly_id),
                    "anomaly_id": anomaly_id,
                    "created_at": stamp,
                    "active_since": str(history.get("first_seen_at") or ""),
                    "source_last_seen_at": str(history.get("last_seen_at") or ""),
                    "source_last_track_at": last_track,
                    "severity": str(anomaly.get("severity") or "unknown"),
                    "kind": str(anomaly.get("kind") or "unknown"),
                    "summary": anomaly.get("suggestion") or "Inspect the observed spending anomaly.",
                    "anomaly": anomaly,
                    "cost_state": "local_observed_not_final_bill",
                    "notification": {
                        "status": "pending_local_delivery",
                        "channel": "local_jsonl",
                        "external_delivery_attempted": False,
                        "external_delivery_confirmed": False,
                        "host_agent_awake": False,
                    },
                }
                created.append(alert)
                known.add(anomaly_id)
            if created:
                outbox_path.parent.mkdir(parents=True, exist_ok=True)
                payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in created)
                with outbox_path.open("a", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
            existing.extend(created)
            pending = sum(1 for row in existing
                          if (row.get("notification") or {}).get("status") == "pending_local_delivery")
    return {
        "schema": "verovo.spend-alert-batch/1",
        "status": "emitted" if created else "no_new_alerts",
        "created_at": stamp,
        "active_anomalies": len(active),
        "eligible_anomalies": len(eligible),
        "emitted": len(created),
        "alert_ids": [row["alert_id"] for row in created],
        "pending_local_delivery": pending,
        "outbox": str(outbox_path),
        "external_delivery_attempted": False,
        "external_delivery_confirmed": False,
        "host_agent_awake": False,
        "boundary": "A local JSONL handoff only. No email, IM, webhook, host wake-up, external spend control or final-bill verification was performed.",
    }


def render(payload):
    return "\n".join([
        "本地费用告警：新增 %d；待本地交付 %d。" % (
            payload["emitted"], payload["pending_local_delivery"]),
        "Outbox：%s" % payload["outbox"],
        "尚未发送外部通知、唤醒宿主 Agent 或控制外部支出。",
    ]) + "\n"
