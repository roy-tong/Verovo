"""Bounded local paired-task execution with process evidence, not inferred cost."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCHEMA = "verovo.controlled-task-run/1"
RESULT_SCHEMA = "verovo.controlled-task-run-result/1"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(value):
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate(spec):
    if not isinstance(spec, dict) or spec.get("schema") != SCHEMA:
        raise ValueError("controlled task run schema must be %s" % SCHEMA)
    for key in ("experiment_id", "task_type"):
        if not str(spec.get(key) or "").strip():
            raise ValueError("%s is required" % key)
    tasks = spec.get("task_ids")
    if not isinstance(tasks, list) or not tasks or not all(str(row or "").strip() for row in tasks):
        raise ValueError("task_ids must be a non-empty array")
    if len(set(tasks)) != len(tasks):
        raise ValueError("duplicate task_id")
    authorization = spec.get("authorization") if isinstance(spec.get("authorization"), dict) else {}
    if not str(authorization.get("authorized_by") or "").strip():
        raise ValueError("authorization.authorized_by is required")
    if authorization.get("local_only") is not True:
        raise ValueError("authorization.local_only must be true")
    limits = spec.get("limits") if isinstance(spec.get("limits"), dict) else {}
    try:
        timeout = Decimal(str(limits.get("timeout_seconds")))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("timeout_seconds must be positive") from exc
    if not timeout.is_finite() or timeout <= 0:
        raise ValueError("timeout_seconds must be positive")
    output_cap = limits.get("max_output_bytes")
    if isinstance(output_cap, bool) or not isinstance(output_cap, int) or output_cap <= 0:
        raise ValueError("max_output_bytes must be a positive integer")
    arms = spec.get("arms")
    if not isinstance(arms, list) or len(arms) < 2:
        raise ValueError("at least two arms are required")
    ids = set()
    roles = set()
    for arm in arms:
        if not isinstance(arm, dict) or not str(arm.get("arm_id") or "").strip():
            raise ValueError("arm_id is required")
        if arm["arm_id"] in ids:
            raise ValueError("duplicate arm_id")
        ids.add(arm["arm_id"])
        if arm.get("role") not in (None, "control", "treatment"):
            raise ValueError("arm role must be control or treatment")
        roles.add(arm.get("role") or arm["arm_id"])
        argv = arm.get("argv")
        if not isinstance(argv, list) or not argv or not all(str(value or "") for value in argv):
            raise ValueError("arm.argv must be a non-empty string array")
    if not {"control", "treatment"}.issubset(roles):
        raise ValueError("controlled run needs control and treatment arms")
    success = spec.get("success") if isinstance(spec.get("success"), dict) else {}
    expected_exit = success.get("exit_code", 0)
    if isinstance(expected_exit, bool) or not isinstance(expected_exit, int):
        raise ValueError("success.exit_code must be an integer")
    markers = success.get("stdout_contains", [])
    if not isinstance(markers, list) or not all(isinstance(row, str) for row in markers):
        raise ValueError("success.stdout_contains must be a string array")
    return timeout, output_cap


def _files(root):
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rows.append({"path": path.relative_to(root).as_posix(),
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return rows


def _one(spec, arm, task_id, out, process_runner):
    work = out / "work" / arm["arm_id"] / task_id
    work.mkdir(parents=True)
    env = {"OVO_EXPERIMENT_TASK_ID": task_id, "OVO_EXPERIMENT_ARM_ID": arm["arm_id"],
           "OVO_EXPERIMENT_ID": spec["experiment_id"]}
    process_id = "run-" + uuid.uuid4().hex
    started = _now()
    outcome = process_runner(
        argv=arm["argv"], cwd=work, env=env,
        timeout=float(spec["limits"]["timeout_seconds"]))
    error = str(outcome.get("error") or "")
    exit_code = outcome.get("exit_code")
    stdout = outcome.get("stdout") or b""
    stderr = outcome.get("stderr") or b""
    if isinstance(stdout, str):
        stdout = stdout.encode("utf-8")
    if isinstance(stderr, str):
        stderr = stderr.encode("utf-8")
    cap = int(spec["limits"]["max_output_bytes"])
    if len(stdout) > cap or len(stderr) > cap:
        error = "output_limit_exceeded"
    stdout = stdout[:cap]
    stderr = stderr[:cap]
    success = spec["success"]
    succeeded = (not error and exit_code == success.get("exit_code", 0)
                 and all(marker in stdout.decode("utf-8", errors="replace")
                         for marker in success.get("stdout_contains", [])))
    finished = _now()
    row = {
        "observation_id": process_id, "process_id": process_id,
        "experiment_id": spec["experiment_id"], "task_id": task_id,
        "arm_id": arm["arm_id"], "role": arm.get("role") or arm["arm_id"],
        "task_type": spec["task_type"], "started_at": started, "finished_at": finished,
        "exit_code": exit_code, "success": succeeded, "error": error,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "output_files": _files(work),
        "cost": None, "cost_status": "unknown", "currency": None,
        "external_actions": 0,
    }
    row["source_ref"] = "verovo-local-run:%s" % process_id
    row["source_hash"] = _hash({key: value for key, value in row.items() if key != "source_hash"})
    return row


def run(spec, out_dir, *, process_runner=None):
    timeout, output_cap = _validate(spec)
    if process_runner is None:
        raise ValueError("an explicit local process_runner adapter is required")
    spec = json.loads(json.dumps(spec))
    spec["limits"]["timeout_seconds"] = float(timeout)
    spec["limits"]["max_output_bytes"] = output_cap
    out = Path(out_dir)
    if out.exists():
        raise FileExistsError("refusing to overwrite experiment run directory")
    out.mkdir(parents=True)
    observations = []
    for task_id in spec["task_ids"]:
        for arm in spec["arms"]:
            observations.append(_one(spec, arm, task_id, out, process_runner))
    successful = sum(row["success"] for row in observations)
    result = {
        "schema": RESULT_SCHEMA, "experiment_id": spec["experiment_id"],
        "status": "PASS" if successful == len(observations) else "FAIL",
        "task_type": spec["task_type"],
        "summary": {
            "observations": len(observations), "successful": successful,
            "failed": len(observations) - successful, "tasks": len(spec["task_ids"]),
            "arms": len({row["arm_id"] for row in observations}),
        },
        "observations": observations,
        "cost_coverage": "unknown_not_inferred_from_runtime",
        "external_actions": 0,
        "authorization": spec["authorization"],
        "boundary": "Bounded local subprocess observations only. Output checks are task-specific acceptance evidence; cost remains unknown and no causal tool winner or production result is claimed.",
    }
    (out / "observations.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in observations), encoding="utf-8")
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
    return result


def render(payload):
    return "\n".join([
        "对照任务运行 %s：%s" % (payload["experiment_id"], payload["status"]),
        "观察 %d；成功 %d；费用状态：未知。" % (
            payload["summary"]["observations"], payload["summary"]["successful"]),
        "仅执行本地受约束进程；未执行外部动作，也不从运行结果推断工具成本或因果赢家。",
    ]) + "\n"
