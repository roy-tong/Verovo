"""V-04 period revisions: append-only, independently recalculated versions.

The V-02/V-03 root layout stays the first version.  On the first revision the
root is snapshotted into ``revisions/r1`` and every later version gets its own
``revisions/rN`` directory; nothing in an older revision is rewritten.  A
revision stores the canonical bundle, calculation, report and review/signoff
sidecars it produced, so a later source change cannot move an earlier signoff.

The delta separates three things that are easy to conflate:

- amount / cause / completion-axis changes;
- newly available evidence (a source or verdict that did not exist before);
- credit **applied** (reduces the amount due) versus credit merely **issued**
  (open exposure, never realized savings).  The recovery/settlement vocabulary
  is aligned with ``credited``/``paid`` meaning realized cash only when the
  credit was actually applied; issuance alone stays pending.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import close_report
from .close import (CloseError, INVOICE_SUFFIXES, ROLES, TABLE_SUFFIXES,
                    confirm_mapping, init_case, ingest, load_period_job)

REVISION_SCHEMA = "verovo.period-revision/1"
DELTA_SCHEMA = "verovo.period-revision-delta/1"
SIGNOFF_SCHEMA = "verovo.close-signoff/1"
INDEX_SCHEMA = "verovo.period-revision-index/1"
JOURNAL_SCHEMA = "verovo.period-revision-journal/1"
REVISION_DIR = "revisions"
JOURNAL_PREFIX = ".journal-"
STAGING_PREFIX = ".staging-"
REVISION_ID_RE = re.compile(r"^r\d+$")

AMOUNT_KEYS = (
    "invoice_total", "rule_repriced", "matched_postings",
    "distinct_source_reconciled", "known_adjustments", "open_difference",
    "credits_applied", "credits_pending", "realized_savings",
)
AXES = ("finance_status", "verification_status", "delivery_status",
        "economics_status")
CREDIT_TYPES = ("credit", "refund", "discount")


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path, label: str) -> Dict[str, Any]:
    if not path.is_file():
        raise CloseError("INPUT_REQUIRED", "%s not found: %s" % (label, path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CloseError("INPUT_REQUIRED", "%s is invalid JSON: %s"
                         % (label, exc)) from exc
    if not isinstance(data, dict):
        raise CloseError("INPUT_REQUIRED", "%s must be an object" % label)
    return data


def _write_json(path: Path, payload: Any) -> None:
    close_report._write_json(path, payload)


def _sha_file(path: Path) -> str:
    return close_report._sha_file(path)


def _fingerprint(payload: Any) -> str:
    return close_report._fingerprint(payload)


def _money(value: Optional[Decimal]) -> Optional[str]:
    return close_report._money(value)


def _dec(value: Any, label: str) -> Optional[Decimal]:
    return close_report._dec(value, label)


def _revisions_dir(case_dir: Path) -> Path:
    return Path(case_dir) / REVISION_DIR


def _index_path(case_dir: Path) -> Path:
    return _revisions_dir(case_dir) / "index.json"


def _load_index(case_dir: Path) -> Dict[str, Any]:
    path = _index_path(case_dir)
    if not path.is_file():
        return {"schema": INDEX_SCHEMA, "revisions": [], "latest": None}
    data = _read_json(path, "revisions/index.json")
    if not isinstance(data.get("revisions"), list):
        raise CloseError("INPUT_REQUIRED", "revisions/index.json needs revisions")
    return data


def _revision_dir(case_dir: Path, revision_id: str) -> Path:
    return _revisions_dir(case_dir) / revision_id


def _case_id(case_dir: Path) -> str:
    return _text(load_period_job(Path(case_dir) / "period-job.json").get("job_id"))


def _bundle_root(case_dir: Path) -> Optional[Path]:
    scan_path = Path(case_dir) / "intake" / "scan.json"
    if not scan_path.is_file():
        return None
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    root = Path(_text(scan.get("bundle_root")))
    if root.is_dir():
        return root
    local = Path(case_dir) / "bundle"
    return local if local.is_dir() else None


def _rewrite_bundle_root(case_dir: Path,
                         bundle_root: Path = None) -> None:
    scan_path = Path(case_dir) / "intake" / "scan.json"
    if not scan_path.is_file():
        return
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    bundle = (Path(bundle_root) if bundle_root is not None
              else Path(case_dir) / "bundle")
    scan["bundle_root"] = str(bundle)
    _write_json(scan_path, scan)


def _source_snapshot(case_dir: Path) -> List[Dict[str, Any]]:
    scan = _read_json(Path(case_dir) / "intake" / "scan.json", "intake/scan.json")
    snapshot = []
    for role, records in (scan.get("sources") or {}).items():
        for record in records:
            snapshot.append({
                "role": _text(role),
                "path": _text(record.get("path")),
                "sha256": _text(record.get("sha256")),
            })
    snapshot.sort(key=lambda item: (item["role"], item["path"]))
    return snapshot


def _input_snapshot_hash(case_dir: Path) -> str:
    """Semantic input identity; formatting-only job changes stay idempotent."""
    job = load_period_job(Path(case_dir) / "period-job.json")
    identity = {
        "job_id": job["job_id"],
        "buyer": _text(job.get("buyer")),
        "vendor": job["vendor"],
        "account": job["account"],
        "currency": job["currency"],
        "period": dict(job["period"]),
        "rule_map": job.get("rule_map"),
        "task_economics_requested": job.get("task_economics_requested") is True,
        "sources": _source_snapshot(case_dir),
    }
    return _fingerprint(identity)


def _rule_map_hash(case_dir: Path) -> str:
    path = Path(case_dir) / "canonical" / "rule-map.json"
    return _sha_file(path) if path.is_file() else ""


def _report(case_dir: Path) -> Optional[Dict[str, Any]]:
    path = Path(case_dir) / "report" / "report.json"
    return _read_json(path, "report/report.json") if path.is_file() else None


def _review(case_dir: Path) -> Optional[Dict[str, Any]]:
    path = Path(case_dir) / "review.json"
    return _read_json(path, "review.json") if path.is_file() else None


def _signoff(case_dir: Path) -> Optional[Dict[str, Any]]:
    path = Path(case_dir) / "signoff.json"
    return _read_json(path, "signoff.json") if path.is_file() else None


def _status(case_dir: Path) -> str:
    signoff = _signoff(case_dir)
    if signoff:
        return _text(signoff.get("state")) or "signed"
    if _review(case_dir) is not None:
        return "reviewed"
    if _report(case_dir) is not None:
        return "report_ready"
    if (Path(case_dir) / "calculation.json").is_file():
        return "calculated"
    return "draft"


def _build_record(case_dir: Path, revision_id: str,
                  parent_revision_id: Optional[str], source_added: List[str],
                  source_removed: List[str], source_changed: List[str],
                  reason: str = "", created_at: str = "") -> Dict[str, Any]:
    case_dir = Path(case_dir)
    job = load_period_job(case_dir / "period-job.json")
    calculation = (_read_json(case_dir / "calculation.json", "calculation.json")
                   if (case_dir / "calculation.json").is_file() else None)
    report = _report(case_dir)
    review = _review(case_dir)
    signoff = _signoff(case_dir)
    confirmation = (_read_json(case_dir / "intake" / "mapping-confirmed.json",
                               "intake/mapping-confirmed.json")
                    if (case_dir / "intake" / "mapping-confirmed.json").is_file()
                    else None)
    reviewer = ""
    if signoff:
        reviewer = _text(signoff.get("reviewer"))
    elif review:
        reviewer = _text(review.get("reviewer"))
    elif confirmation:
        reviewer = _text(confirmation.get("confirmed_by"))
    return {
        "schema": REVISION_SCHEMA,
        "period_job_id": job["job_id"],
        "revision_id": revision_id,
        "parent_revision_id": parent_revision_id,
        "input_snapshot_hash": _input_snapshot_hash(case_dir),
        "rule_map_hash": _rule_map_hash(case_dir),
        "source_snapshot": _source_snapshot(case_dir),
        "source_added": sorted(source_added),
        "source_removed": sorted(source_removed),
        "source_changed": sorted(source_changed),
        "reviewer": reviewer or None,
        "closed_at": (signoff or {}).get("decided_at") or None,
        "status": _status(case_dir),
        "reason": _text(reason),
        "created_at": created_at or _now(),
        "report_fingerprint": ((report or {}).get("replay") or {}).get(
            "output_snapshot_fingerprint"),
        "calculation_fingerprint": (calculation or {}).get(
            "calculation_fingerprint"),
        "canonical_snapshot_hash": ((calculation or {}).get(
            "canonical_snapshot") or {}).get("fingerprint"),
        "signoff": signoff,
    }


def _summary(record: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("revision_id", "parent_revision_id", "status",
            "input_snapshot_hash", "rule_map_hash", "created_at", "closed_at",
            "reviewer", "source_added", "source_removed", "source_changed")
    return {key: record.get(key) for key in keys}


# ---------------------------------------------------------------------------
# write-ahead journal and recovery
#
# A close transaction touches more than one file: a revision directory plus
# revision.json / delta.json / index.json, or signoff.json plus the revision and
# index entries.  The journal is fsynced *before* the first externally visible
# write and lists every path with the SHA-256 the transaction expects.  Readers
# and writers inspect it before doing anything else: a resumable transaction is
# completed and reported, a conflict is reported with the completed/pending
# steps (never silently ignored).
# ---------------------------------------------------------------------------

class JournalConflict(CloseError):
    """A journal exists but the on-disk state no longer matches it."""

    def __init__(self, message: str):
        super().__init__("REVIEW_REQUIRED", message)


# Tests replace this with an explicit hook; never a timing signal.
_CRASH_HOOK = None


def _checkpoint(stage: str, info: Dict[str, Any]) -> None:
    hook = _CRASH_HOOK
    if hook is not None:
        hook(stage, info)


_CHECKPOINT_STAGES = {
    ("snapshot", "promote"): "snapshot:promoted",
    ("snapshot", "record"): "snapshot:built",
    ("revise", "promote"): "revise:promoted",
    ("revise", "record"): "revise:record-written",
    ("revise", "delta"): "revise:delta-written",
    ("revise", "index"): "revise:indexed",
    ("signoff", "signoff"): "signoff:signoff-written",
    ("signoff", "record"): "signoff:revision-written",
    ("signoff", "index"): "signoff:indexed",
}


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_tree(root: Path) -> None:
    root = Path(root)
    directories = [root]
    for path in sorted(root.rglob("*")):
        if path.is_file():
            with open(path, "rb") as handle:
                os.fsync(handle.fileno())
        elif path.is_dir():
            directories.append(path)
    for directory in reversed(directories):
        _fsync_dir(directory)


def _write_bytes_durable(path: Path, raw: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temp), str(path))
    _fsync_dir(path.parent)


def _write_json_durable(path: Path, payload: Any) -> None:
    _write_bytes_durable(path, _json_bytes(payload))


def _unlink_durable(path: Path) -> None:
    path = Path(path)
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _fsync_dir(path.parent)


def _case_rel(case_dir: Path, path: Path) -> str:
    return Path(path).relative_to(Path(case_dir)).as_posix()


def _journal_path(case_dir: Path, revision_id: str) -> Path:
    return _revisions_dir(case_dir) / (JOURNAL_PREFIX + revision_id + ".json")


def _journal_paths(case_dir: Path) -> List[Path]:
    root = _revisions_dir(case_dir)
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob(JOURNAL_PREFIX + "*.json")
                  if path.is_file())


def _revision_id_from_journal(path: Path) -> str:
    name = path.name
    if name.startswith(JOURNAL_PREFIX) and name.endswith(".json"):
        return name[len(JOURNAL_PREFIX):-len(".json")]
    return ""


def _write_descriptor(case_dir: Path, step: str, path: Path, payload: Any,
                      before: Any = None) -> Dict[str, Any]:
    raw = _json_bytes(payload)
    return {
        "step": step,
        "path": _case_rel(case_dir, path),
        "sha256": _sha_bytes(raw),
        "payload": payload,
        "before": before,
        "before_sha256": _sha_bytes(_json_bytes(before))
        if before is not None else None,
    }


def _text_descriptor(case_dir: Path, step: str, path: Path,
                     text: str) -> Dict[str, Any]:
    """A journal write whose bytes are the raw UTF-8 text, not a JSON encoding.

    Only used for newly created derived artifacts (a signed report copy), so no
    ``before`` payload is needed: rollback unlinks the file.
    """
    raw = _text(text).encode("utf-8")
    return {
        "step": step,
        "path": _case_rel(case_dir, path),
        "sha256": _sha_bytes(raw),
        "text": _text(text),
        "before": None,
        "before_sha256": None,
    }


def _tree_hashes(root: Path, prefix: str = "") -> List[Dict[str, Any]]:
    root = Path(root)
    prefix = prefix.rstrip("/")
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            entries.append({
                "path": ("%s/%s" % (prefix, relative) if prefix else relative),
                "sha256": _sha_file(path),
                "bytes": path.stat().st_size,
            })
    return entries


def _tree_problems(root: Path, tree: List[Dict[str, Any]],
                   allowed: List[str] = None,
                   prefix: str = "") -> List[Dict[str, Any]]:
    root = Path(root)
    prefix = (prefix or "").rstrip("/")
    problems = []
    expected = {}
    for item in tree or []:
        declared = _text(item.get("path"))
        relative = declared
        if prefix and declared.startswith(prefix + "/"):
            relative = declared[len(prefix) + 1:]
        expected[relative] = _text(item.get("sha256"))
        path = root / relative
        if not path.is_file():
            problems.append({"path": declared, "problem": "missing"})
            continue
        actual = _sha_file(path)
        if actual != expected[relative]:
            problems.append({"path": declared, "problem": "hash_changed",
                             "expected": expected[relative], "actual": actual})
    allowed_paths = set(expected) | set(allowed or [])
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative.endswith(".tmp"):
                continue
            if relative not in allowed_paths:
                problems.append({"path": relative, "problem": "unexpected"})
    return problems


def _writes_under(journal: Dict[str, Any], final_rel: Optional[str]) -> List[str]:
    if not final_rel:
        return []
    prefix = final_rel.rstrip("/") + "/"
    return [item["path"][len(prefix):]
            for item in journal.get("writes") or []
            if _text(item.get("path")).startswith(prefix)]


def _inspect_promote(case_dir: Path,
                     journal: Dict[str, Any]) -> Dict[str, Any]:
    staging_rel = _text(journal.get("staging")) or None
    final_rel = _text(journal.get("final")) or None
    tree = journal.get("tree") or []
    step = {"name": "promote", "state": "conflict", "problems": []}
    if not final_rel:
        step["state"] = "done"
        return step
    final = Path(case_dir) / final_rel
    staging = (Path(case_dir) / staging_rel) if staging_rel else None
    allowed = _writes_under(journal, final_rel)
    if final.is_dir():
        problems = _tree_problems(final, tree, allowed,
                                  prefix=final_rel)
        if problems:
            step["problems"] = problems
            return step
        step["state"] = "done"
        if staging is not None and staging.exists():
            step["note"] = "stale staging left behind"
        return step
    if staging is not None and staging.is_dir():
        problems = _tree_problems(staging, tree, prefix=final_rel)
        if problems:
            step["problems"] = problems
            return step
        step["state"] = "pending"
        step["note"] = "staging intact"
        return step
    step["problems"] = [{"path": final_rel,
                         "problem": "staging_and_final_missing"}]
    return step


def _inspect_write(case_dir: Path, write: Dict[str, Any]) -> Dict[str, Any]:
    relative = _text(write.get("path"))
    step = {"name": _text(write.get("step")), "path": relative,
            "state": "pending", "problems": []}
    path = Path(case_dir) / relative
    if path.is_file():
        actual = _sha_file(path)
        if actual == _text(write.get("sha256")):
            step["state"] = "done"
        elif (write.get("before_sha256")
              and actual == _text(write.get("before_sha256"))):
            step["note"] = "old content still in place"
        else:
            step["state"] = "conflict"
            step["problems"] = [{"path": relative, "problem": "hash_changed",
                                 "expected": write.get("sha256"),
                                 "actual": actual}]
    return step


def _inspect_journal(case_dir: Path,
                     journal_path: Path) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    info = {
        "kind": "journal",
        "journal": journal_path.name,
        "path": str(journal_path),
        "revision_id": _revision_id_from_journal(journal_path),
        "state": "conflict",
        "steps": [],
        "problems": [],
    }
    try:
        data = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        info["message"] = ("journal 损坏，需人工核对：%s（%s）"
                           % (journal_path.name, exc))
        return info, None
    if not isinstance(data, dict) or data.get("schema") != JOURNAL_SCHEMA:
        info["message"] = ("journal 格式不受支持，需人工核对：%s"
                           % journal_path.name)
        return info, None
    info["revision_id"] = _text(data.get("revision_id")) or info["revision_id"]
    info["journal_kind"] = _text(data.get("kind"))
    info["parent_revision_id"] = data.get("parent_revision_id")
    steps = []
    if _text(data.get("staging")):
        steps.append(_inspect_promote(case_dir, data))
    for write in data.get("writes") or []:
        if isinstance(write, dict):
            steps.append(_inspect_write(case_dir, write))
    seen_incomplete = False
    for step in steps:
        if step["state"] == "done" and seen_incomplete:
            step["state"] = "conflict"
            step["problems"].append({"problem": "out_of_order"})
        if step["state"] in ("pending", "conflict"):
            seen_incomplete = True
    info["steps"] = steps
    info["problems"] = [problem for step in steps
                        for problem in step.get("problems") or []]
    if any(step["state"] == "conflict" for step in steps):
        info["state"] = "conflict"
        info["message"] = ("上一次修订未完成，需人工核对：%s（%s）"
                           % (info["revision_id"], info["journal_kind"]))
    elif steps and all(step["state"] == "done" for step in steps):
        info["state"] = "complete"
        info["message"] = ("journal 记录的事务已全部落盘，待清理：%s"
                           % info["revision_id"])
    else:
        info["state"] = "resumable"
        info["message"] = ("可完成上一次中断的修订：%s（%s）"
                           % (info["revision_id"], info["journal_kind"]))
    return info, data


def _apply_journal(case_dir: Path, journal: Dict[str, Any],
                   journal_path: Path) -> None:
    case_dir = Path(case_dir)
    kind = _text(journal.get("kind"))
    revision_id = _text(journal.get("revision_id"))
    staging_rel = _text(journal.get("staging")) or None
    final_rel = _text(journal.get("final")) or None
    tree = journal.get("tree") or []
    if staging_rel:
        staging = case_dir / staging_rel
        final = case_dir / final_rel
        allowed = _writes_under(journal, final_rel)
        if final.is_dir():
            problems = _tree_problems(final, tree, allowed,
                                  prefix=final_rel)
            if problems:
                raise JournalConflict(
                    "恢复冲突：已提升的 revision 与 journal 不一致：%s"
                    % json.dumps(problems, ensure_ascii=False))
            if staging.exists():
                shutil.rmtree(staging)
                _fsync_dir(staging.parent)
        else:
            if not staging.is_dir():
                raise JournalConflict(
                    "恢复冲突：staging 与 final 均不存在：%s" % staging_rel)
            problems = _tree_problems(staging, tree, prefix=final_rel)
            if problems:
                raise JournalConflict(
                    "恢复冲突：staging 与 journal 不一致：%s"
                    % json.dumps(problems, ensure_ascii=False))
            staging.rename(final)
            _fsync_dir(final.parent)
            stage = _CHECKPOINT_STAGES.get((kind, "promote"))
            if stage:
                _checkpoint(stage, {"revision_id": revision_id, "kind": kind,
                                    "step": "promote"})
    for write in journal.get("writes") or []:
        path = case_dir / _text(write.get("path"))
        if path.is_file():
            actual = _sha_file(path)
            if actual == _text(write.get("sha256")):
                continue
            if not (write.get("before_sha256")
                    and actual == _text(write.get("before_sha256"))):
                raise JournalConflict(
                    "恢复冲突：%s 与 journal 期望的 hash 不一致"
                    % write.get("path"))
        if "text" in write:
            _write_bytes_durable(path, _text(write.get("text")).encode("utf-8"))
        else:
            _write_bytes_durable(path, _json_bytes(write.get("payload")))
        stage = _CHECKPOINT_STAGES.get((kind, _text(write.get("step"))))
        if stage:
            _checkpoint(stage, {"revision_id": revision_id, "kind": kind,
                                "step": _text(write.get("step"))})
    # Verify the complete post-state before dropping the journal.
    if staging_rel:
        problems = _tree_problems(case_dir / final_rel, tree,
                                  _writes_under(journal, final_rel),
                                  prefix=final_rel)
        if problems:
            raise JournalConflict(
                "恢复冲突：完成后的 revision 仍与 journal 不一致：%s"
                % json.dumps(problems, ensure_ascii=False))
    for write in journal.get("writes") or []:
        path = case_dir / _text(write.get("path"))
        if not path.is_file() or _sha_file(path) != _text(write.get("sha256")):
            raise JournalConflict(
                "恢复冲突：%s 未能写入 journal 期望的内容" % write.get("path"))
    _unlink_durable(journal_path)


def _rollback_journal(case_dir: Path, journal: Dict[str, Any],
                      journal_path: Path) -> None:
    case_dir = Path(case_dir)
    for write in reversed(journal.get("writes") or []):
        path = case_dir / _text(write.get("path"))
        if path.is_file():
            actual = _sha_file(path)
            if actual == _text(write.get("before_sha256")):
                continue
            if actual != _text(write.get("sha256")):
                raise JournalConflict(
                    "回滚拒绝：%s 在 journal 之后被其他写入改动"
                    % write.get("path"))
        before = write.get("before")
        if before is None:
            _unlink_durable(path)
        else:
            _write_bytes_durable(path, _json_bytes(before))
    staging_rel = _text(journal.get("staging")) or None
    if staging_rel:
        final_rel = _text(journal.get("final")) or None
        staging = case_dir / staging_rel
        if staging.exists():
            shutil.rmtree(staging)
        if final_rel:
            final = case_dir / final_rel
            if final.exists():
                shutil.rmtree(final)
                _fsync_dir(final.parent)
    _unlink_durable(journal_path)


def _rollback_corrupt_journal(case_dir: Path,
                              issue: Dict[str, Any]) -> None:
    revision_id = _text(issue.get("revision_id"))
    _unlink_durable(Path(issue["path"]))
    if not revision_id:
        return
    final = _revision_dir(case_dir, revision_id)
    if final.exists():
        shutil.rmtree(final)
        _fsync_dir(final.parent)
    _drop_index_entry(case_dir, revision_id)


def _drop_index_entry(case_dir: Path, revision_id: str) -> None:
    path = _index_path(case_dir)
    if not path.is_file():
        return
    index = _load_index(case_dir)
    remaining = [item for item in index.get("revisions") or []
                 if _text(item.get("revision_id")) != revision_id]
    if len(remaining) == len(index.get("revisions") or []):
        return
    index["revisions"] = remaining
    ids = [_text(item.get("revision_id")) for item in remaining]
    index["latest"] = ids[-1] if ids else None
    _write_json_durable(path, index)


def _source_problems(case_dir: Path) -> List[Dict[str, Any]]:
    try:
        snapshot = _source_snapshot(case_dir)
        bundle = _bundle_root(case_dir)
    except CloseError as exc:
        return [{"problem": "source_snapshot_unavailable", "detail": str(exc)}]
    if bundle is None or not Path(bundle).is_dir():
        return [{"problem": "bundle_missing", "path": str(bundle)}]
    problems = []
    for item in snapshot:
        path = Path(bundle) / item["path"]
        if not path.is_file():
            problems.append({"path": item["path"],
                             "problem": "source_missing"})
        elif _sha_file(path) != item["sha256"]:
            problems.append({"path": item["path"],
                             "problem": "source_hash_changed",
                             "expected": item["sha256"],
                             "actual": _sha_file(path)})
    return problems


def _verify_orphan(case_dir: Path, revision_id: str,
                   index: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]],
                                                   List[Dict[str, Any]]]:
    final = _revision_dir(case_dir, revision_id)
    record_path = final / "revision.json"
    if not record_path.is_file():
        return None, [{"problem": "missing_revision_record",
                       "path": _case_rel(case_dir, record_path)}]
    try:
        record = _read_json(record_path, "revision.json")
    except CloseError as exc:
        return None, [{"problem": "invalid_revision_record",
                       "detail": str(exc)}]
    problems = []
    if _text(record.get("revision_id")) != revision_id:
        problems.append({"problem": "revision_id_mismatch",
                         "expected": revision_id,
                         "actual": record.get("revision_id")})
    ids = [_text(item.get("revision_id"))
           for item in index.get("revisions") or []]
    parent = record.get("parent_revision_id")
    if parent is None:
        if ids:
            problems.append({"problem": "r1_orphan_with_nonempty_index"})
    else:
        parent = _text(parent)
        if parent not in ids:
            problems.append({"problem": "parent_not_indexed", "parent": parent})
        elif _text(index.get("latest")) != parent:
            problems.append({"problem": "not_a_direct_child_of_latest",
                             "parent": parent,
                             "latest": index.get("latest")})
    try:
        if record.get("input_snapshot_hash") != _input_snapshot_hash(final):
            problems.append({"problem": "input_snapshot_hash_mismatch"})
    except CloseError as exc:
        problems.append({"problem": "input_snapshot_unavailable",
                         "detail": str(exc)})
    if record.get("rule_map_hash") != _rule_map_hash(final):
        problems.append({"problem": "rule_map_hash_mismatch"})
    calculation_path = final / "calculation.json"
    if not calculation_path.is_file():
        problems.append({"problem": "missing_calculation"})
    else:
        calculation = _read_json(calculation_path, "calculation.json")
        if (calculation.get("calculation_fingerprint")
                != record.get("calculation_fingerprint")):
            problems.append({"problem": "calculation_fingerprint_mismatch"})
        canonical_hash = ((calculation.get("canonical_snapshot") or {})
                          .get("fingerprint"))
        if canonical_hash != record.get("canonical_snapshot_hash"):
            problems.append({"problem": "canonical_snapshot_mismatch"})
    report_path = final / "report" / "report.json"
    if not report_path.is_file():
        problems.append({"problem": "missing_report"})
    else:
        report = _read_json(report_path, "report/report.json")
        fingerprint = (report.get("replay") or {}).get(
            "output_snapshot_fingerprint")
        if fingerprint != record.get("report_fingerprint"):
            problems.append({"problem": "report_fingerprint_mismatch"})
        elif fingerprint and fingerprint != _report_fingerprint(report):
            problems.append({"problem": "report_tampered"})
    problems.extend(_source_problems(final))
    if revision_id != "r1":
        delta_path = final / "delta.json"
        if not delta_path.is_file():
            problems.append({"problem": "missing_delta"})
        else:
            delta = _read_json(delta_path, "delta.json")
            if _text(delta.get("from_revision")) != _text(parent):
                problems.append({"problem": "delta_from_mismatch"})
            if _text(delta.get("to_revision")) != revision_id:
                problems.append({"problem": "delta_to_mismatch"})
    return (record if not problems else None), problems


def _adopt_revision(case_dir: Path, revision_id: str,
                    record: Dict[str, Any]) -> bool:
    index = _load_index(case_dir)
    ids = [_text(item.get("revision_id"))
           for item in index.get("revisions") or []]
    if revision_id in ids:
        return False
    index.setdefault("schema", INDEX_SCHEMA)
    index.setdefault("case_id", _case_id(case_dir))
    index["revisions"].append(_summary(record))
    index["latest"] = revision_id
    _write_json_durable(_index_path(case_dir), index)
    return True


def _scan_orphans(case_dir: Path,
                  index: Dict[str, Any]) -> List[Dict[str, Any]]:
    root = _revisions_dir(case_dir)
    if not root.is_dir():
        return []
    indexed = {_text(item.get("revision_id"))
               for item in index.get("revisions") or []}
    issues = []
    for path in sorted(root.iterdir()):
        if path.is_file():
            continue
        name = path.name
        if name.startswith(STAGING_PREFIX):
            issues.append({
                "kind": "staging",
                "revision_id": name[len(STAGING_PREFIX):],
                "state": "stale_staging",
                "path": str(path),
                "message": "发现遗留的 staging 目录：%s" % name,
                "steps": [],
            })
            continue
        if not REVISION_ID_RE.match(name):
            issues.append({
                "kind": "unknown",
                "revision_id": name,
                "state": "conflict",
                "path": str(path),
                "message": "revisions/ 下出现无法识别的目录：%s" % name,
                "steps": [],
            })
            continue
        if name in indexed:
            continue
        record, problems = _verify_orphan(case_dir, name, index)
        issues.append({
            "kind": "orphan",
            "revision_id": name,
            "state": "resumable" if record is not None else "conflict",
            "path": str(path),
            "record": record,
            "problems": problems,
            "message": ("发现未入索引的修订：%s" % name if record is not None
                        else "未入索引的修订 %s 无法校验，需人工核对" % name),
            "steps": [],
        })
    return issues


def _inspect_all(case_dir: Path) -> List[Dict[str, Any]]:
    issues = []
    journal_ids = set()
    for path in _journal_paths(case_dir):
        info, data = _inspect_journal(case_dir, path)
        info["data"] = data
        journal_ids.add(_text(info.get("revision_id")))
        issues.append(info)
    index = _load_index(case_dir)
    for orphan in _scan_orphans(case_dir, index):
        if _text(orphan.get("revision_id")) in journal_ids:
            continue
        issues.append(orphan)
    return issues


def _public_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in issue.items()
            if key not in ("data", "record")}


def _issue_summary(issue: Dict[str, Any]) -> str:
    revision_id = _text(issue.get("revision_id")) or "?"
    kind = _text(issue.get("journal_kind")) or _text(issue.get("kind"))
    steps = issue.get("steps") or []
    detail = ", ".join("%s=%s" % (step.get("name"), step.get("state"))
                       for step in steps)
    if not detail:
        detail = _text(issue.get("message"))
    return "%s（%s）：%s" % (revision_id, kind, detail)


def ensure_recovered(case_dir: Path, *, strict: bool = False) -> Dict[str, Any]:
    """Complete pending close journals before an entry point reads or writes."""
    case_dir = Path(case_dir)
    issues = _inspect_all(case_dir)
    remaining = []
    recovered = []
    for issue in issues:
        if issue.get("kind") == "journal":
            data = issue.get("data")
            if issue.get("state") == "complete" and data is not None:
                _unlink_durable(Path(issue["path"]))
                recovered.append(issue)
                continue
            if issue.get("state") == "resumable" and data is not None:
                try:
                    _apply_journal(case_dir, data, Path(issue["path"]))
                except JournalConflict as exc:
                    conflict = dict(issue)
                    conflict["state"] = "conflict"
                    conflict["message"] = str(exc)
                    remaining.append(conflict)
                    continue
                recovered.append(issue)
                continue
            remaining.append(issue)
            continue
        if issue.get("kind") == "orphan" and issue.get("state") == "resumable":
            _adopt_revision(case_dir, _text(issue.get("revision_id")),
                            issue.get("record") or {})
            recovered.append(issue)
            continue
        remaining.append(issue)
    if remaining:
        conflicted = any(issue.get("state") == "conflict"
                        for issue in remaining)
        if conflicted:
            message = ("上一次修订未完成，需人工核对："
                       + "；".join(_issue_summary(issue)
                                  for issue in remaining))
        else:
            message = ("发现待恢复的修订目录："
                       + "；".join(_issue_summary(issue)
                                  for issue in remaining))
        result = {
            "state": "conflict" if conflicted else "orphan",
            "message": message,
            "issues": [_public_issue(issue) for issue in remaining],
            "recovered": [_public_issue(issue) for issue in recovered],
        }
        if strict and conflicted:
            raise CloseError("REVIEW_REQUIRED", message)
        return result
    if recovered:
        return {
            "state": "recovered",
            "message": ("已恢复上一次中断的修订："
                        + "；".join(_issue_summary(issue)
                                   for issue in recovered)),
            "issues": [],
            "recovered": [_public_issue(issue) for issue in recovered],
        }
    return {"state": "clean", "message": "没有待恢复的修订事务",
            "issues": [], "recovered": []}


def recover(case_dir: Path, *, apply: bool = False,
            rollback: bool = False) -> Dict[str, Any]:
    """Inspect pending journals; dry-run unless --apply / --rollback."""
    if apply and rollback:
        raise CloseError("INPUT_REQUIRED",
                         "choose either --apply or --rollback")
    case_dir = Path(case_dir)
    issues = _inspect_all(case_dir)
    public = [_public_issue(issue) for issue in issues]
    if not issues:
        return {"state": "clean", "issues": [], "applied": [],
                "rolled_back": []}
    if apply:
        conflicts = [issue for issue in issues
                     if issue.get("state") == "conflict"]
        if conflicts:
            raise CloseError(
                "REVIEW_REQUIRED",
                "recover --apply 拒绝执行，需人工核对："
                + "；".join(_issue_summary(issue) for issue in conflicts))
        applied = []
        for issue in issues:
            if issue.get("kind") == "journal":
                _apply_journal(case_dir, issue.get("data") or {},
                               Path(issue["path"]))
            elif issue.get("kind") == "orphan":
                _adopt_revision(case_dir, _text(issue.get("revision_id")),
                                issue.get("record") or {})
            elif issue.get("kind") == "staging":
                path = Path(issue["path"])
                if path.exists():
                    shutil.rmtree(path)
                    _fsync_dir(path.parent)
            applied.append(_public_issue(issue))
        return {"state": "applied", "applied": applied, "issues": [],
                "rolled_back": []}
    if rollback:
        rolled_back = []
        for issue in issues:
            if issue.get("kind") == "journal":
                if issue.get("data") is None:
                    _rollback_corrupt_journal(case_dir, issue)
                else:
                    _rollback_journal(case_dir, issue.get("data") or {},
                                      Path(issue["path"]))
            elif issue.get("kind") == "orphan":
                path = Path(issue["path"])
                if path.exists():
                    shutil.rmtree(path)
                    _fsync_dir(path.parent)
                _drop_index_entry(case_dir,
                                  _text(issue.get("revision_id")))
            elif issue.get("kind") == "staging":
                path = Path(issue["path"])
                if path.exists():
                    shutil.rmtree(path)
                    _fsync_dir(path.parent)
            rolled_back.append(_public_issue(issue))
        return {"state": "rolled_back", "applied": [], "issues": [],
                "rolled_back": rolled_back}
    return {"state": "planned", "issues": public, "applied": [],
            "rolled_back": []}


def _report_fingerprint(report: Dict[str, Any]) -> str:
    body = dict(report)
    body.pop("generated_at", None)
    body["replay"] = dict(body.get("replay") or {})
    body["replay"].pop("output_snapshot_fingerprint", None)
    return _fingerprint(body)


def _ensure_root_snapshot(case_dir: Path) -> Dict[str, Any]:
    """Snapshot the V-02/V-03 root as r1 through the same journal as revise."""
    case_dir = Path(case_dir)
    index = _load_index(case_dir)
    if index.get("revisions"):
        return index
    if not (case_dir / "report" / "report.json").is_file():
        raise CloseError("INPUT_REQUIRED",
                         "the current period has no report; run close calculate "
                         "and close report before creating a revision")
    root = _revisions_dir(case_dir) / "r1"
    if root.exists():
        record, problems = _verify_orphan(case_dir, "r1", index)
        if record is not None:
            _adopt_revision(case_dir, "r1", record)
            return _load_index(case_dir)
        raise CloseError(
            "REVIEW_REQUIRED",
            "revisions/r1 exists but cannot be verified; run close recover "
            "--apply or --rollback before revising (%s)"
            % json.dumps(problems, ensure_ascii=False))
    staging = _revisions_dir(case_dir) / (STAGING_PREFIX + "r1")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for name in ("period-job.json", "intake", "canonical", "calculation.json",
                 "report", "review.json", "signoff.json"):
        source = case_dir / name
        if source.is_dir():
            shutil.copytree(source, staging / name, symlinks=True)
        elif source.is_file():
            shutil.copy2(source, staging / name)
    bundle = _bundle_root(case_dir)
    if bundle is not None:
        shutil.copytree(bundle, staging / "bundle", symlinks=True)
        _rewrite_bundle_root(staging, root / "bundle")
    record = _build_record(
        staging, "r1", None,
        [item["path"] for item in _source_snapshot(staging)],
        [], [], reason="initial snapshot")
    index = {
        "schema": INDEX_SCHEMA,
        "case_id": _case_id(case_dir),
        "revisions": [_summary(record)],
        "latest": "r1",
    }
    journal = {
        "schema": JOURNAL_SCHEMA,
        "kind": "snapshot",
        "revision_id": "r1",
        "parent_revision_id": None,
        "created_at": _now(),
        "input_snapshot_hash": record["input_snapshot_hash"],
        "staging": _case_rel(case_dir, staging),
        "final": _case_rel(case_dir, root),
        "tree": _tree_hashes(staging, _case_rel(case_dir, root)),
        "writes": [
            _write_descriptor(case_dir, "record", root / "revision.json",
                              record),
            _write_descriptor(case_dir, "index", _index_path(case_dir), index),
        ],
        "steps": ["promote", "record", "index"],
    }
    _fsync_tree(staging)
    journal_path = _journal_path(case_dir, "r1")
    _write_json_durable(journal_path, journal)
    _apply_journal(case_dir, journal, journal_path)
    return _load_index(case_dir)


def _validate_same_identity(parent: Dict[str, Any], child: Dict[str, Any]) -> None:
    for field in ("job_id", "buyer", "vendor", "account", "currency"):
        if _text(parent.get(field)) != _text(child.get(field)):
            raise CloseError("UNSUPPORTED_SCOPE",
                             "a revision cannot change %s (%r -> %r)"
                             % (field, parent.get(field), child.get(field)))
    for field in ("from", "to", "to_exclusive", "timezone"):
        if parent["period"].get(field) != child["period"].get(field):
            raise CloseError("UNSUPPORTED_SCOPE",
                             "a revision cannot change period.%s" % field)


def _diff_sources(before: List[Dict[str, Any]],
                  after: List[Dict[str, Any]]) -> Tuple[List[str], List[str], List[str]]:
    def keyed(items: List[Dict[str, Any]]) -> Dict[Tuple[str, str], str]:
        return {(item["role"], item["path"]): item["sha256"] for item in items}

    old, new = keyed(before), keyed(after)
    added = sorted(path for role, path in new if (role, path) not in old)
    removed = sorted(path for role, path in old if (role, path) not in new)
    changed = sorted(path for (role, path), sha in new.items()
                     if (role, path) in old and old[(role, path)] != sha)
    return added, removed, changed


def _parent_control_domains(parent_dir: Path,
                            parent_job: Dict[str, Any],
                            new_job: Dict[str, Any]) -> Dict[str, str]:
    """Inherit a control domain only for an unchanged role/path source.

    A revised export from the same source keeps its control domain; a new or
    renamed source must be resolved explicitly (``--control-domain``).
    """
    path = parent_dir / "intake" / "mapping-confirmed.json"
    if not path.is_file():
        return {}
    confirmation = json.loads(path.read_text(encoding="utf-8"))
    parent_paths = {entry["role"]: entry["path"] for entry in parent_job["sources"]}
    new_paths = {entry["role"]: entry["path"] for entry in new_job["sources"]}
    domains = {}
    for role, entry in (confirmation.get("sources") or {}).items():
        if not isinstance(entry, dict) or not _text(entry.get("control_domain")):
            continue
        if parent_paths.get(role) == new_paths.get(role):
            domains[role] = _text(entry["control_domain"])
    return domains


# ---------------------------------------------------------------------------
# V-07：同一账期的跨进程互斥
# ---------------------------------------------------------------------------

CASE_LOCK_NAME = ".close.lock"


def _case_lock(case_dir):
    """独占本账期：两个进程同时 revise/signoff 时后来者得到明确冲突。

    journal 解决的是"单进程崩溃后能恢复"，不解决两个进程同时写同一个 rN /
    同一个签核三件套。这里用 flock 非阻塞独占；拿不到锁就 fail closed，
    不排队、不猜测谁赢。
    """
    import contextlib

    @contextlib.contextmanager
    def locked():
        case = Path(case_dir)
        case.mkdir(parents=True, exist_ok=True)
        path = case / CASE_LOCK_NAME
        handle = open(path, "a+")
        try:
            try:
                import fcntl
            except ImportError:  # Windows：退化成 O_EXCL 锁文件
                fcntl = None
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    raise CloseError(
                        "REVIEW_REQUIRED",
                        "并发冲突：另一个进程正在处理这个账期（%s）。"
                        "等它结束后重跑；不要并行修订或签核同一个账期。" % path.name)
            yield
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    return locked()


def revise(case_dir: Path, *, bundle: Path = None, source: Path = None,
           role: str = "", reason: str = "", reviewer: str,
           control_domains: Dict[str, str] = None) -> Dict[str, Any]:
    """Create a new independent revision; same input is a no-op."""
    with _case_lock(case_dir):
        return _revise_locked(case_dir, bundle=bundle, source=source, role=role,
                              reason=reason, reviewer=reviewer,
                              control_domains=control_domains)


def _revise_locked(case_dir: Path, *, bundle: Path = None, source: Path = None,
                   role: str = "", reason: str = "", reviewer: str,
                   control_domains: Dict[str, str] = None) -> Dict[str, Any]:
    case_dir = Path(case_dir)
    reviewer = _text(reviewer)
    if not reviewer:
        raise CloseError("INPUT_REQUIRED",
                         "a revision needs --review-by; no mapping is auto-confirmed")
    if bool(bundle) == bool(source):
        raise CloseError("INPUT_REQUIRED",
                         "provide exactly one of --bundle or --source")
    if bundle and role:
        raise CloseError("INPUT_REQUIRED",
                         "--role 只能和 --source 一起用于具名新增或替换单个来源；"
                         "--bundle 的每个材料必须在 period-job.sources 声明")
    if role and role not in ROLES:
        raise CloseError("INPUT_REQUIRED",
                         "unknown source role %s; available roles: %s" % (role, ", ".join(sorted(ROLES))))
    ensure_recovered(case_dir, strict=True)
    index = _ensure_root_snapshot(case_dir)
    parent_id = index["latest"]
    parent_dir = _revision_dir(case_dir, parent_id)
    parent_record = _read_json(parent_dir / "revision.json", "revision.json")
    parent_job = load_period_job(parent_dir / "period-job.json")
    parent_bundle = _bundle_root(parent_dir)
    if parent_bundle is None:
        raise CloseError("STALE_SOURCE",
                         "the parent revision has no available bundle snapshot")
    next_id = "r%d" % (len(index["revisions"]) + 1)
    staging = _revisions_dir(case_dir) / (STAGING_PREFIX + next_id)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    job = deepcopy(parent_job)

    if bundle is not None:
        new_bundle = Path(bundle)
        if not new_bundle.is_dir():
            raise CloseError("INPUT_REQUIRED", "revision bundle not found: %s"
                             % new_bundle)
        new_job_path = new_bundle / "period-job.json"
        if new_job_path.is_file():
            job = load_period_job(new_job_path)
            _validate_same_identity(parent_job, job)
        declared = {_text(entry.get("path")) for entry in job["sources"]}
        undeclared = sorted(
            path.relative_to(new_bundle).as_posix()
            for path in new_bundle.rglob("*")
            if path.is_file() and path.suffix.lower() in TABLE_SUFFIXES | INVOICE_SUFFIXES
            and path.relative_to(new_bundle).as_posix() not in declared)
        if undeclared:
            shutil.rmtree(staging)
            raise CloseError(
                "INPUT_REQUIRED",
                "bundle 含 period-job.sources 未声明的材料：%s；"
                "请用 --source --role 具名新增，或在 period-job.sources 明确登记" % ", ".join(undeclared))
        shutil.copytree(new_bundle, staging / "bundle", symlinks=True)
    else:
        source_path = Path(source)
        if not source_path.is_file():
            raise CloseError("INPUT_REQUIRED", "revision source not found: %s"
                             % source_path)
        shutil.copytree(parent_bundle, staging / "bundle", symlinks=True)
        entries = job["sources"]
        if role:
            matches = [entry for entry in entries if entry["role"] == role]
        else:
            matches = [entry for entry in entries
                       if Path(entry["path"]).name == source_path.name]
        if len(matches) > 1 or (not matches and not role):
            raise CloseError("INPUT_REQUIRED",
                             "cannot identify the source role; pass --role "
                             "(matches: %d)" % len(matches))
        if not matches:
            entry = {"role": role, "path": source_path.name,
                     "control_domain": "buyer_or_vendor_pending_review"}
            entries.append(entry)
        else:
            entry = matches[0]
        old_rel = entry["path"]
        new_rel = (old_rel if Path(old_rel).name == source_path.name else
                   (Path(old_rel).parent / source_path.name).as_posix())
        target = staging / "bundle" / new_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target)
        if new_rel != old_rel:
            stale = staging / "bundle" / old_rel
            if stale.is_file():
                stale.unlink()
        entry["path"] = new_rel
        entry["sha256"] = _sha_file(target)

    (staging / "new-job.json").write_text(
        json.dumps(job, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    init_case(staging, job_path=staging / "new-job.json")
    (staging / "new-job.json").unlink()
    ingest(staging, bundle=staging / "bundle")
    domains = _parent_control_domains(parent_dir, parent_job, job)
    domains.update(control_domains or {})
    confirm_mapping(staging, reviewer=reviewer, control_domains=domains)
    close_report.calculate(staging)
    close_report.build_report(staging, revision_id=next_id)

    final = _revision_dir(case_dir, next_id)
    _rewrite_bundle_root(staging, final / "bundle")
    added, removed, changed = _diff_sources(
        parent_record.get("source_snapshot") or _source_snapshot(parent_dir),
        _source_snapshot(staging))
    record = _build_record(staging, next_id, parent_id, added, removed, changed,
                           reason=reason)
    if (record["input_snapshot_hash"] == parent_record["input_snapshot_hash"]
            and record["rule_map_hash"] == parent_record["rule_map_hash"]):
        shutil.rmtree(staging)
        return {"revision": parent_record, "delta": None, "idempotent": True}
    if final.exists():
        # V-04 crashed between rename() and index.json.  The directory is a
        # committed revision, not a version to overwrite: adopt it when it
        # matches, refuse with a recovery instruction when it does not.
        try:
            existing = _read_json(final / "revision.json", "revision.json")
        except CloseError:
            existing = None
        if (existing is not None
                and existing.get("input_snapshot_hash")
                == record["input_snapshot_hash"]
                and existing.get("rule_map_hash") == record["rule_map_hash"]):
            shutil.rmtree(staging)
            _adopt_revision(case_dir, next_id, existing)
            delta_path = final / "delta.json"
            delta = (_read_json(delta_path, "delta.json")
                     if delta_path.is_file() else None)
            return {"revision": existing, "delta": delta,
                    "idempotent": True, "recovery": "adopted_unindexed"}
        shutil.rmtree(staging)
        raise CloseError(
            "REVIEW_REQUIRED",
            "revision %s exists without an index entry and does not match this "
            "input; run close recover %s --apply or --rollback before revising"
            % (next_id, case_dir))
    delta = _build_delta_dirs(parent_dir, staging, to_name=next_id,
                              after_record=record)
    index_after = deepcopy(index)
    index_after["revisions"].append(_summary(record))
    index_after["latest"] = next_id
    journal = {
        "schema": JOURNAL_SCHEMA,
        "kind": "revise",
        "revision_id": next_id,
        "parent_revision_id": parent_id,
        "created_at": _now(),
        "input_snapshot_hash": record["input_snapshot_hash"],
        "staging": _case_rel(case_dir, staging),
        "final": _case_rel(case_dir, final),
        "tree": _tree_hashes(staging, _case_rel(case_dir, final)),
        "writes": [
            _write_descriptor(case_dir, "record", final / "revision.json",
                              record),
            _write_descriptor(case_dir, "delta", final / "delta.json", delta),
            _write_descriptor(case_dir, "index", _index_path(case_dir),
                              index_after, before=index),
        ],
        "steps": ["promote", "record", "delta", "index"],
    }
    _fsync_tree(staging)
    journal_path = _journal_path(case_dir, next_id)
    _write_json_durable(journal_path, journal)
    _apply_journal(case_dir, journal, journal_path)
    return {"revision": record, "delta": delta, "idempotent": False,
            "path": str(final)}


def _amount_change(before: Any, after: Any, key: str) -> Dict[str, Any]:
    a = _dec(before, "%s before" % key)
    b = _dec(after, "%s after" % key)
    if a is None or b is None:
        return {"before": before, "after": after, "delta": None,
                "status": "unknown"}
    return {"before": _money(a), "after": _money(b),
            "delta": _money(b - a), "status": "comparable"}


def _findings(report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {"%s|%s" % (item.get("kind"), item.get("line_ref") or ""): item
            for item in report.get("findings") or []}


def _verdict_changes(before: Dict[str, Any],
                     after: Dict[str, Any]) -> List[Dict[str, Any]]:
    changes = []
    before_verdicts = before.get("verdicts") or {}
    after_verdicts = after.get("verdicts") or {}
    for name in after_verdicts:
        old = _text((before_verdicts.get(name) or {}).get("status"))
        new = _text((after_verdicts.get(name) or {}).get("status"))
        if old != new:
            changes.append({"verdict": name, "before": old, "after": new})
    return changes


def _credit_lines(report: Dict[str, Any]) -> Tuple[List[Dict[str, Any]],
                                                   List[Dict[str, Any]]]:
    applied, pending = [], []
    for line in report.get("line_reconciliation") or []:
        if line.get("adjustment_type") not in CREDIT_TYPES:
            continue
        item = {
            "line_ref": line.get("line_ref"),
            "type": line.get("adjustment_type"),
            "state": line.get("adjustment_state"),
            "amount": line.get("amount"),
            "currency": line.get("currency"),
        }
        if line.get("classification") == "explained_adjustment":
            applied.append(item)
        elif line.get("classification") == "excluded_pending_adjustment":
            pending.append(item)
    return applied, pending


def _build_delta_dirs(before_dir: Path, after_dir: Path, *,
                      to_name: str = None,
                      after_record: Dict[str, Any] = None) -> Dict[str, Any]:
    before = _read_json(before_dir / "report" / "report.json", "report/report.json")
    after = _read_json(after_dir / "report" / "report.json", "report/report.json")
    before_amounts = before.get("amounts") or {}
    after_amounts = after.get("amounts") or {}
    amounts = {key: _amount_change(before_amounts.get(key),
                                   after_amounts.get(key), key)
               for key in AMOUNT_KEYS}
    axes = {axis: {"before": before.get(axis), "after": after.get(axis)}
            for axis in AXES}
    changed_axes = [axis for axis in AXES
                    if axes[axis]["before"] != axes[axis]["after"]]

    before_findings = _findings(before)
    after_findings = _findings(after)
    new_findings = sorted(set(after_findings) - set(before_findings))
    cleared_findings = sorted(set(before_findings) - set(after_findings))
    changed_findings = []
    for key in sorted(set(before_findings) & set(after_findings)):
        old, new = before_findings[key], after_findings[key]
        if (old.get("state") != new.get("state")
                or old.get("confirmed_cause") != new.get("confirmed_cause")
                or old.get("candidate_causes") != new.get("candidate_causes")):
            changed_findings.append({
                "key": key,
                "before": {"state": old.get("state"),
                           "confirmed_cause": old.get("confirmed_cause")},
                "after": {"state": new.get("state"),
                          "confirmed_cause": new.get("confirmed_cause")},
            })

    before_sources = {(item["role"], item["path"]): item["sha256"]
                      for item in _source_snapshot(before_dir)}
    after_sources = {(item["role"], item["path"]): item["sha256"]
                     for item in _source_snapshot(after_dir)}
    new_sources = sorted(path for role, path in after_sources
                         if (role, path) not in before_sources)
    removed_sources = sorted(path for role, path in before_sources
                             if (role, path) not in after_sources)
    changed_sources = sorted(path for (role, path), sha in after_sources.items()
                             if (role, path) in before_sources
                             and before_sources[(role, path)] != sha)
    verdict_changes = _verdict_changes(before, after)
    newly_available = list(new_sources)
    for change in verdict_changes:
        if (change["before"] in ("UNPROVABLE", "UNKNOWN", "not_applicable", "")
                and change["after"] in ("PASS", "FAIL")):
            newly_available.append("%s:%s" % (change["verdict"], change["after"]))

    before_applied, before_pending = _credit_lines(before)
    after_applied, after_pending = _credit_lines(after)
    applied_keys = {(item["line_ref"], item["amount"]) for item in before_applied}
    applied_this_revision = [item for item in after_applied
                             if (item["line_ref"], item["amount"])
                             not in applied_keys]
    pending_keys = {(item["line_ref"], item["type"], item["amount"],
                     item["state"]) for item in before_pending}
    issued_this_revision = [
        item for item in after_pending
        if (item["line_ref"], item["type"], item["amount"], item["state"])
        not in pending_keys]
    credits = {
        "applied": _amount_change(before_amounts.get("credits_applied"),
                                  after_amounts.get("credits_applied"),
                                  "credits_applied"),
        "pending": _amount_change(before_amounts.get("credits_pending"),
                                  after_amounts.get("credits_pending"),
                                  "credits_pending"),
        "realized": _amount_change(before_amounts.get("realized_savings"),
                                   after_amounts.get("realized_savings"),
                                   "realized_savings"),
        "issued_not_applied": after_pending,
        "issued_this_revision": issued_this_revision,
        "applied_this_revision": applied_this_revision,
        "classification": {
            "amount_changes": [key for key, value in amounts.items()
                               if value["delta"] not in (None, "0", "0.00")],
            "cause_changes": sorted(set(new_findings + cleared_findings)
                                    | {item["key"] for item in changed_findings}),
            "evidence_added": newly_available,
            "credit_applied": applied_this_revision,
            "credit_issued_not_applied": issued_this_revision,
        },
    }
    return {
        "schema": DELTA_SCHEMA,
        "from_revision": before_dir.name,
        "to_revision": to_name or after_dir.name,
        "amounts": amounts,
        "axes": axes,
        "changed_axes": changed_axes,
        "causes": {
            "new_findings": new_findings,
            "cleared_findings": cleared_findings,
            "changed_findings": changed_findings,
        },
        "evidence": {
            "new_sources": new_sources,
            "removed_sources": removed_sources,
            "changed_sources": changed_sources,
            "verdict_changes": verdict_changes,
            "newly_available": newly_available,
        },
        "credits": credits,
        "rules": {
            "before": _rule_map_hash(before_dir),
            "after": _rule_map_hash(after_dir),
            "changed": _rule_map_hash(before_dir) != _rule_map_hash(after_dir),
        },
        "revision": {
            "before": _read_json(before_dir / "revision.json",
                                 "revision.json").get("status"),
            "after": ((after_record if after_record is not None else
                       _read_json(after_dir / "revision.json",
                                  "revision.json")).get("status")),
        },
        "classification": {
            "amount_changes": credits["classification"]["amount_changes"],
            "cause_changes": credits["classification"]["cause_changes"],
            "evidence_added": credits["classification"]["evidence_added"],
            "credit_applied": applied_this_revision,
            "credit_issued_not_applied": issued_this_revision,
        },
    }


def build_delta(case_dir: Path, from_revision: str,
                to_revision: str) -> Dict[str, Any]:
    case_dir = Path(case_dir)
    return _build_delta_dirs(_revision_dir(case_dir, from_revision),
                             _revision_dir(case_dir, to_revision))


def load_revision(case_dir: Path, revision_id: str) -> Dict[str, Any]:
    return _read_json(_revision_dir(Path(case_dir), revision_id) / "revision.json",
                      "revision.json")


def list_revisions(case_dir: Path) -> List[Dict[str, Any]]:
    case_dir = Path(case_dir)
    records = []
    for summary in _load_index(case_dir).get("revisions") or []:
        revision_id = _text(summary.get("revision_id"))
        path = _revision_dir(case_dir, revision_id) / "revision.json"
        records.append(_read_json(path, "revision.json")
                       if path.is_file() else summary)
    return records


def latest_revision(case_dir: Path) -> Optional[Dict[str, Any]]:
    index = _load_index(Path(case_dir))
    latest = index.get("latest")
    return load_revision(case_dir, latest) if latest else None


def resolve_case_dir(case_dir: Path, revision_id: str = "") -> Path:
    case_dir = Path(case_dir)
    index = _load_index(case_dir)
    if revision_id:
        if revision_id == "r1" and not index.get("revisions"):
            # Before the first revise, the root layout *is* r1.
            return case_dir
        target = _revision_dir(case_dir, revision_id)
        if not target.is_dir():
            raise CloseError("INPUT_REQUIRED", "revision not found: %s"
                             % revision_id)
        return target
    latest = index.get("latest")
    if latest:
        return _revision_dir(case_dir, latest)
    return case_dir


def _verify_signed_triple(case_dir: Path, revision: Dict[str, Any],
                          signoff: Dict[str, Any]) -> None:
    """signoff.json, revision.json and index.json must tell one story."""
    if revision is None:
        return
    revision_id = _text(revision.get("revision_id"))
    index = _load_index(case_dir)
    entry = next((item for item in index.get("revisions") or []
                  if _text(item.get("revision_id")) == revision_id), None)
    problems = []
    if entry is None:
        problems.append("index.json 没有 %s 的条目" % revision_id)
    else:
        if not (entry.get("status") == revision.get("status")
                == signoff.get("state")):
            problems.append("status 不一致：signoff=%r revision=%r index=%r"
                            % (signoff.get("state"), revision.get("status"),
                               entry.get("status")))
        if not (entry.get("closed_at") == revision.get("closed_at")
                == signoff.get("decided_at")):
            problems.append("closed_at 不一致")
        if not (entry.get("reviewer") == revision.get("reviewer")
                == signoff.get("reviewer")):
            problems.append("reviewer 不一致")
    if revision.get("signoff") != signoff:
        problems.append("revision.json 内的 signoff 与 signoff.json 不一致")
    if problems:
        raise CloseError(
            "REVIEW_REQUIRED",
            "signoff/revision/index 三处不一致，需人工核对或运行 close recover："
            + "；".join(problems))


def _verify_signoff_freshness(case_dir: Path, target: Path,
                              revision: Optional[Dict[str, Any]],
                              existing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Re-verify the signed surface from the bytes on disk, right before writing.

    A signoff is only meaningful over the exact report, canonical snapshot and
    source files it was produced from.  A changed canonical bundle, a changed
    source export or a hand-edited report must refuse with the step to rerun
    instead of producing a second, inconsistent signoff.
    """
    def stale(message):
        raise CloseError("STALE_SOURCE", message)

    report_path = target / "report" / "report.json"
    if not report_path.is_file():
        raise CloseError("REVIEW_REQUIRED",
                         "this revision has no report; run close revise to build "
                         "a report before signing")
    report = _read_json(report_path, "report/report.json")
    if not (target / "calculation.json").is_file():
        stale("calculation.json is missing; run close revise before signing")
    calculation = _read_json(target / "calculation.json", "calculation.json")
    expected_calculation = _fingerprint(
        close_report._calculation_projection(calculation.get("outcome") or {}))
    if calculation.get("calculation_fingerprint") != expected_calculation:
        stale("calculation.json no longer matches its own fingerprint; run close "
              "revise instead of trusting an edited calculation")
    try:
        snapshot = close_report._canonical_snapshot(target)
        current_inputs = close_report._input_hashes(target)
    except CloseError as exc:
        stale("the canonical bundle is unavailable or incomplete (%s); run close "
              "revise" % exc)
    if (snapshot["fingerprint"]
            != (calculation.get("canonical_snapshot") or {}).get("fingerprint")):
        stale("the canonical bundle changed after calculation; run close revise")
    stored_inputs = calculation.get("input_hashes") or {}
    for key in sorted(current_inputs):
        if stored_inputs.get(key) != current_inputs[key]:
            stale("%s changed after calculation; run close revise" % key)

    replay = report.get("replay") or {}
    actual_report_fingerprint = _report_fingerprint(report)
    if replay.get("output_snapshot_fingerprint") != actual_report_fingerprint:
        stale("report/report.json does not match its own fingerprint; the report "
              "was edited or truncated — run close revise to rebuild it")
    if replay.get("calculation_fingerprint") != calculation.get(
            "calculation_fingerprint"):
        stale("the report was built from a different calculation; run close "
              "revise")
    if replay.get("canonical_fingerprint") != snapshot["fingerprint"]:
        stale("the report was built from a different canonical snapshot; run "
              "close revise")
    for key in ("period_job_sha256", "mapping_confirmed_sha256",
                "rule_map_sha256"):
        if replay.get(key) != current_inputs.get(key):
            stale("report replay input %s changed; run close revise" % key)

    source_snapshot = _source_snapshot(target)
    source_problems = _source_problems(target)
    if source_problems:
        stale("source files changed after the report was built (%s); run close "
              "revise with the new export"
              % json.dumps(source_problems, ensure_ascii=False))
    if revision is not None:
        revision_id = _text(revision.get("revision_id"))
        if revision.get("input_snapshot_hash") != _input_snapshot_hash(target):
            stale("revision %s input snapshot no longer matches its sources; run "
                  "close revise" % revision_id)
        if revision.get("rule_map_hash") != _rule_map_hash(target):
            stale("revision %s rule map changed; run close revise" % revision_id)
        if (revision.get("calculation_fingerprint")
                != calculation.get("calculation_fingerprint")):
            stale("revision %s calculation fingerprint changed; run close revise"
                  % revision_id)
        if (revision.get("canonical_snapshot_hash")
                != snapshot["fingerprint"]):
            stale("revision %s canonical snapshot changed; run close revise"
                  % revision_id)
        if (revision.get("report_fingerprint")
                != replay.get("output_snapshot_fingerprint")):
            stale("revision %s report fingerprint changed; run close revise"
                  % revision_id)

    if existing is not None:
        checks = (
            ("report_fingerprint", replay.get("output_snapshot_fingerprint"),
             "report"),
            ("calculation_fingerprint", calculation.get(
                "calculation_fingerprint"), "calculation"),
            ("canonical_snapshot_fingerprint", snapshot["fingerprint"],
             "canonical snapshot"),
            ("input_hashes", stored_inputs, "input hashes"),
            ("source_snapshot", source_snapshot, "source snapshot"),
            ("rule_map_hash", _text(report.get("rule_map_hash")),
             "rule map"),
        )
        for key, current, label in checks:
            if key in existing and existing.get(key) != current:
                stale("the signed %s no longer matches the %s on disk; a later "
                      "source must create a new revision — run close revise"
                      % (label, label))
        stored_attestation = existing.get("record_fingerprint")
        if stored_attestation:
            body = {key: value for key, value in existing.items()
                    if key != "record_fingerprint"}
            if stored_attestation != _fingerprint(body):
                stale("signoff.json no longer matches its own record "
                      "fingerprint; reviewer, scope or timing declarations were "
                      "edited after signing — do not overwrite it")
        _verify_signed_triple(case_dir, revision, existing)
    return {
        "report": report,
        "calculation": calculation,
        "snapshot": snapshot,
        "source_snapshot": source_snapshot,
        "input_hashes": stored_inputs,
    }


