"""Explicit hold-adapter execution with independent runtime readback."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

SCHEMA = "verovo.spend-control-adapter/1"
RECEIPT_SCHEMA = "verovo.spend-control-receipt/1"


def _validate(spec, decision):
    if not isinstance(spec, dict) or spec.get("schema") != SCHEMA:
        raise ValueError("control adapter schema must be %s" % SCHEMA)
    for key in ("adapter_id", "budget_id"):
        if not str(spec.get(key) or "").strip():
            raise ValueError("control adapter.%s is required" % key)
    if spec.get("action") != "hold":
        raise ValueError("only an explicit hold adapter action is supported")
    authorization = spec.get("authorization") if isinstance(spec.get("authorization"), dict) else {}
    if not str(authorization.get("authorized_by") or "").strip() \
            or not str(authorization.get("authorization_ref") or "").strip():
        raise ValueError("named authorization and authorization_ref are required")
    for key in ("argv", "readback_argv"):
        if not isinstance(spec.get(key), list) or not spec[key] \
                or not all(str(value or "").strip() for value in spec[key]):
            raise ValueError("%s must be a non-empty string array" % key)
    timeout = spec.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
        raise ValueError("timeout_seconds must be between 0 and 60")
    if not isinstance(decision, dict) or decision.get("decision") != "hold":
        raise ValueError("a current hold decision is required")
    if str(decision.get("budget_id") or "") != str(spec["budget_id"]):
        raise ValueError("hold decision budget_id does not match the adapter")
    if (decision.get("scope") or {}) != (spec.get("scope") or {}):
        raise ValueError("hold decision scope does not match the adapter")


def _instant(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("control decision and readback need ISO-8601 timestamps") from exc
    if parsed.tzinfo is None:
        raise ValueError("control decision and readback timestamps need a timezone")
    return parsed


def _call(runner, argv, spec):
    try:
        outcome = runner(argv=argv, cwd=str(Path(spec.get("working_directory") or ".")),
                         env={}, timeout=float(spec["timeout_seconds"]))
    except (OSError, ValueError) as exc:
        return {"exit_code": None, "stdout": b"", "stderr": b"",
                "error": type(exc).__name__ + ": " + str(exc)}
    result = dict(outcome or {})
    for stream in ("stdout", "stderr"):
        value = result.get(stream) or b""
        if isinstance(value, str):
            value = value.encode("utf-8")
        result[stream] = value[:65536]
    return result


def apply(spec, decision, *, process_runner=None):
    _validate(spec, decision)
    if process_runner is None:
        raise ValueError("an explicit control process_runner adapter is required")
    action = _call(process_runner, spec["argv"], spec)
    readback = _call(process_runner, spec["readback_argv"], spec)
    observed = {}
    try:
        candidate = json.loads(readback["stdout"].decode("utf-8"))
        if isinstance(candidate, dict):
            observed = candidate
    except (ValueError, UnicodeDecodeError):
        observed = {}
    verification_errors = []
    if observed.get("budget_id") != spec["budget_id"]:
        verification_errors.append("budget_mismatch")
    if (observed.get("scope") or {}) != (spec.get("scope") or {}):
        verification_errors.append("scope_mismatch")
    if observed.get("action") != "hold":
        verification_errors.append("action_mismatch")
    try:
        if _instant(observed.get("observed_at")) < _instant(decision.get("evaluated_at")):
            verification_errors.append("stale_readback")
    except ValueError:
        verification_errors.append("invalid_readback_time")
    readback_valid = bool(str(observed.get("observed_at") or "").strip()
                          and str(observed.get("source_ref") or "").strip()
                          and not verification_errors)
    applied = bool(not action.get("error") and action.get("exit_code") == 0
                   and not readback.get("error") and readback.get("exit_code") == 0
                   and readback_valid and observed.get("status") == "stopped")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "receipt_id": "control-" + hashlib.sha256(json.dumps({
            "adapter": spec["adapter_id"], "decision": decision,
            "action_exit": action.get("exit_code"), "readback": observed,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16],
        "budget_id": spec["budget_id"],
        "requested_action": "hold",
        "scope": spec.get("scope") or {},
        "control_verified": applied,
        "readback": {
            "status": str(observed.get("status") or "unknown"),
            "budget_id": str(observed.get("budget_id") or ""),
            "scope": observed.get("scope") if isinstance(observed.get("scope"), dict) else {},
            "action": str(observed.get("action") or ""),
            "observed_at": str(observed.get("observed_at") or ""),
            "source_type": "runtime_readback",
            "source_ref": str(observed.get("source_ref") or ""),
        },
        "coverage": {"complete": False, "source_ref": ""},
    }
    return {
        "schema": "verovo.spend-control-adapter-result/1",
        "status": "APPLIED" if applied else "UNPROVABLE",
        "adapter_id": spec["adapter_id"],
        "adapter_executed": True,
        "external_runtime_contacted": spec.get("external_runtime") is True,
        "action": {"exit_code": action.get("exit_code"), "error": str(action.get("error") or "")},
        "readback_exit_code": readback.get("exit_code"),
        "verification_errors": verification_errors,
        "receipt": receipt,
        "subsequent_spend_verified": False,
        "automatic_retry": False,
        "boundary": "APPLIED means the configured command returned success and a stopped runtime readback with matching budget/scope/action and a non-stale observation. It does not prove complete post-stop spend coverage or realized savings; run budget verify separately.",
    }
