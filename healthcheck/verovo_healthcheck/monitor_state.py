"""Persistent, deduplicated monitoring with an explicitly frozen historical baseline."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import monitor

STATE_SCHEMA = "verovo.spend-monitor-state/1"

try:
    import fcntl
except ImportError:
    fcntl = None


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _record_id(record):
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _anomaly_id(anomaly):
    canonical = json.dumps(anomaly, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _empty_state():
    return {"schema": STATE_SCHEMA, "records": [], "baseline": None,
            "anomaly_history": {}}


def _load(path):
    if not path.is_file():
        return _empty_state()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("monitor state is unreadable: %s" % exc) from exc
    if not isinstance(state, dict) or state.get("schema") != STATE_SCHEMA:
        raise ValueError("monitor state has an unsupported schema")
    if not isinstance(state.get("records"), list) \
            or not isinstance(state.get("anomaly_history"), dict):
        raise ValueError("monitor state is structurally invalid")
    return state


def _save(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+") as lock:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def track(records, state_path, *, min_days=7, z_threshold=3.5,
          price_drift=0.2, retry_threshold=5, freeze=False, now=None):
    path = Path(state_path)
    stamp = str(now or _now())
    with _locked(path):
        state = _load(path)
        known = {entry["record_id"] for entry in state["records"]}
        added = []
        for record in records:
            record_id = _record_id(record)
            if record_id in known:
                continue
            entry = {"record_id": record_id, "record": record}
            state["records"].append(entry)
            known.add(record_id)
            added.append(entry)
        duplicates = len(records) - len(added)
        all_records = [entry["record"] for entry in state["records"]]

        if freeze:
            payload = monitor.baseline(all_records, min_days=min_days)
            state["baseline"] = {
                "frozen_at": stamp,
                "through": max((row["observed_at"] for row in all_records), default=""),
                "record_ids": sorted(known),
                "payload": payload,
            }
            result = monitor.report(all_records, payload, [])
            result["status"] = "baseline_frozen"
            result["new_anomalies"] = []
            late_records = 0
        elif state.get("baseline") is None:
            payload = monitor.baseline([], min_days=min_days)
            result = monitor.report([], payload, [])
            result["status"] = "baseline_not_frozen"
            result["new_anomalies"] = []
            late_records = 0
        else:
            frozen = state["baseline"]
            baseline_ids = set(frozen.get("record_ids") or [])
            pending = [entry for entry in state["records"]
                       if entry["record_id"] not in baseline_ids]
            detection = [entry["record"] for entry in pending]
            payload = frozen["payload"]
            found = monitor.anomalies(
                detection, payload, z_threshold=z_threshold,
                price_drift=price_drift, retry_threshold=retry_threshold)
            new_anomalies = []
            for anomaly in found:
                anomaly_id = _anomaly_id(anomaly)
                history = state["anomaly_history"].get(anomaly_id)
                if history is None:
                    history = {"first_seen_at": stamp, "anomaly": anomaly}
                    state["anomaly_history"][anomaly_id] = history
                    new_anomalies.append(anomaly)
                history["last_seen_at"] = stamp
            result = monitor.report(detection, payload, found)
            result["new_anomalies"] = new_anomalies
            through = str(frozen.get("through") or "")
            late_records = sum(1 for entry in pending
                               if through and entry["record"]["observed_at"] <= through)
            result["baseline_frozen_at"] = frozen.get("frozen_at")
            result["baseline_through"] = through
        result.update({
            "new_records": len(added),
            "duplicate_records": duplicates,
            "state_records": len(state["records"]),
            "late_records": late_records,
            "state_schema": STATE_SCHEMA,
        })
        state["last_track_at"] = stamp
        _save(path, state)
        return result


def track_file(file_path, state_path, *, min_days=7, z_threshold=3.5,
               price_drift=0.2, retry_threshold=5, freeze=False, now=None):
    records = monitor.load_records(file_path)
    return track(records, state_path, min_days=min_days,
                 z_threshold=z_threshold, price_drift=price_drift,
                 retry_threshold=retry_threshold, freeze=freeze, now=now)