def signoff(case_dir: Path, *, reviewer: str, approved_scope: str = "",
            reservations: List[str] = None, note: str = "",
            revision_id: str = "") -> Dict[str, Any]:
    """Record a named period signoff; never mutates the report or closes items."""
    with _case_lock(case_dir):
        return _signoff_locked(case_dir, reviewer=reviewer,
                               approved_scope=approved_scope,
                               reservations=reservations, note=note,
                               revision_id=revision_id)


def _require_signoff_authority(report: Dict[str, Any], reviewer: str) -> Dict[str, Any]:
    authority = report.get("acceptance_authority") or {}
    signers = [_text(item) for item in authority.get("authorized_signers") or [] if _text(item)]
    normalized_reviewer = _text(reviewer).casefold()
    matched = next((signer for signer in signers
                    if signer.casefold() == normalized_reviewer), None)
    if matched is None:
        raise CloseError(
            "AUTH_REQUIRED",
            "signoff reviewer must be one of canonical manifest.buyer.acceptance_signers; "
            "a name in --by alone does not grant signing authority")
    return {"org_id": authority.get("org_id"), "source": authority.get("source"),
            "matched_signer": matched}


def _signoff_locked(case_dir: Path, *, reviewer: str, approved_scope: str = "",
                    reservations: List[str] = None, note: str = "",
                    revision_id: str = "") -> Dict[str, Any]:
    case_dir = Path(case_dir)
    ensure_recovered(case_dir, strict=True)
    reviewer = _text(reviewer)
    if not reviewer:
        raise CloseError("INPUT_REQUIRED",
                         "signoff needs --by; an Agent cannot sign for a human")
    target = resolve_case_dir(case_dir, revision_id)
    revision = (_read_json(target / "revision.json", "revision.json")
                if (target / "revision.json").is_file() else None)
    existing = _signoff(target)
    verified = _verify_signoff_freshness(case_dir, target, revision, existing)
    report = verified["report"]
    calculation = verified["calculation"]
    snapshot = verified["snapshot"]
    source_snapshot = verified["source_snapshot"]
    if existing is not None:
        raise CloseError("REVIEW_REQUIRED",
                         "this revision is already signed; a later source must "
                         "create a new revision, never overwrite the signoff")
    if revision is not None:
        index = _load_index(case_dir)
        entry = next((item for item in index.get("revisions") or []
                      if _text(item.get("revision_id"))
                      == _text(revision.get("revision_id"))), None)
        if entry is None:
            raise CloseError(
                "REVIEW_REQUIRED",
                "index.json does not know revision %s; run close recover "
                "--apply before signing" % revision.get("revision_id"))
        if (entry.get("status") != revision.get("status")
                or entry.get("closed_at") != revision.get("closed_at")
                or entry.get("reviewer") != revision.get("reviewer")):
            raise CloseError(
                "REVIEW_REQUIRED",
                "revision.json and index.json disagree about revision %s; run "
                "close recover --apply before signing"
                % revision.get("revision_id"))
    signer_authority = _require_signoff_authority(report, reviewer)
    unresolved = [item for item in report.get("open_items") or []
                  if item.get("state") not in ("resolved", "closed",
                                               "not_applicable")]
    state = "signed_with_open_items" if unresolved else "signed"
    decided_at = _now()
    source_hashes = list(report.get("source_hashes") or [])
    rule_map_hash = _text(report.get("rule_map_hash"))
    scope_hash = _fingerprint({
        "case_id": report.get("job_id"),
        "revision_id": (revision or {}).get("revision_id", "r1"),
        "approved_scope": _text(approved_scope),
        "source_hashes": source_hashes,
        "source_snapshot": source_snapshot,
        "rule_map_hash": rule_map_hash,
    })
    record = {
        "schema": SIGNOFF_SCHEMA,
        "job_id": report.get("job_id"),
        "revision_id": (revision or {}).get("revision_id", "r1"),
        "state": state,
        "reviewer": reviewer,
        "decided_at": decided_at,
        "attestation": "attested_by_declaration",
        "signer_authority": signer_authority,
        "approved_scope": _text(approved_scope),
        "reservations": [item for item in (reservations or []) if _text(item)],
        "note": _text(note),
        "scope_hash": scope_hash,
        "source_hashes": source_hashes,
        "source_snapshot": source_snapshot,
        "rule_map_hash": rule_map_hash,
        "report_fingerprint": (report.get("replay") or {}).get(
            "output_snapshot_fingerprint"),
        "calculation_fingerprint": calculation.get("calculation_fingerprint"),
        "canonical_snapshot_fingerprint": snapshot["fingerprint"],
        "input_hashes": verified["input_hashes"],
        "unresolved_items": [{
            "id": item.get("id"), "kind": item.get("kind"),
            "line_ref": item.get("line_ref"), "state": item.get("state"),
            "amount": item.get("amount"), "currency": item.get("currency"),
            "owner": item.get("owner"), "next_evidence": item.get("next_evidence"),
        } for item in unresolved],
        "payment_authorization": False,
        "boundary": ("A named period signoff is a declaration over this revision's "
                     "source and rule fingerprints. It is not payment "
                     "authorization, does not close unresolved items, and does not "
                     "transfer to a later revision."),
    }
    record["record_fingerprint"] = _fingerprint(record)
    delivered = deepcopy(report)
    delivered["signoff"] = {
        "schema": SIGNOFF_SCHEMA,
        "revision_id": record["revision_id"],
        "state": state,
        "reviewer": reviewer,
        "decided_at": decided_at,
        "attestation": record["attestation"],
        "approved_scope": record["approved_scope"],
        "reservations": record["reservations"],
        "note": record["note"],
        "scope_hash": scope_hash,
        "report_fingerprint": record["report_fingerprint"],
        "payment_authorization": False,
        "boundary": record["boundary"],
    }
    delivered["delivery"] = {
        "kind": "signed_report_copy",
        "source": "report/report.json",
        "source_report_fingerprint": record["report_fingerprint"],
        "signed": True,
        "source_snapshot_preserved": True,
    }
    delivered_md = close_report._report_markdown(delivered).rstrip("\n")
    delivered_md += "\n\n## 签核\n\n"
    delivered_md += "- 签核状态：%s\n" % state
    delivered_md += "- 签核人：%s\n" % reviewer
    delivered_md += "- 签核时间：%s\n" % decided_at
    delivered_md += "- 核验范围指纹：%s\n" % scope_hash
    delivered_md += "- 付款授权：False（签核是声明，不等于付款授权）\n"
    delivered_md += ("- 说明：已签核不等于所有差异已解决（未闭合项 %d 条）。\n"
                     % len(unresolved))
    delivered_md += ("- 边界：%s\n" % record["boundary"])
    if record["reservations"]:
        delivered_md += "- 保留意见：%s\n" % "；".join(record["reservations"])
    writes = [_write_descriptor(case_dir, "signoff", target / "signoff.json",
                                record)]
    if revision is not None:
        updated = deepcopy(revision)
        updated["status"] = state
        updated["closed_at"] = decided_at
        updated["reviewer"] = reviewer
        updated["signoff"] = record
        index = _load_index(case_dir)
        index_after = deepcopy(index)
        for item in index_after.get("revisions") or []:
            if item.get("revision_id") == updated["revision_id"]:
                item["status"] = state
                item["closed_at"] = decided_at
                item["reviewer"] = reviewer
                break
        writes.append(_write_descriptor(case_dir, "record",
                                        target / "revision.json", updated,
                                        before=revision))
        writes.append(_write_descriptor(case_dir, "index",
                                        _index_path(case_dir), index_after,
                                        before=index))
    writes.append(_text_descriptor(case_dir, "report-delivered-md",
                                   target / "report" / "report-delivered.md",
                                   delivered_md))
    writes.append(_write_descriptor(case_dir, "report-delivered-json",
                                    target / "report" / "report-delivered.json",
                                    delivered))
    journal = {
        "schema": JOURNAL_SCHEMA,
        "kind": "signoff",
        "revision_id": (revision or {}).get("revision_id", "r1"),
        "parent_revision_id": (revision or {}).get("parent_revision_id"),
        "created_at": _now(),
        "input_snapshot_hash": (revision or {}).get("input_snapshot_hash"),
        "staging": None,
        "final": ("revisions/%s" % revision["revision_id"]
                  if revision is not None else None),
        "tree": [],
        "writes": writes,
        "steps": [item["step"] for item in writes],
    }
    journal_path = _journal_path(case_dir, journal["revision_id"])
    _write_json_durable(journal_path, journal)
    _apply_journal(case_dir, journal, journal_path)
    return record
