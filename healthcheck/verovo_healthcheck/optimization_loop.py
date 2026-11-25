"""Persistent, explicitly ticked optimization reviews over authorized local task files."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import monitor_state, optimization_review, tool_value

SCHEMA = "verovo.optimization-loop/1"
STATE_SCHEMA = "verovo.optimization-loop-state/1"
RUN_SCHEMA = "verovo.optimization-loop-run/1"
SPEC_NAME = "optimization-loop.json"
STATE_NAME = "state.json"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_hash(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _instant(value):
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("optimization loop timestamps must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("optimization loop timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value):
    return value.isoformat(timespec="seconds")


def _validate(spec):
    if not isinstance(spec, dict) or spec.get("schema") != SCHEMA:
        raise ValueError("optimization loop schema must be %s" % SCHEMA)
    for key in ("loop_id", "owner", "registered_at", "baseline", "final"):
        if not str(spec.get(key) or "").strip():
            raise ValueError("optimization loop.%s is required" % key)
    interval = spec.get("interval_hours")
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) \
            or not math.isfinite(interval) or interval <= 0:
        raise ValueError("interval_hours must be a finite positive number")
    authorization = spec.get("authorization")
    if not isinstance(authorization, dict) or authorization.get("local_only") is not True \
            or str(authorization.get("authorized_by") or "") != str(spec["owner"]):
        raise ValueError("optimization loop requires named local_only authorization by its owner")
    _instant(spec["registered_at"])
    for key in ("baseline", "final"):
        if not Path(spec[key]).is_file():
            raise ValueError("optimization loop %s file is not available" % key)
    optimization_review._validate_contract(spec.get("review"))


def create(spec, out_dir):
    _validate(spec)
    registered = copy.deepcopy(spec)
    registered["baseline"] = str(Path(spec["baseline"]).resolve())
    registered["final"] = str(Path(spec["final"]).resolve())
    out = Path(out_dir)
    if out.exists():
        raise FileExistsError("refusing to overwrite optimization loop directory")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.mkdir()
    (out / SPEC_NAME).write_text(json.dumps(registered, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
    state = {
        "schema": STATE_SCHEMA, "loop_id": registered["loop_id"],
        "spec_sha256": _hash(registered), "paused": False,
        "registered_at": _iso(_instant(registered["registered_at"])),
        "next_due_at": _iso(_instant(registered["registered_at"])),
        "runs": [],
    }
    monitor_state._save(out / STATE_NAME, state)
    return {"status": "created", "schema": SCHEMA, "loop_id": registered["loop_id"],
            "dir": str(out), "scheduler": "external_tick_required"}


def _load(root):
    root = Path(root)
    spec = json.loads((root / SPEC_NAME).read_text(encoding="utf-8"))
    _validate(spec)
    state = json.loads((root / STATE_NAME).read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("schema") != STATE_SCHEMA \
            or not isinstance(state.get("runs"), list):
        raise ValueError("optimization loop state is structurally invalid")
    if state.get("spec_sha256") != _hash(spec):
        raise ValueError("registered optimization loop fingerprint mismatch")
    return spec, state


def status(root, *, now=""):
    spec, state = _load(root)
    current = _instant(now) if now else datetime.now(timezone.utc)
    runs = state["runs"]
    latest = runs[-1] if runs else None
    if state["paused"]:
        current_state = "paused"
    elif latest and latest["review"]["decision"] == "rollback":
        current_state = "rollback_required"
    else:
        current_state = "active"
    return {
        "schema": STATE_SCHEMA, "loop_id": spec["loop_id"],
        "state": current_state, "paused": state["paused"],
        "run_count": len(runs), "next_due_at": state["next_due_at"],
        "due": not state["paused"] and _instant(state["next_due_at"]) <= current,
        "latest_run": latest, "scheduler": "external_tick_required",
        "external_runtime_contacted": False, "rollback_applied": False,
        "supplier_bill_reconciled": False,
    }


def tick(root, *, now=""):
    path = Path(root) / STATE_NAME
    with monitor_state._locked(path):
        spec, state = _load(root)
        current = _instant(now) if now else datetime.now(timezone.utc)
        stamp = _iso(current)
        if state["paused"]:
            return {"schema": STATE_SCHEMA, "status": "paused", "runs": [],
                    "scheduler": "external_tick_required"}
        if _instant(state["next_due_at"]) > current:
            return {"schema": STATE_SCHEMA, "status": "no_due_loop", "runs": [],
                    "next_due_at": state["next_due_at"], "scheduler": "external_tick_required"}
        baseline_path = Path(spec["baseline"])
        final_path = Path(spec["final"])
        source = {"baseline_sha256": _file_hash(baseline_path),
                  "final_sha256": _file_hash(final_path)}
        review = optimization_review.evaluate(
            tool_value.load_tasks(baseline_path), tool_value.load_tasks(final_path), spec["review"])
        runs = state["runs"]
        previous = runs[-1]["source"] if runs else None
        run = {
            "schema": RUN_SCHEMA, "run_id": "%s-R%04d" % (spec["loop_id"], len(runs) + 1),
            "loop_id": spec["loop_id"], "started_at": stamp, "completed_at": stamp,
            "source": source, "source_changed": bool(previous and previous != source),
            "review": review, "rollback_required": review["decision"] == "rollback",
            "rollback_applied": False, "external_runtime_contacted": False,
            "supplier_bill_reconciled": False, "causality": "not_claimed",
        }
        runs.append(run)
        state["next_due_at"] = _iso(current + timedelta(hours=float(spec["interval_hours"])))
        monitor_state._save(path, state)
    return {"schema": STATE_SCHEMA, "status": "completed", "runs": [run],
            "next_due_at": state["next_due_at"], "scheduler": "external_tick_required",
            "boundary": "Explicit local review tick only. No background worker, external configuration, rollback or supplier-bill reconciliation is performed."}


def set_paused(root, paused, *, now=""):
    path = Path(root) / STATE_NAME
    with monitor_state._locked(path):
        spec, state = _load(root)
        current = _instant(now) if now else datetime.now(timezone.utc)
        state["paused"] = bool(paused)
        if not paused:
            state["next_due_at"] = _iso(current)
        monitor_state._save(path, state)
    return {"schema": STATE_SCHEMA, "status": "paused" if paused else "resumed",
            "loop_id": spec["loop_id"], "scheduler": "external_tick_required"}
