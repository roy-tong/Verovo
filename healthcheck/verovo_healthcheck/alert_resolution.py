"""Read-only spend-alert projection into the shared Problem Resolution Contract."""
from __future__ import annotations

import json
from pathlib import Path

SCHEMA = "ovo.problem-resolution/1"
ALERT_SCHEMA = "verovo.spend-alert/1"


def load_alert(path, alert_id):
    path = Path(path)
    if not path.is_file():
        raise ValueError("alert file not found: %s" % path)
    if path.suffix.lower() == ".json":
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("alert JSON is unreadable: %s" % exc.msg) from exc
        rows = [document]
    else:
        rows = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("alert outbox line %d is unreadable: %s" % (number, exc.msg)) from exc
            rows.append(row)
    matches = [row for row in rows if isinstance(row, dict) and row.get("alert_id") == alert_id]
    if len(matches) > 1:
        raise ValueError("duplicate alert_id in alert input: %s" % alert_id)
    if not matches:
        raise ValueError("alert_id not found: %s" % alert_id)
    return matches[0]


def from_alert(alert):
    if not isinstance(alert, dict) or alert.get("schema") != ALERT_SCHEMA:
        raise ValueError("alert schema must be %s" % ALERT_SCHEMA)
    alert_id = str(alert.get("alert_id") or "").strip()
    anomaly_id = str(alert.get("anomaly_id") or "").strip()
    anomaly = alert.get("anomaly")
    if not alert_id or not anomaly_id or not isinstance(anomaly, dict):
        raise ValueError("alert needs alert_id, anomaly_id and anomaly")
    kind = str(anomaly.get("kind") or "").strip()
    if not kind:
        raise ValueError("alert anomaly.kind is required")
    refs = [alert_id, anomaly_id]
    document = {
        "schema": SCHEMA,
        "problem_id": alert_id + "-resolution",
        "product": "verovo",
        "task_id": alert_id,
        "observed_at": alert.get("source_last_seen_at") or alert.get("created_at"),
        "observation": {
            "code": "SPEND_ANOMALY_" + kind.upper(),
            "summary": str(alert.get("summary") or anomaly.get("suggestion") or "Observed spend anomaly"),
            "external_effect_status": "none",
            "evidence_refs": refs,
        },
    }
    if kind in ("orphan_call", "unit_price_drift"):
        document["requested_resolution"] = "data_needed"
        task_mapping = kind == "orphan_call"
        document["data"] = {
            "request_id": alert_id + ("-task-mapping" if task_mapping else "-billing-rule"),
            "source": "authorized-agent-task-registry" if task_mapping else "approved-billing-rule-registry",
            "access_basis": "buyer-authorized local records",
            "automatic_fetch_attempted": False,
            "side_effect_free_fetch": True,
        }
        return document

    document["requested_resolution"] = "business_decision"
    if kind == "retry_storm":
        options = [
            {"option_id": "pause_retry_path",
             "impact": "A pause recommendation only; an external pause is not applied or verified."},
            {"option_id": "keep_observing",
             "impact": "No runtime change; preserve evidence and inspect the failing step."},
        ]
    else:
        options = [
            {"option_id": "inspect_task_and_price",
             "impact": "Review task volume, retries and applicable prices; no external action is applied."},
            {"option_id": "defer_disposition",
             "impact": "Keep the amount unverified and do not infer a supplier error or savings."},
        ]
    document["decision"] = {"evidence_refs": refs, "options": options}
    return document
