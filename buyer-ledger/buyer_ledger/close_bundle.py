"""V-P0-04 close Evidence Bundle: read-only Charges / Delivery / Coverage page.

This module is an **L0 read-only projection**.  It renders artifacts that
already exist in a close case:

    report/report.json      four completion axes, five evidence verdicts,
                            amounts, line reconciliation, findings, open items
    report/manifest.json    artifact hashes for freshness checks
    calculation.json        task economics and outcome evidence
    signoff.json            named sign-off declaration (when signed)
    review.json             named review declaration (when reviewed)
    revision.json           the selected revision descriptor
    revisions/index.json    append-only revision history

It never calculates, never builds or rewrites a report, never approves,
never signs off, never records a difference action, never recovers a
transaction, never sends an inquiry and never retries an unknown external
action.  Missing evidence is shown as missing; UNKNOWN/UNPROVABLE are never
folded into FALSE.
"""
from __future__ import annotations

import hashlib
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import close_report, close_revision
from .close import CloseError

SCHEMA = "verovo.close-bundle/1"
GENERATOR = "buyer_ledger.close_bundle"
GENERATOR_VERSION = "1"

AXES = close_report.AXES
VERDICT_NAMES = close_report.VERDICT_NAMES
AXIS_LABELS_ZH = close_report.AXIS_LABELS_ZH
VERDICT_LABELS_ZH = close_report.VERDICT_LABELS_ZH

PAGE_STATE_LABELS_ZH = {
    "empty": "空案例：没有可投影的工件",
    "current": "当前 revision",
    "signed": "已签核",
    "historical": "历史 revision",
    "stale": "来源已变化，页面过期",
    "partial": "工件不完整",
}

COLUMN_STATE_LABELS_ZH = {
    "complete": "完整",
    "partial": "部分",
    "not_applicable": "未请求",
    "empty": "无数据",
    "not_computed": "未计算",
}

SIGNOFF_STATE_LABELS_ZH = {
    "not_signed": "未签核",
    "signed": "已签核",
    "signed_with_open_items": "已签核（保留未决项）",
    "stale": "签核与当前工件不一致",
    "unreadable": "签核文件无法读取",
}

DELIVERY_GAP_GENERIC = (
    "本页没有独立的验收标准工件（acceptance criteria artifact）；"
    "验收结论只投影已有 outcomes 的声明级证据。")
REQUEST_COVERAGE_REASON = "没有可靠的请求总数分母；本期未计算请求覆盖率"
BOUNDARY_TEXT = (
    "L0 只读投影：不写 canonical、不生成或重建报告、不自动审批、不签核、"
    "不恢复事务、不外发问询、不付款、不重试未知外部动作。所有金额、裁决与"
    "缺口均来自已存在的工件；缺失即显示缺失，UNKNOWN/UNPROVABLE 不折叠为 FALSE。")


# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------

def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _now_iso(now: Optional[datetime] = None) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_optional(path: Path, label: str, faults: List[Dict[str, Any]],
                   missing: List[str]) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        missing.append(label)
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        faults.append({"artifact": label, "path": str(path), "error": str(exc)})
        return None
    if not isinstance(data, dict):
        faults.append({"artifact": label, "path": str(path),
                       "error": "JSON root is not an object"})
        return None
    return data


def _command_case(case_dir: Path) -> str:
    text = str(case_dir)
    return '"%s"' % text if " " in text else text


def _commands(case_dir: Path, revision_id: str) -> List[Dict[str, str]]:
    case = _command_case(case_dir)
    rev = " --revision %s" % revision_id if revision_id else ""
    return [
        {"label": "终端三栏摘要", "command": "python3 -m buyer_ledger close bundle %s%s" % (case, rev)},
        {"label": "页面 JSON", "command": "python3 -m buyer_ledger close bundle %s%s --json" % (case, rev)},
        {"label": "导出 HTML", "command": "python3 -m buyer_ledger close bundle %s%s --html <new-file>.html" % (case, rev)},
        {"label": "只读状态投影", "command": "python3 -m buyer_ledger close status %s%s --format json" % (case, rev)},
        {"label": "只读处置记录", "command": "python3 -m buyer_ledger close action %s --list" % case},
        {"label": "恢复事务 dry-run", "command": "python3 -m buyer_ledger close recover %s" % case},
    ]


# ---------------------------------------------------------------------------
# revision resolution and artifacts
# ---------------------------------------------------------------------------

def _resolve_revision(case_dir: Path, revision: str, faults: List[Dict[str, Any]],
                      warnings: List[str]) -> Dict[str, Any]:
    requested = _text(revision)
    revisions: List[Dict[str, Any]] = []
    latest_record: Optional[Dict[str, Any]] = None
    try:
        revisions = close_revision.list_revisions(case_dir)
        latest_record = close_revision.latest_revision(case_dir)
    except (CloseError, OSError, ValueError) as exc:
        faults.append({"artifact": "revisions/index.json",
                       "path": str(case_dir / "revisions" / "index.json"),
                       "error": str(exc)})
    latest_id = _text((latest_record or {}).get("revision_id"))
    if not latest_id and revisions:
        latest_id = _text(revisions[-1].get("revision_id"))

    target: Path
    revision_id: str
    try:
        if requested:
            target = close_revision.resolve_case_dir(case_dir, requested)
            revision_id = requested
        elif latest_id:
            target = close_revision.resolve_case_dir(case_dir)
            revision_id = latest_id
        else:
            target = case_dir
            revision_id = "r1"
    except (CloseError, OSError, ValueError) as exc:
        faults.append({"artifact": "revision", "path": str(case_dir),
                       "error": str(exc)})
        target = case_dir
        revision_id = requested or latest_id or "r1"

    indexed = bool(revisions)
    historical = bool(indexed and latest_id and revision_id != latest_id)
    if not indexed:
        label = "r1（根目录）"
    else:
        label = revision_id
    if historical:
        warnings.append(
            "本页展示 %s，但当前最新 revision 是 %s；该页是历史只读快照。"
            % (revision_id, latest_id))
    if requested and not indexed and requested != "r1":
        warnings.append("案例尚无 revisions/index.json；请求的 %s 按根目录 %s 解析。"
                        % (requested, revision_id))

    items = []
    for record in revisions:
        signoff = record.get("signoff") if isinstance(record, dict) else None
        items.append({
            "revision_id": record.get("revision_id"),
            "parent_revision_id": record.get("parent_revision_id"),
            "status": record.get("status"),
            "created_at": record.get("created_at"),
            "closed_at": record.get("closed_at"),
            "reason": record.get("reason"),
            "signoff_state": (signoff or {}).get("state")
            if isinstance(signoff, dict) else None,
            "report_fingerprint": record.get("report_fingerprint"),
        })

    return {
        "target": target,
        "revision_id": revision_id,
        "revision_label": label,
        "latest_id": latest_id or (None if not indexed else ""),
        "historical": historical,
        "indexed": indexed,
        "items": items,
        "requested": requested,
    }


def _recovery_state(case_dir: Path, faults: List[Dict[str, Any]],
                    warnings: List[str]) -> Dict[str, Any]:
    try:
        result = close_revision.recover(case_dir)
    except (CloseError, OSError, ValueError) as exc:
        faults.append({"artifact": "recovery", "path": str(case_dir),
                       "error": str(exc)})
        return {"state": "unknown", "issues": [],
                "message": "恢复 dry-run 无法完成：%s" % exc}
    state = _text(result.get("state")) or "unknown"
    issues = result.get("issues") or []
    if state != "clean":
        warnings.append("存在未完成的 revision/signoff 事务（%s）；本页只做 dry-run 展示，"
                        "不会自动 apply 或 rollback。" % state)
    return {
        "state": state,
        "issues": issues,
        "message": ("没有待恢复事务" if state == "clean" else
                    "存在待恢复事务；先人工核对 close recover 的 dry-run 计划"),
    }


def _signoff_view(target: Path, report: Optional[Dict[str, Any]],
                  faults: List[Dict[str, Any]],
                  warnings: List[str]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    signoff_path = target / "signoff.json"
    review_path = target / "review.json"
    signoff = None
    review = None
    unreadable = ""
    if signoff_path.is_file():
        try:
            signoff = json.loads(signoff_path.read_text(encoding="utf-8"))
            if not isinstance(signoff, dict):
                unreadable = "JSON root is not an object"
                signoff = None
        except (OSError, ValueError) as exc:
            unreadable = str(exc)
            signoff = None
    if unreadable:
        faults.append({"artifact": "signoff.json", "path": str(signoff_path),
                       "error": unreadable})
        warnings.append(
            "signoff.json 存在但无法解析（%s）；本页不把它当成未签核，也不当成有效"
            "签核，需人工修复或用新 revision 重新签核。" % unreadable)
        return {
            "present": True,
            "state": "unreadable",
            "label": SIGNOFF_STATE_LABELS_ZH["unreadable"],
            "reviewer": None,
            "decided_at": None,
            "attestation": None,
            "approved_scope": None,
            "reservations": [],
            "note": None,
            "unresolved_items": [],
            "payment_authorization": False,
            "report_fingerprint": None,
            "error": unreadable,
            "source": "signoff.json",
        }, None
    if review_path.is_file():
        try:
            review = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            review = None
    report_review = (report or {}).get("review") or {}
    if signoff:
        state = _text(signoff.get("state")) or "signed"
        return {
            "present": True,
            "state": state,
            "label": SIGNOFF_STATE_LABELS_ZH.get(state, state),
            "reviewer": signoff.get("reviewer"),
            "decided_at": signoff.get("decided_at"),
            "attestation": signoff.get("attestation"),
            "approved_scope": signoff.get("approved_scope"),
            "reservations": signoff.get("reservations") or [],
            "note": signoff.get("note"),
            "unresolved_items": signoff.get("unresolved_items") or [],
            "payment_authorization": signoff.get("payment_authorization", False),
            "report_fingerprint": signoff.get("report_fingerprint"),
            "source": "signoff.json",
        }, signoff
    state = _text(report_review.get("state")) or "not_signed"
    return {
        "present": False,
        "state": state,
        "label": SIGNOFF_STATE_LABELS_ZH.get(state, state),
        "reviewer": report_review.get("by") or (review or {}).get("reviewer"),
        "decided_at": report_review.get("at") or (review or {}).get("recorded_at"),
        "attestation": "attested_by_declaration",
        "approved_scope": report_review.get("approved_scope"),
        "reservations": report_review.get("reservations") or [],
        "note": (review or {}).get("note"),
        "unresolved_items": report_review.get("unresolved_items") or [],
        "payment_authorization": False,
        "report_fingerprint": None,
        "source": "report.review" if report_review else "missing",
    }, None


# ---------------------------------------------------------------------------
# freshness
# ---------------------------------------------------------------------------

def _expect_file(target: Path, rel: str, expected_sha: Any,
                 expected_bytes: Any, name: str) -> Dict[str, Any]:
    path = target / rel
    if not path.is_file():
        return {"check": name, "path": rel, "status": "missing",
                "expected_sha256": expected_sha, "actual_sha256": None,
                "expected_bytes": expected_bytes, "actual_bytes": None,
                "detail": "文件不存在"}
    actual_sha = _sha_file(path)
    actual_bytes = path.stat().st_size
    mismatch = []
    if expected_sha and actual_sha != expected_sha:
        mismatch.append("sha256")
    if expected_bytes is not None and actual_bytes != expected_bytes:
        mismatch.append("bytes")
    return {"check": name, "path": rel,
            "status": "mismatch" if mismatch else "ok",
            "expected_sha256": expected_sha, "actual_sha256": actual_sha,
            "expected_bytes": expected_bytes, "actual_bytes": actual_bytes,
            "detail": ("失配：%s" % "、".join(mismatch)) if mismatch else "一致"}


def _expect_value(name: str, expected: Any, actual: Any) -> Dict[str, Any]:
    if expected in (None, "") or actual in (None, ""):
        return {"check": name, "path": "", "status": "missing",
                "expected_sha256": expected, "actual_sha256": actual,
                "expected_bytes": None, "actual_bytes": None,
                "detail": "缺少可比对的指纹"}
    if _text(expected) == _text(actual):
        return {"check": name, "path": "", "status": "ok",
                "expected_sha256": expected, "actual_sha256": actual,
                "expected_bytes": None, "actual_bytes": None,
                "detail": "一致"}
    return {"check": name, "path": "", "status": "mismatch",
            "expected_sha256": expected, "actual_sha256": actual,
            "expected_bytes": None, "actual_bytes": None,
            "detail": "指纹不一致"}


def _signoff_binding_checks(signoff: Optional[Dict[str, Any]],
                            report: Optional[Dict[str, Any]],
                            calculation: Optional[Dict[str, Any]],
                            revision_record: Optional[Dict[str, Any]]
                            ) -> List[Dict[str, Any]]:
    """A signoff is only valid over the exact report/scope it was declared on."""
    if not signoff:
        return []
    replay = (report or {}).get("replay") or {}
    checks = [
        _expect_value("signoff.job_id", (report or {}).get("job_id"),
                      signoff.get("job_id")),
        _expect_value("signoff.report_fingerprint",
                      replay.get("output_snapshot_fingerprint"),
                      signoff.get("report_fingerprint")),
        _expect_value("signoff.calculation_fingerprint",
                      (calculation or {}).get("calculation_fingerprint"),
                      signoff.get("calculation_fingerprint")),
        _expect_value("signoff.canonical_snapshot_fingerprint",
                      ((calculation or {}).get("canonical_snapshot")
                       or {}).get("fingerprint"),
                      signoff.get("canonical_snapshot_fingerprint")),
    ]
    expected_scope = close_report._fingerprint({
        "case_id": (report or {}).get("job_id"),
        "revision_id": _text(signoff.get("revision_id")) or "r1",
        "approved_scope": _text(signoff.get("approved_scope")),
        "source_hashes": list(signoff.get("source_hashes") or []),
        "source_snapshot": signoff.get("source_snapshot"),
        "rule_map_hash": _text(signoff.get("rule_map_hash")),
    })
    checks.append(_expect_value("signoff.scope_hash", expected_scope,
                                signoff.get("scope_hash")))
    stored_attestation = _text(signoff.get("record_fingerprint"))
    if stored_attestation:
        body = {key: value for key, value in signoff.items()
                if key != "record_fingerprint"}
        checks.append(_expect_value("signoff.record_fingerprint",
                                    close_report._fingerprint(body),
                                    stored_attestation))
    else:
        checks.append({
            "check": "signoff.record_fingerprint", "path": "signoff.json",
            "status": "missing", "expected_sha256": None,
            "actual_sha256": None, "expected_bytes": None,
            "actual_bytes": None,
            "detail": "旧签核没有自指纹，无法验证 reviewer/时间/范围是否被改",
        })
    archived = (revision_record or {}).get("signoff")
    if isinstance(archived, dict):
        for key in ("reviewer", "decided_at", "approved_scope", "state"):
            checks.append(_expect_value(
                "revision.signoff.%s" % key, archived.get(key),
                signoff.get(key)))
    return checks


def _freshness(target: Path, report: Optional[Dict[str, Any]],
               manifest: Optional[Dict[str, Any]],
               calculation: Optional[Dict[str, Any]],
               signoff: Optional[Dict[str, Any]] = None,
               signoff_state: str = "",
               revision_record: Optional[Dict[str, Any]] = None
               ) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    if report:
        replay = report.get("replay") or {}
        for item in replay.get("canonical_files") or []:
            if not isinstance(item, dict):
                continue
            checks.append(_expect_file(
                target, "canonical/%s" % item.get("path"),
                item.get("sha256"), item.get("bytes"),
                "canonical:%s" % item.get("path")))
        for key, rel in (
                ("period_job_sha256", "period-job.json"),
                ("mapping_confirmed_sha256", "intake/mapping-confirmed.json"),
                ("rule_map_sha256", "canonical/rule-map.json")):
            checks.append(_expect_file(target, rel, replay.get(key), None, key))
        if calculation:
            snapshot = calculation.get("canonical_snapshot") or {}
            checks.append(_expect_value(
                "report.replay.calculation_fingerprint",
                replay.get("calculation_fingerprint"),
                calculation.get("calculation_fingerprint")))
            checks.append(_expect_value(
                "report.replay.canonical_fingerprint",
                replay.get("canonical_fingerprint"),
                snapshot.get("fingerprint")))
    if manifest and report:
        replay = report.get("replay") or {}
        inputs = manifest.get("inputs") or {}
        for key in ("period_job_sha256", "mapping_confirmed_sha256",
                    "rule_map_sha256", "canonical_fingerprint"):
            checks.append(_expect_value(
                "manifest.inputs.%s" % key, replay.get(key), inputs.get(key)))
        checks.append(_expect_value(
            "manifest.calculation_fingerprint",
            replay.get("calculation_fingerprint"),
            manifest.get("calculation_fingerprint")))
        checks.append(_expect_value(
            "manifest.output_snapshot_fingerprint",
            replay.get("output_snapshot_fingerprint"),
            manifest.get("output_snapshot_fingerprint")))
        for name, item in sorted((manifest.get("artifacts") or {}).items()):
            item = item if isinstance(item, dict) else {}
            checks.append(_expect_file(
                target, "report/%s" % name, item.get("sha256"),
                item.get("bytes"), "artifact:%s" % name))
    checks.extend(_signoff_binding_checks(signoff, report, calculation,
                                          revision_record))

    if not report:
        state = "unverified"
    elif any(check["status"] == "mismatch" for check in checks):
        state = "stale"
    elif signoff_state == "unreadable":
        state = "unverified"
    elif any(check["status"] == "missing" for check in checks):
        state = "unverified"
    else:
        state = "ok"
    return {"state": state, "checks": checks}


def _apply_signoff_binding(signoff: Dict[str, Any],
                           freshness: Dict[str, Any],
                           warnings: List[str]) -> Dict[str, Any]:
    """A signoff only stands while the fingerprints it was declared on hold."""
    if signoff.get("state") not in ("signed", "signed_with_open_items"):
        return signoff
    broken = [check for check in freshness.get("checks") or []
              if check.get("status") == "mismatch"
              and str(check.get("check", "")).startswith(("signoff.",
                                                          "revision.signoff."))]
    if not broken:
        return signoff
    degraded = dict(signoff)
    degraded["state"] = "stale"
    degraded["label"] = SIGNOFF_STATE_LABELS_ZH["stale"]
    degraded["binding"] = "mismatch"
    degraded["binding_checks"] = [check["check"] for check in broken]
    warnings.append(
        "signoff.json 的签核与当前报告/范围不一致（%s）；该签核不得当作当前"
        "有效签核，需重新签核。" % "、".join(degraded["binding_checks"]))
    return degraded


# ---------------------------------------------------------------------------
# page sections
# ---------------------------------------------------------------------------

AMOUNT_ROWS: Tuple[Tuple[str, str], ...] = (
    ("invoice_total", "发票总额"),
    ("invoice_total_status", "发票总额状态"),
    ("rule_repriced", "规则重算金额"),
    ("rule_repricing_status", "规则重算状态"),
    ("rule_repricing_difference", "规则差异"),
    ("matched_postings", "已匹配入账"),
    ("distinct_source_reconciled", "双来源对账金额"),
    ("buyer_independent_attributable", "买方独立可归属"),
    ("known_adjustments", "已知调整"),
    ("tax_applied", "已计税费"),
    ("credits_applied", "已抵扣信用"),
    ("credits_pending", "待抵扣信用（不计已实现）"),
    ("open_difference", "未解释差异"),
    ("realized_savings", "已实现节省（恒 0.00）"),
)


def _verdict_view(report: Optional[Dict[str, Any]], name: str) -> Dict[str, Any]:
    payload = ((report or {}).get("verdicts") or {}).get(name) or {}
    return {
        "name": name,
        "name_zh": VERDICT_LABELS_ZH.get(name, name),
        "status": payload.get("status") or "not_applicable",
        "counts": payload.get("counts") or {},
        "rows": payload.get("rows") or [],
        "limitations": payload.get("limitations") or [],
    }


def _charges_view(report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not report:
        return {
            "state": "empty", "currency": None, "amounts": [],
            "identity": {}, "identity_holds": None, "amount_basis_note": None,
            "verdicts": [], "lines": [], "findings": [],
            "findings_count": 0, "findings_policy": {},
            "limitations": [],
        }
    amounts = report.get("amounts") or {}
    identity = amounts.get("identity") or {}
    rows = []
    for key, label in AMOUNT_ROWS:
        rows.append({"key": key, "label": label, "value": amounts.get(key)})
    return {
        "state": report.get("finance_status") or "partial",
        "currency": amounts.get("currency") or (report.get("scope") or {}).get("currency"),
        "amounts": rows,
        "identity": identity,
        "identity_holds": identity.get("holds"),
        "amount_basis_note": amounts.get("amount_basis_note"),
        "invoice_total_notes": amounts.get("invoice_total_notes") or [],
        "rule_repricing_missing_lines":
            amounts.get("rule_repricing_missing_lines") or [],
        "verdicts": [_verdict_view(report, name) for name in
                     ("Rule Repriced", "Invoice Reconciled", "Vendor Reconciled")],
        "lines": report.get("line_reconciliation") or [],
        "findings": report.get("findings") or [],
        "findings_count": len(report.get("findings") or []),
        "findings_policy": report.get("findings_policy") or {},
        "limitations": report.get("limitations") or [],
    }


def _delivery_view(report: Optional[Dict[str, Any]],
                   calculation: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not report:
        return {
            "state": "empty", "verdict": None, "buyer_verified": None,
            "verification_status": None, "delivery_status": None,
            "rows": [], "tasks": [], "outcomes": [], "gaps": [],
        }
    outcome_rows = (((calculation or {}).get("outcome") or {})
                    .get("case") or {}).get("outcomes") or []
    by_id = {_text(item.get("outcome_id")): item for item in outcome_rows
             if isinstance(item, dict)}
    delivery = _verdict_view(report, "Delivery Accepted")
    rows = []
    gaps = [DELIVERY_GAP_GENERIC]
    for row in delivery["rows"]:
        merged = dict(row)
        outcome = by_id.get(_text(row.get("id"))) or {}
        merged["task_id"] = row.get("task_id") or outcome.get("task_id") or ""
        merged["accepted_by"] = outcome.get("accepted_by") or ""
        merged["accepted_at"] = outcome.get("accepted_at") or ""
        merged["acceptance_basis"] = (
            "declared_reference_not_independently_verified"
            if merged["accepted_by"] or merged["accepted_at"] else "")
        if merged["status"] in ("PASS", "FAIL") and not (
                merged["accepted_by"] and merged["accepted_at"]):
            gaps.append("任务 %s 的交付验收缺少 accepted_by/accepted_at 具名证据。"
                        % (merged["task_id"] or merged["id"]))
        rows.append(merged)
    if not outcome_rows:
        gaps.append("本期没有 outcomes 来源：Delivery Accepted 为 not_applicable，"
                    "不是 FALSE，也不是 0 条验收。")
    unknown = [row for row in rows if row["status"] in ("UNKNOWN", "UNPROVABLE")]
    if unknown:
        gaps.append("%d 条验收结论是 UNKNOWN/UNPROVABLE；页面保留原因，不猜测结果。"
                    % len(unknown))
    return {
        "state": report.get("delivery_status") or "partial",
        "verdict": delivery,
        "buyer_verified": _verdict_view(report, "Buyer Verified"),
        "verification_status": report.get("verification_status"),
        "delivery_status": report.get("delivery_status"),
        "rows": rows,
        "tasks": (((calculation or {}).get("outcome") or {}).get("case") or {})
        .get("tasks") or [],
        "outcomes": outcome_rows,
        "gaps": gaps,
    }


def _coverage_view(report: Optional[Dict[str, Any]],
                   calculation: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    economics = (((calculation or {}).get("outcome") or {})
                 .get("economics") or {})
    if not report:
        return {
            "state": "empty", "record": {}, "amount": {}, "gross_net": {},
            "boundary": "", "allocation_coverage": [],
            "economics_coverage": economics.get("coverage") or {},
            "request_coverage": {
                "status": "not_computed", "numerator": None,
                "denominator": None, "reason": REQUEST_COVERAGE_REASON},
        }
    coverage = report.get("coverage") or {}
    record = coverage.get("record") or {}
    amount = coverage.get("amount") or {}
    complete = (amount.get("status") == "comparable"
                and record.get("ratio") not in (None, ""))
    return {
        "state": "complete" if complete else "partial",
        "record": record,
        "amount": amount,
        "gross_net": coverage.get("gross_net") or {},
        "boundary": coverage.get("boundary") or "",
        "allocation_coverage": economics.get("allocation_coverage") or [],
        "economics_coverage": economics.get("coverage") or {},
        "request_coverage": {
            "status": "not_computed", "numerator": None,
            "denominator": None, "reason": REQUEST_COVERAGE_REASON},
    }


def _economics_view(report: Optional[Dict[str, Any]],
                    calculation: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    economics = (((calculation or {}).get("outcome") or {})
                 .get("economics") or {})
    totals = economics.get("totals") or {}
    task = (report or {}).get("task_economics") or {}
    roi = totals.get("roi", task.get("roi"))
    roi_reason = totals.get("roi_reason") or ""
    fully_loaded = totals.get("fully_loaded_cost") or task.get("fully_loaded_cost")
    verified_value = totals.get("verified_business_value")
    formula = None
    if roi not in (None, ""):
        formula = {
            "expression": "(verified_business_value - fully_loaded_cost) / fully_loaded_cost",
            "verified_business_value": verified_value,
            "fully_loaded_cost": fully_loaded,
        }
    return {
        "state": task.get("status") or ("not_applicable" if report else "empty"),
        "status": task.get("status"),
        "cost_status": totals.get("cost_status") or task.get("cost_status"),
        "fully_loaded_cost": fully_loaded,
        "attributed_cost_subtotal":
            totals.get("attributed_cost_subtotal") or task.get("attributed_cost_subtotal"),
        "cost_per_verified_success":
            totals.get("cost_per_verified_success") or task.get("cost_per_verified_success"),
        "cost_per_verified_success_reason": totals.get(
            "cost_per_verified_success_reason") or task.get("reason") or "",
        "verified_success_tasks": totals.get("verified_success_tasks"),
        "success_denominator_status": totals.get("success_denominator_status"),
        "verified_business_value": verified_value,
        "roi": roi,
        "roi_reason": roi_reason,
        "roi_formula": formula,
        "loss_events": totals.get("loss_events"),
        "unallocated": economics.get("unallocated") or task.get("unallocated") or [],
        "findings": economics.get("findings") or task.get("findings") or [],
        "coverage_counts": economics.get("coverage") or {},
        "boundary": economics.get("boundary") or "",
    }


def _disputes_view(report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload = report or {}
    actions = payload.get("difference_actions") or {}
    return {
        "state": "empty" if not report else "ready",
        "open_items": payload.get("open_items") or [],
        "difference_actions": actions,
        "external_action_executed": bool(actions.get("external_action_executed")),
        "payment_authorization": bool(actions.get("payment_authorization")),
        "evidence_status": actions.get("evidence_status")
        or "declared_reference_not_independently_verified",
        "boundary": actions.get("boundary") or "",
        "note": ("未解释差异保持待核对；这不是对供应商多收的定性，也没有外发给供应商。"),
    }


def _renewal_view(report: Optional[Dict[str, Any]], revision: Dict[str, Any],
                  signoff: Dict[str, Any],
                  revision_record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    replay = (report or {}).get("replay") or {}
    amounts = (report or {}).get("amounts") or {}
    review = (report or {}).get("review") or {}
    return {
        "period": ((report or {}).get("scope") or {}).get("period"),
        "currency": ((report or {}).get("scope") or {}).get("currency")
        or amounts.get("currency"),
        "rule_versions": replay.get("rule_versions") or [],
        "fingerprints": {
            "canonical": replay.get("canonical_fingerprint"),
            "calculation": replay.get("calculation_fingerprint"),
            "report_output": replay.get("output_snapshot_fingerprint"),
            "rule_map": replay.get("rule_map_sha256"),
            "code_version": replay.get("code_version"),
            "algorithm_versions": replay.get("algorithm_versions") or {},
        },
        "credits": {
            "applied": amounts.get("credits_applied"),
            "pending": amounts.get("credits_pending"),
        },
        "realized_savings": amounts.get("realized_savings"),
        "signoff": signoff,
        "review": {
            "state": review.get("state") or "not_signed",
            "by": review.get("by"),
            "at": review.get("at"),
            "approved_scope": review.get("approved_scope"),
            "reservations": review.get("reservations") or [],
            "next_review": review.get("next_review"),
            "payment_authorization": False,
        },
        "revisions": {
            "indexed": bool(revision.get("indexed")),
            "revision_id": revision.get("revision_id"),
            "latest_id": revision.get("latest_id"),
            "historical": bool(revision.get("historical")),
            "requested": revision.get("requested"),
            "items": revision.get("items") or [],
        },
        "current_revision": {
            "revision_id": (revision_record or {}).get("revision_id"),
            "parent_revision_id": (revision_record or {}).get("parent_revision_id"),
            "status": (revision_record or {}).get("status"),
            "reason": (revision_record or {}).get("reason"),
            "created_at": (revision_record or {}).get("created_at"),
            "closed_at": (revision_record or {}).get("closed_at"),
        },
    }


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def build_bundle(case_dir: Path, revision: str = "", *,
                 now: Optional[datetime] = None) -> Dict[str, Any]:
    """Project an existing close case into a read-only page model."""
    case_dir = Path(case_dir)
    faults: List[Dict[str, Any]] = []
    warnings: List[str] = []
    missing: List[str] = []

    resolved = _resolve_revision(case_dir, revision, faults, warnings)
    target = resolved["target"]
    revision_id = resolved["revision_id"]

    report = _read_optional(target / "report" / "report.json",
                            "report/report.json", faults, missing)
    manifest = _read_optional(target / "report" / "manifest.json",
                              "report/manifest.json", faults, missing)
    calculation = _read_optional(target / "calculation.json",
                                 "calculation.json", faults, missing)
    revision_record = _read_optional(target / "revision.json",
                                     "revision.json", faults, missing)
    if not resolved["indexed"] and "revision.json" in missing:
        # r1 lives in the case root; only indexed revisions carry a descriptor.
        missing.remove("revision.json")
    job = _read_optional(target / "period-job.json", "period-job.json",
                         faults, missing)
    signoff, signoff_record = _signoff_view(target, report, faults, warnings)
    recovery = _recovery_state(case_dir, faults, warnings)
    freshness = _freshness(target, report, manifest, calculation,
                           signoff=signoff_record,
                           signoff_state=_text(signoff.get("state")),
                           revision_record=revision_record)
    signoff = _apply_signoff_binding(signoff, freshness, warnings)

    charges = _charges_view(report)
    delivery = _delivery_view(report, calculation)
    coverage = _coverage_view(report, calculation)
    economics = _economics_view(report, calculation)
    disputes = _disputes_view(report)
    renewal = _renewal_view(report, resolved, signoff, revision_record)

    report_revision_id = _text((report or {}).get("revision_id"))
    if report and report_revision_id and report_revision_id != revision_id:
        warnings.append(
            "report.json 的 revision_id 是 %s，本页按 revision.json/index.json "
            "展示 %s；report 内该字段不参与历史判断。"
            % (report_revision_id, revision_id))

    artifacts_complete = True
    if manifest:
        artifacts = manifest.get("artifacts") or {}
        for name in ("report.md", "report.json", "line-reconciliation.csv",
                     "review-queue.csv", "evidence-index.json"):
            if name not in artifacts:
                artifacts_complete = False
    else:
        artifacts_complete = False

    signed = signoff.get("state") in ("signed", "signed_with_open_items")
    has_inputs = bool(job) or (target / "canonical").is_dir()
    empty_case = not report and not calculation and not has_inputs
    if empty_case:
        state = "empty"
    elif not report:
        state = "partial"
    elif freshness["state"] == "stale":
        state = "stale"
    elif signoff.get("state") == "unreadable":
        state = "partial"
    elif resolved["historical"]:
        state = "historical"
    elif signed:
        state = "signed"
    elif not calculation or not manifest or not artifacts_complete:
        state = "partial"
    else:
        state = "current"

    if state == "stale":
        warnings.append("来源文件或指纹已与报告不一致；本页保留历史报告内容，"
                        "但不得当作当前结论，需重新 calculate/report 后再看。")
    if state == "partial" and (not report
                               or signoff.get("state") != "unreadable"):
        warnings.append("calculation.json 或 report/manifest.json 不完整；"
                        "页面只投影已有工件，缺失结论不补造。")

    sources = []
    if report:
        replay = report.get("replay") or {}
        for item in replay.get("canonical_files") or []:
            if not isinstance(item, dict):
                continue
            entry = _expect_file(
                target, "canonical/%s" % item.get("path"), item.get("sha256"),
                item.get("bytes"), "canonical:%s" % item.get("path"))
            sources.append({
                "label": "canonical/%s" % item.get("path"),
                "path": entry["path"],
                "artifact": "canonical",
                "present": entry["status"] != "missing",
                "sha256": entry["actual_sha256"],
                "bytes": entry["actual_bytes"],
                "status": entry["status"],
            })
        for label, rel in (("period-job.json", "period-job.json"),
                           ("intake/mapping-confirmed.json",
                            "intake/mapping-confirmed.json"),
                           ("canonical/rule-map.json", "canonical/rule-map.json")):
            entry = _expect_file(target, rel, None, None, label)
            sources.append({
                "label": label, "path": rel, "artifact": "input",
                "present": entry["status"] != "missing",
                "sha256": entry["actual_sha256"], "bytes": entry["actual_bytes"],
                "status": entry["status"],
            })
        for name in ("report.md", "report.json", "line-reconciliation.csv",
                     "review-queue.csv", "evidence-index.json", "manifest.json"):
            entry = _expect_file(target, "report/%s" % name, None, None,
                                 "artifact:%s" % name)
            sources.append({
                "label": "report/%s" % name, "path": "report/%s" % name,
                "artifact": "report",
                "present": entry["status"] != "missing",
                "sha256": entry["actual_sha256"], "bytes": entry["actual_bytes"],
                "status": entry["status"],
            })
    if signoff.get("present"):
        entry = _expect_file(target, "signoff.json", None, None, "signoff.json")
        sources.append({
            "label": "signoff.json", "path": "signoff.json",
            "artifact": "sidecar", "present": True,
            "sha256": entry["actual_sha256"], "bytes": entry["actual_bytes"],
            "status": entry["status"],
        })

    scope = (report or {}).get("scope") or {}
    if not scope and job:
        period = job.get("period") or {}
        scope = {
            "buyer": job.get("buyer"), "vendor": job.get("vendor"),
            "account": job.get("account"), "currency": job.get("currency"),
            "period": {"start": period.get("from"), "end": period.get("to"),
                       "to_exclusive": period.get("to_exclusive"),
                       "timezone": period.get("timezone")},
        }
    bundle_meta = {
        "case_id": (report or {}).get("job_id") or (job or {}).get("job_id")
        or case_dir.name,
        "case_path": str(case_dir),
        "resolved_path": str(target),
        "revision_id": revision_id,
        "revision_label": resolved["revision_label"],
        "scope": scope,
        "period": scope.get("period"),
        "currency": scope.get("currency"),
        "signoff": signoff,
        "freshness": freshness,
        "recovery": recovery,
        "report_generated_at": (report or {}).get("generated_at"),
        "calculation_generated_at": (calculation or {}).get("calculated_at"),
        "fingerprints": {
            "canonical": ((report or {}).get("replay") or {}).get("canonical_fingerprint"),
            "calculation": ((report or {}).get("replay") or {}).get("calculation_fingerprint"),
            "rule_map": ((report or {}).get("replay") or {}).get("rule_map_sha256"),
            "report_output": ((report or {}).get("replay") or {}).get("output_snapshot_fingerprint"),
        },
    }

    axes = []
    for axis in AXES:
        axes.append({
            "axis": axis,
            "label": AXIS_LABELS_ZH.get(axis, axis),
            "status": (report or {}).get(axis) or ("empty" if empty_case else "partial"),
        })
    verdicts = [_verdict_view(report, name) for name in VERDICT_NAMES]

    states = {
        "page": state,
        "page_label": PAGE_STATE_LABELS_ZH.get(state, state),
        "finance": (report or {}).get("finance_status"),
        "verification": (report or {}).get("verification_status"),
        "delivery": (report or {}).get("delivery_status"),
        "economics": (report or {}).get("economics_status"),
        "signoff": signoff.get("state"),
        "freshness": freshness["state"],
        "revision": "historical" if resolved["historical"] else "current",
    }
    column_states = {
        "charges": {"state": charges["state"] if report else "empty",
                    "label": COLUMN_STATE_LABELS_ZH.get(
                        charges["state"], charges["state"])},
        "delivery": {"state": delivery["state"] if report else "empty",
                     "label": COLUMN_STATE_LABELS_ZH.get(
                         delivery["state"], delivery["state"])},
        "coverage": {"state": coverage["state"] if report else "empty",
                     "label": COLUMN_STATE_LABELS_ZH.get(
                         coverage["state"], coverage["state"])},
        "economics": {"state": economics["state"] if report else "empty",
                      "label": COLUMN_STATE_LABELS_ZH.get(
                          economics["state"], economics["state"])},
    }

    return {
        "schema": SCHEMA,
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "generated_at": _now_iso(now),
        "read_only": True,
        "state": state,
        "state_label": PAGE_STATE_LABELS_ZH.get(state, state),
        "bundle": bundle_meta,
        "axes": axes,
        "verdicts": verdicts,
        "charges": charges,
        "delivery": delivery,
        "coverage": coverage,
        "economics": economics,
        "disputes": disputes,
        "renewal": renewal,
        "revisions": {
            "selected": revision_id,
            "latest": resolved["latest_id"],
            "historical": resolved["historical"],
            "indexed": resolved["indexed"],
            "items": resolved["items"],
            "current": revision_record,
        },
        "states": states,
        "column_states": column_states,
        "sources": sources,
        "warnings": warnings,
        "faults": faults,
        "missing": missing,
        "commands": _commands(case_dir, revision_id),
        "boundary": BOUNDARY_TEXT,
    }


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

class _Raw(str):
    """Marker for already-escaped HTML fragments."""


def _e(value: Any) -> str:
    return html.escape(_text(value), quote=True)


def _display(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    return text if text.strip() else "—"


def _count_of(value: Any) -> Any:
    if value is None:
        return "—"
    if isinstance(value, (list, tuple, dict)):
        return len(value)
    return value


def _badge(status: Any) -> _Raw:
    text = _text(status) or "—"
    key = text.lower()
    style = {
        "pass": "pass", "complete": "pass", "signed": "pass",
        "signed_with_open_items": "pass", "current": "pass", "ok": "pass",
        "fail": "fail", "blocked": "fail", "stale": "fail",
        "unknown": "warn", "unprovable": "warn", "partial": "warn",
        "historical": "warn", "not_signed": "warn", "unverified": "warn",
        "not_applicable": "neutral", "not_computed": "neutral",
        "empty": "neutral", "clean": "pass", "planned": "warn",
    }.get(key, "neutral")
    return _Raw('<span class="v v-%s">%s</span>' % (style, _e(text)))


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str).replace(
        "</", "<\\/")


def _table(headers: List[str], rows: List[List[Any]],
           css_class: str = "") -> str:
    if not rows:
        return '<p class="empty">无记录</p>'
    out = ['<div class="tw"><table%s>' % (
        ' class="%s"' % _e(css_class) if css_class else "")]
    out.append("<thead><tr>")
    out.extend("<th>%s</th>" % _e(header) for header in headers)
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>")
        for value in row:
            if isinstance(value, _Raw):
                out.append("<td>%s</td>" % value)
            else:
                out.append("<td>%s</td>" % _e(_display(value)))
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def _section(title: str, body: str, *, subtitle: str = "",
             css_class: str = "") -> str:
    parts = ['<section class="card %s">' % css_class,
             '<h2>%s</h2>' % _e(title)]
    if subtitle:
        parts.append('<p class="sub">%s</p>' % _e(subtitle))
    parts.append(body)
    parts.append("</section>")
    return "".join(parts)


def _kv_rows(pairs: List[Tuple[str, Any]]) -> str:
    return _table(["字段", "值"], [[label, value] for label, value in pairs],
                  "kv")


def _page_css() -> str:
    return """
:root{--ink:#1a1f28;--muted:#667085;--line:#e3e7ee;--bg:#f6f7fa;--card:#fff;
--pass:#0f7b4f;--pass-bg:#e7f5ee;--fail:#a0322b;--fail-bg:#fbeceb;
--warn:#8a5a00;--warn-bg:#fdf3da;--neutral:#475467;--neutral-bg:#eef1f6;
--accent:#1f4e79}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
"Hiragino Sans GB","Microsoft YaHei",sans-serif}
.wrap{max-width:1500px;margin:0 auto;padding:28px 24px 48px}
header.top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;
flex-wrap:wrap;margin-bottom:18px}
.brand{font-weight:700;letter-spacing:.02em;color:var(--accent);font-size:13px;
text-transform:uppercase}
h1{font-size:24px;line-height:1.25;margin:4px 0 6px}
.meta{color:var(--muted);font-size:13px}
.pill{display:inline-block;border:1px solid var(--accent);color:var(--accent);
border-radius:999px;padding:3px 10px;font-size:12px;font-weight:600;
background:#fff}
.banner{border:1px solid var(--line);border-left:5px solid var(--accent);
background:var(--card);padding:12px 16px;border-radius:8px;margin-bottom:18px}
.banner .state{font-weight:700}
.banner ul{margin:6px 0 0 18px;padding:0;color:var(--muted)}
.axes{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;
margin-bottom:12px}
.axis{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:10px 12px}
.axis .k{color:var(--muted);font-size:12px}
.axis .v{margin-top:4px;display:block}
.verdicts{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;
margin-bottom:20px}
.verdict{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:10px 12px}
.verdict .k{color:var(--muted);font-size:12px}
.verdict .v{margin-top:4px;display:block}
.cols{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;
align-items:start;margin-bottom:16px}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;
align-items:start;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:16px 16px 18px;margin-bottom:16px;min-width:0}
.cols .card{margin-bottom:0}
h2{font-size:15px;margin:0 0 4px;letter-spacing:.01em}
h3{font-size:13px;margin:16px 0 6px;color:var(--accent)}
.sub{color:var(--muted);font-size:12px;margin:0 0 10px}
.v{display:inline-block;border-radius:6px;padding:1px 8px;font-size:12px;
font-weight:700;background:var(--neutral-bg);color:var(--neutral)}
.v-pass{background:var(--pass-bg);color:var(--pass)}
.v-fail{background:var(--fail-bg);color:var(--fail)}
.v-warn{background:var(--warn-bg);color:var(--warn)}
.v-neutral{background:var(--neutral-bg);color:var(--neutral)}
.tw{overflow-x:auto;max-width:100%}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{text-align:left;vertical-align:top;border-bottom:1px solid var(--line);
padding:6px 8px}
th{color:var(--muted);font-weight:600;background:#fafbfd;white-space:nowrap}
table.kv td:first-child{width:44%;color:var(--muted)}
.empty{color:var(--muted);font-size:12.5px;margin:6px 0}
ul.plain{margin:6px 0 0 18px;padding:0}
ul.plain li{margin:2px 0}
.note{border-left:3px solid var(--line);padding:6px 10px;color:var(--muted);
font-size:12.5px;margin:10px 0 0;background:#fafbfd}
.formula{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
font-size:12.5px;background:#f4f6fa;border:1px solid var(--line);
border-radius:6px;padding:8px 10px;margin:8px 0;white-space:pre-wrap}
details{margin-top:10px}
summary{cursor:pointer;color:var(--accent);font-size:12.5px}
footer{color:var(--muted);font-size:12px;border-top:1px solid var(--line);
margin-top:8px;padding-top:14px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
font-size:12px;background:#f4f6fa;padding:1px 5px;border-radius:4px}
@media(max-width:1100px){.cols,.grid2{grid-template-columns:1fr}
.axes{grid-template-columns:repeat(2,minmax(0,1fr))}
.verdicts{grid-template-columns:repeat(2,minmax(0,1fr))}}
"""


def _html_header(payload: Dict[str, Any]) -> str:
    meta = payload["bundle"]
    scope = meta.get("scope") or {}
    period = scope.get("period") or {}
    period_text = ""
    if period:
        period_text = "%s → %s%s" % (
            period.get("start") or "—", period.get("end") or "—",
            "（不含）" if period.get("to_exclusive") else "")
    lines = [
        '<header class="top"><div>',
        '<div class="brand">Verovo · Evidence Bundle</div>',
        "<h1>%s</h1>" % _e(meta.get("case_id") or "—"),
        '<div class="meta">%s · %s · %s</div>' % (
            _e(scope.get("buyer") or "—"), _e(scope.get("vendor") or "—"),
            _e(scope.get("account") or "—")),
        '<div class="meta">账期 %s · %s · revision %s</div>' % (
            _e(period_text or "—"), _e(meta.get("currency") or "—"),
            _e(meta.get("revision_label") or meta.get("revision_id") or "—")),
        "</div><div><span class=\"pill\">L0 只读</span></div></header>",
    ]
    warnings = payload.get("warnings") or []
    fault_lines = "".join("<li>%s</li>" % _e(item) for item in warnings)
    lines.append('<div class="banner"><div class="state">%s · %s</div>'
                 % (_badge(payload.get("state")), _e(payload.get("state_label"))))
    lines.append('<div class="meta">生成时间 %s · 报告生成时间 %s</div>'
                 % (_e(payload.get("generated_at")),
                    _e(meta.get("report_generated_at") or "—")))
    if fault_lines:
        lines.append("<ul>%s</ul>" % fault_lines)
    lines.append("</div>")
    return "".join(lines)


def _html_axes(payload: Dict[str, Any]) -> str:
    parts = ['<div class="axes">']
    for axis in payload.get("axes") or []:
        parts.append('<div class="axis"><div class="k">%s（%s）</div>'
                     '<span class="v">%s</span></div>'
                     % (_e(axis.get("label")), _e(axis.get("axis")),
                        _badge(axis.get("status"))))
    parts.append("</div>")
    return "".join(parts)


def _html_verdicts(payload: Dict[str, Any]) -> str:
    parts = ['<div class="verdicts">']
    for verdict in payload.get("verdicts") or []:
        parts.append('<div class="verdict"><div class="k">%s<br>%s</div>'
                     '<span class="v">%s</span></div>'
                     % (_e(verdict.get("name_zh")), _e(verdict.get("name")),
                        _badge(verdict.get("status"))))
    parts.append("</div>")
    return "".join(parts)


def _html_charges(charges: Dict[str, Any]) -> str:
    if not charges.get("amounts"):
        return _section("Charges · 费用与对账", '<p class="empty">没有费用工件。</p>')
    amount_rows = [[row.get("label"), row.get("value")]
                   for row in charges.get("amounts") or []]
    body = ['<h3>金额口径（各自独立，不跨口径相加）</h3>',
            _table(["字段", "金额/状态"], amount_rows, "money")]
    identity = charges.get("identity") or {}
    if identity:
        body.append('<h3>恒等式</h3>')
        body.append(_table(
            ["invoice_total", "=", "matched_postings", "+",
             "known_adjustments", "+", "open_difference", "holds"],
            [[identity.get("invoice_total"), "=",
              identity.get("matched_postings"), "+",
              identity.get("known_adjustments"), "+",
              identity.get("open_difference"),
              _badge("PASS" if identity.get("holds") else "FAIL")]]))
    verdict_rows = []
    for verdict in charges.get("verdicts") or []:
        verdict_rows.append([
            verdict.get("name_zh"), verdict.get("name"),
            _badge(verdict.get("status")),
            len(verdict.get("rows") or []),
            "；".join(verdict.get("limitations") or []) or "—",
        ])
    body.append("<h3>Charges 三裁决</h3>")
    body.append(_table(["结论", "英文", "状态", "行数", "限制"], verdict_rows))

    line_rows = []
    for line in charges.get("lines") or []:
        line_rows.append([
            line.get("line_ref"), line.get("charge_id"), line.get("amount"),
            line.get("currency"), line.get("classification"),
            _badge(line.get("recalculation_status")),
            _badge(line.get("invoice_reconciled")),
            _badge(line.get("vendor_reconciled")),
            _badge(line.get("buyer_verified")),
            "|".join(line.get("finding_ids") or []),
        ])
    body.append("<h3>行级对账（互斥分类；一行只计一次）</h3>")
    body.append(_table(
        ["line_ref", "charge", "金额", "币种", "分类", "规则重算",
         "发票对账", "供应商对账", "买方核验", "findings"], line_rows))

    finding_rows = []
    for finding in charges.get("findings") or []:
        finding_rows.append([
            finding.get("id"), finding.get("kind"), finding.get("line_ref"),
            finding.get("impact_amount"), finding.get("impact_currency"),
            finding.get("qualification"), finding.get("owner"),
            finding.get("next_evidence"),
            "|".join(finding.get("candidate_causes") or []),
        ])
    body.append("<h3>Findings（%d 条，金额不累加）</h3>"
                % charges.get("findings_count", 0))
    body.append(_table(
        ["ID", "类型", "行", "影响金额", "币种", "资格", "责任人", "下一步证据",
         "候选原因"], finding_rows))
    notes = charges.get("invoice_total_notes") or []
    missing_rule_lines = charges.get("rule_repricing_missing_lines") or []
    if notes:
        body.append('<div class="note">%s</div>' % "<br>".join(_e(n) for n in notes))
    if missing_rule_lines:
        body.append('<div class="note">规则重算缺少行：%s</div>'
                    % _e("、".join(missing_rule_lines)))
    if charges.get("amount_basis_note"):
        body.append('<div class="note">%s</div>'
                    % _e(charges.get("amount_basis_note")))
    if charges.get("limitations"):
        body.append('<div class="note">%s</div>'
                    % "<br>".join(_e(item) for item in charges["limitations"]))
    return _section("Charges · 费用与对账", "".join(body),
                    subtitle="金额、行级对账与 three verdicts；findings 只列不计。")


def _html_delivery(delivery: Dict[str, Any]) -> str:
    if delivery.get("state") == "empty":
        return _section("Delivery · 交付与验收",
                        '<p class="empty">没有交付工件。</p>')
    body = []
    body.append(_kv_rows([
        ("Delivery Accepted", _badge((delivery.get("verdict") or {}).get("status"))),
        ("Buyer Verified", _badge((delivery.get("buyer_verified") or {}).get("status"))),
        ("verification_status", _badge(delivery.get("verification_status"))),
        ("delivery_status", _badge(delivery.get("delivery_status"))),
    ]))
    rows = []
    for row in delivery.get("rows") or []:
        rows.append([
            row.get("id"), row.get("task_id"),
            _badge(row.get("status")), row.get("reason"),
            row.get("accepted_by"), row.get("accepted_at"),
            row.get("acceptance_basis") or "—",
            row.get("source_ref"),
        ])
    body.append("<h3>逐任务验收（声明级证据）</h3>")
    body.append(_table(
        ["outcome", "task", "状态", "原因", "accepted_by", "accepted_at",
         "证据级别", "source_ref"], rows))
    if delivery.get("gaps"):
        body.append("<h3>验收缺口</h3>")
        body.append('<ul class="plain">%s</ul>'
                    % "".join("<li>%s</li>" % _e(item)
                              for item in delivery["gaps"]))
    body.append('<div class="note">Delivery Accepted 与 Buyer Verified 是两条独立'
                "结论；delivery_status=complete 只表示验收轴完成，不代表 PASS。"
                "</div>")
    return _section("Delivery · 交付与验收", "".join(body),
                    subtitle="UNKNOWN/UNPROVABLE 保留原因，不折叠为 FALSE。")


def _coverage_table(label: str, record: Dict[str, Any]) -> str:
    rows = [[key, record.get(key)]
            for key in ("numerator", "denominator", "ratio", "status",
                        "reason", "basis") if key in record]
    if not rows:
        return '<p class="empty">%s 无记录。</p>' % _e(label)
    return '<h3>%s</h3>%s' % (_e(label),
                              _table(["字段", "值"], rows, "kv"))


def _html_coverage(coverage: Dict[str, Any]) -> str:
    if coverage.get("state") == "empty":
        return _section("Coverage · 覆盖与分母",
                        '<p class="empty">没有覆盖工件。</p>')
    body = [_coverage_table("记录覆盖 record", coverage.get("record") or {}),
            _coverage_table("金额覆盖 amount", coverage.get("amount") or {})]
    gross = coverage.get("gross_net") or {}
    if gross:
        body.append("<h3>Gross / Net</h3>")
        body.append(_table(["字段", "值"], [[k, v] for k, v in gross.items()],
                           "kv"))
    allocation = coverage.get("allocation_coverage") or []
    if allocation:
        body.append("<h3>分摊覆盖（allocatable = allocated + unallocated + non_allocatable）</h3>")
        body.append(_table(
            ["币种", "状态", "可分摊", "已分摊", "未分摊", "不可分摊",
             "未决项", "恒等式"],
            [[item.get("currency"), _badge(item.get("status")),
              item.get("allocatable_amount"), item.get("allocated_amount"),
              item.get("unallocated_amount"), item.get("non_allocatable_amount"),
              _count_of(item.get("open_items")), item.get("identity")]
             for item in allocation]))
    counts = coverage.get("economics_coverage") or {}
    if counts:
        body.append("<h3>任务经济覆盖计数</h3>")
        body.append(_table(["计数", "值"], [[k, v] for k, v in counts.items()],
                           "kv"))
    request = coverage.get("request_coverage") or {}
    body.append("<h3>请求覆盖率</h3>")
    body.append(_kv_rows([
        ("状态", _badge(request.get("status"))),
        ("分子", request.get("numerator")),
        ("分母", request.get("denominator")),
        ("原因", request.get("reason")),
    ]))
    if coverage.get("boundary"):
        body.append('<div class="note">%s</div>' % _e(coverage["boundary"]))
    return _section("Coverage · 覆盖与分母", "".join(body),
                    subtitle="record 与 amount 分母不同；不与请求覆盖率合并。")


def _html_economics(economics: Dict[str, Any]) -> str:
    if economics.get("state") == "empty":
        return _section("Task Economics · 成本与 ROI",
                        '<p class="empty">没有任务经济工件。</p>')
    body = [_kv_rows([
        ("状态", _badge(economics.get("status"))),
        ("成本状态 cost_status", _badge(economics.get("cost_status"))),
        ("完全加载成本", economics.get("fully_loaded_cost")),
        ("已归属成本小计", economics.get("attributed_cost_subtotal")),
        ("每个已验证成功成本", economics.get("cost_per_verified_success")),
        ("该口径原因", economics.get("cost_per_verified_success_reason")),
        ("已验证成功任务数", economics.get("verified_success_tasks")),
        ("成功分母状态", _badge(economics.get("success_denominator_status"))),
        ("已验证业务价值", economics.get("verified_business_value")),
        ("ROI", economics.get("roi")),
        ("ROI 原因", economics.get("roi_reason")),
        ("损失事件", economics.get("loss_events")),
    ])]
    if economics.get("roi_formula"):
        formula = economics["roi_formula"]
        body.append('<div class="formula">%s\n= (%s - %s) / %s</div>'
                    % (_e(formula.get("expression")),
                       _e(formula.get("verified_business_value")),
                       _e(formula.get("fully_loaded_cost")),
                       _e(formula.get("fully_loaded_cost"))))
    if economics.get("unallocated"):
        body.append("<h3>未分摊项</h3>")
        body.append(_table(
            ["币种", "金额", "原因"],
            [[item.get("currency"), item.get("amount"), item.get("reason")]
             for item in economics["unallocated"]]))
    if economics.get("findings"):
        body.append("<h3>经济 findings</h3>")
        body.append(_table(
            ["code", "severity", "task", "message"],
            [[item.get("code"), item.get("severity"), item.get("task_id"),
              item.get("message")] for item in economics["findings"]]))
    if economics.get("boundary"):
        body.append('<div class="note">%s</div>' % _e(economics["boundary"]))
    return _section("Task Economics · 成本与 ROI", "".join(body),
                    subtitle="ROI 不可算时给 roi_reason；未验证价值不填补缺口。")


def _html_disputes(disputes: Dict[str, Any]) -> str:
    body = []
    actions = disputes.get("difference_actions") or {}
    body.append(_kv_rows([
        ("open items", len(disputes.get("open_items") or [])),
        ("external_action_executed", disputes.get("external_action_executed")),
        ("payment_authorization", disputes.get("payment_authorization")),
        ("evidence_status", disputes.get("evidence_status")),
        ("已登记处置总数", actions.get("total")),
        ("未解决项", "、".join(actions.get("unresolved_item_refs") or []) or "—"),
    ]))
    rows = []
    for item in disputes.get("open_items") or []:
        latest = item.get("difference_action") or {}
        rows.append([
            item.get("id"), item.get("kind"), item.get("line_ref"),
            item.get("amount"), item.get("currency"),
            _badge(item.get("state")), item.get("owner"),
            item.get("next_evidence"),
            "|".join(item.get("candidate_causes") or []),
            latest.get("action"), latest.get("source_ref"),
            len(item.get("difference_action_history") or []),
        ])
    body.append("<h3>未决清单</h3>")
    body.append(_table(
        ["ID", "类型", "行", "金额", "币种", "状态", "责任人", "下一步证据",
         "候选原因", "最新处置", "凭据", "历史条数"], rows))
    body.append('<div class="note">%s</div>' % _e(disputes.get("note")))
    if disputes.get("boundary"):
        body.append('<div class="note">%s</div>' % _e(disputes["boundary"]))
    return _section("Open items · 差异与争议", "".join(body),
                    subtitle="处置是本地声明记录，不代表供应商已收到或已解决。")


def _html_renewal(renewal: Dict[str, Any], sources: List[Dict[str, Any]],
                  checks: List[Dict[str, Any]]) -> str:
    body = []
    period = renewal.get("period") or {}
    body.append(_kv_rows([
        ("账期", "%s → %s" % (period.get("start") or "—",
                              period.get("end") or "—")),
        ("币种", renewal.get("currency")),
        ("已抵扣信用", (renewal.get("credits") or {}).get("applied")),
        ("待抵扣信用", (renewal.get("credits") or {}).get("pending")),
        ("已实现节省", renewal.get("realized_savings")),
    ]))
    rules = []
    for rule in renewal.get("rule_versions") or []:
        rules.append([rule.get("rule_id"), rule.get("kind"),
                      rule.get("version"), rule.get("valid_from"),
                      rule.get("valid_to"), rule.get("confirmed_by"),
                      rule.get("source_ref")])
    body.append("<h3>规则版本</h3>")
    body.append(_table(
        ["rule_id", "kind", "version", "valid_from", "valid_to",
         "confirmed_by", "source_ref"], rules))
    fingerprints = renewal.get("fingerprints") or {}
    body.append("<h3>重放指纹</h3>")
    body.append(_kv_rows([
        ("canonical", fingerprints.get("canonical")),
        ("calculation", fingerprints.get("calculation")),
        ("report output", fingerprints.get("report_output")),
        ("rule map", fingerprints.get("rule_map")),
        ("code_version", fingerprints.get("code_version")),
    ]))
    signoff = renewal.get("signoff") or {}
    unresolved_labels = []
    for item in signoff.get("unresolved_items") or []:
        if isinstance(item, dict):
            unresolved_labels.append(_text(item.get("id"))
                                     or _text(item.get("kind")) or "?")
        else:
            unresolved_labels.append(_text(item))
    body.append("<h3>签核 / 复核</h3>")
    body.append(_kv_rows([
        ("签核状态", _badge(signoff.get("state"))),
        ("签核人", signoff.get("reviewer")),
        ("签核时间", signoff.get("decided_at")),
        ("attestation", signoff.get("attestation")),
        ("批准范围", signoff.get("approved_scope")),
        ("保留意见", "；".join(signoff.get("reservations") or []) or "—"),
        ("未决项", "、".join(unresolved_labels) or "—"),
        ("付款授权", signoff.get("payment_authorization", False)),
        ("review.next_review", (renewal.get("review") or {}).get("next_review")),
        ("review.payment_authorization",
         (renewal.get("review") or {}).get("payment_authorization")),
    ]))
    revisions = renewal.get("revisions") or {}
    items = []
    for item in revisions.get("items") or []:
        items.append([item.get("revision_id"), item.get("parent_revision_id"),
                      item.get("status"), item.get("created_at"),
                      item.get("closed_at"), item.get("signoff_state"),
                      item.get("reason")])
    body.append("<h3>Revision 历史（session 选中：%s；最新：%s）</h3>"
                % (_e(revisions.get("selected")), _e(revisions.get("latest"))))
    body.append(_table(
        ["revision", "parent", "status", "created_at", "closed_at",
         "signoff", "reason"], items))
    if checks:
        check_rows = []
        for check in checks:
            check_rows.append([
                check.get("check"), check.get("path"),
                _badge("PASS" if check.get("status") == "ok" else
                       ("FAIL" if check.get("status") == "mismatch"
                        else "UNKNOWN")),
                check.get("detail"),
            ])
        body.append("<details open><summary>新鲜度检查（%d 项）</summary>%s</details>"
                    % (len(check_rows),
                       _table(["check", "path", "状态", "说明"], check_rows)))
    if sources:
        body.append("<details><summary>工件清单（%d 项）</summary>%s</details>"
                    % (len(sources),
                       _table(["工件", "路径", "存在", "sha256", "bytes"],
                              [[item.get("label"), item.get("path"),
                                _badge("PASS" if item.get("present") else "FAIL"),
                                (item.get("sha256") or "")[:16], item.get("bytes")]
                               for item in sources])))
    return _section("Renewal · 续约与复核材料包", "".join(body),
                    subtitle="规则版本、指纹、签核与 revision 历史；不重新计算。")


def _html_commands(commands: List[Dict[str, str]]) -> str:
    if not commands:
        return ""
    items = "".join("<li><strong>%s</strong><br><code>%s</code></li>"
                    % (_e(item.get("label")), _e(item.get("command")))
                    for item in commands)
    return _section("可运行命令", '<ul class="plain">%s</ul>' % items,
                    subtitle="全部为只读或需要显式新路径的命令；页面不会自动执行。")


def render_html(payload: Dict[str, Any]) -> str:
    """Render a self-contained HTML page; no scripts and no external links."""
    meta = payload.get("bundle") or {}
    scope = meta.get("scope") or {}
    title = "Verovo Evidence Bundle · %s · %s" % (
        meta.get("case_id") or "case", meta.get("revision_label") or "—")
    parts = [
        "<!doctype html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>%s</title>" % _e(title),
        "<style>%s</style>" % _page_css(),
        "</head><body><div class=\"wrap\">",
        _html_header(payload),
        _html_axes(payload),
        _html_verdicts(payload),
        '<div class="cols">',
        _html_charges(payload.get("charges") or {}),
        _html_delivery(payload.get("delivery") or {}),
        _html_coverage(payload.get("coverage") or {}),
        "</div>",
        _html_economics(payload.get("economics") or {}),
        '<div class="grid2">',
        _html_disputes(payload.get("disputes") or {}),
        _html_renewal(payload.get("renewal") or {},
                      payload.get("sources") or [],
                      (meta.get("freshness") or {}).get("checks") or []),
        "</div>",
        _html_commands(payload.get("commands") or []),
    ]
    missing = payload.get("missing") or []
    faults = payload.get("faults") or []
    if missing or faults:
        parts.append(_section(
            "缺失与读取故障",
            (_table(["缺失工件"], [[item] for item in missing])
             if missing else "") +
            (_table(["工件", "错误"], [[item.get("artifact"), item.get("error")]
                                      for item in faults]) if faults else "")))
    parts.append("<footer><strong>边界</strong><br>%s<br>"
                 "scope: buyer=%s vendor=%s account=%s · schema %s · generator %s/%s"
                 "</footer>" % (
                     _e(payload.get("boundary")),
                     _e(scope.get("buyer")), _e(scope.get("vendor")),
                     _e(scope.get("account")), _e(payload.get("schema")),
                     _e(payload.get("generator")),
                     _e(payload.get("generator_version"))))
    parts.append("</div></body></html>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# terminal summary
# ---------------------------------------------------------------------------

def render_summary(payload: Dict[str, Any]) -> str:
    meta = payload.get("bundle") or {}
    scope = meta.get("scope") or {}
    charges = payload.get("charges") or {}
    delivery = payload.get("delivery") or {}
    coverage = payload.get("coverage") or {}
    economics = payload.get("economics") or {}
    disputes = payload.get("disputes") or {}
    signoff = (payload.get("renewal") or {}).get("signoff") or {}
    identity = charges.get("identity") or {}
    open_count = len(disputes.get("open_items") or [])
    decision = ("当前不建议直接付款或续约：还有 %d 项未决事项" % open_count
                if open_count else "当前没有未决事项；是否付款或续约仍由买方负责人决定")
    accepted = len(delivery.get("rows") or []) if (delivery.get("verdict") or {}).get("status") == "PASS" else 0
    invoice = identity.get("invoice_total") or "未登记"
    difference = identity.get("open_difference") or "未登记"
    cost = economics.get("cost_per_verified_success") or "暂时无法计算"
    roi = economics.get("roi") if economics.get("roi") is not None else "暂时无法计算"
    business_lines = [
        "Verovo 业务结论 · %s · 版本 %s" % (
            meta.get("case_id") or "—",
            meta.get("revision_label") or meta.get("revision_id") or "—"),
        "付款与续约：%s" % decision,
        "费用：供应商账单 %s %s；未解释差额 %s %s" % (
            invoice, scope.get("currency") or "币种未明",
            difference, scope.get("currency") or "币种未明"),
        "任务结果：%s" % ("已有 %d 条任务验收记录通过" % accepted
                         if accepted else "任务交付尚不能确认"),
        "投入产出：每个已验证成功任务成本 %s %s；投资回报 %s" % (
            cost, scope.get("currency") or "", roi),
        "--- 核验明细 ---",
        "",
    ]
    lines = business_lines + [
        "Verovo Evidence Bundle · %s · revision %s" % (
            meta.get("case_id") or "—",
            meta.get("revision_label") or meta.get("revision_id") or "—"),
        "state: %s（%s）· L0 只读" % (payload.get("state"),
                                    payload.get("state_label")),
        "scope: buyer=%s vendor=%s account=%s currency=%s" % (
            scope.get("buyer") or "—", scope.get("vendor") or "—",
            scope.get("account") or "—", scope.get("currency") or "—"),
        "axes: " + " ".join(
            "%s=%s" % (axis.get("axis"), axis.get("status"))
            for axis in payload.get("axes") or []),
        "verdicts: " + " ".join(
            "%s=%s" % (verdict.get("name"), verdict.get("status"))
            for verdict in payload.get("verdicts") or []),
    ]
    identity = charges.get("identity") or {}
    lines.append(
        "charges: invoice=%s open_difference=%s identity_holds=%s findings=%s"
        % (identity.get("invoice_total") or "—",
           identity.get("open_difference") or "—",
           identity.get("holds"), charges.get("findings_count", 0)))
    lines.append(
        "delivery: Delivery Accepted=%s delivery_status=%s verification_status=%s rows=%s"
        % ((delivery.get("verdict") or {}).get("status") or "—",
           delivery.get("delivery_status") or "—",
           delivery.get("verification_status") or "—",
           len(delivery.get("rows") or [])))
    record = coverage.get("record") or {}
    amount = coverage.get("amount") or {}
    lines.append(
        "coverage: record=%s/%s amount=%s request=%s"
        % (record.get("numerator"), record.get("denominator"),
           amount.get("status") or "—",
           (coverage.get("request_coverage") or {}).get("status")))
    lines.append(
        "economics: status=%s cost_per_verified_success=%s roi=%s"
        % (economics.get("status") or "—",
           economics.get("cost_per_verified_success") or "—",
           economics.get("roi") if economics.get("roi") is not None
           else "—（%s）" % (economics.get("roi_reason") or "not computed")))
    lines.append(
        "disputes: open_items=%s external_action_executed=%s payment_authorization=%s"
        % (len(disputes.get("open_items") or []),
           disputes.get("external_action_executed"),
           disputes.get("payment_authorization")))
    lines.append("signoff: %s · by %s · at %s"
                 % (signoff.get("state") or "—",
                    signoff.get("reviewer") or "—",
                    signoff.get("decided_at") or "—"))
    if payload.get("warnings"):
        lines.append("warnings:")
        lines.extend("  - %s" % item for item in payload["warnings"])
    if payload.get("missing"):
        lines.append("missing: %s" % "、".join(payload["missing"]))
    if payload.get("faults"):
        lines.append("faults:")
        lines.extend("  - %s: %s" % (item.get("artifact"), item.get("error"))
                     for item in payload["faults"])
    lines.append("boundary: %s" % payload.get("boundary"))
    return "\n".join(lines)


__all__ = ["SCHEMA", "GENERATOR", "GENERATOR_VERSION", "build_bundle",
           "render_html", "render_summary"]
