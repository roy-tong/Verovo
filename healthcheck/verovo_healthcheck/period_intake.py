"""Period intake: turn a billing-period bundle into a task-economics case.

One manifest names the local files — agent traces, usage rows, supplier bills,
business outcomes and (optionally) invoices — and this module assembles the case
that ``task_economics.evaluate`` already understands.

Discipline is unchanged from the economics engine:

- nothing is invented: a row without a task link stays unallocated, a currency
  that differs from the case is excluded and named, an unverified outcome does
  not count as success or value;
- invoices stay unreviewed drafts; no tax, credit, refund or FX is guessed;
- the intake report lists every excluded row and every missing link, so a
  finance reviewer can complete the period instead of trusting a precise-looking
  ROI built on absent data.

It performs no network request and calls no provider.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import invoice_intake

SCHEMA_VERSION = "1.0"
FORMATS = ("tasks-csv", "runs-jsonl", "usage-jsonl", "bills-csv", "outcomes-csv")
TRUE_VALUES = {"1", "true", "yes", "y", "t"}


class IntakeError(ValueError):
    pass


def _amount(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise IntakeError("%s is not a monetary value" % field)
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise IntakeError("%s is not numeric: %r" % (field, value))
    if not number.is_finite():
        raise IntakeError("%s is not finite" % field)
    return number


def _text(value: Any) -> str:
    return str(value or "").strip()


def _truth(value: Any) -> Optional[bool]:
    text = _text(value).lower()
    if text in TRUE_VALUES:
        return True
    if text in {"0", "false", "no", "n", "f"}:
        return False
    return None


def _rows(path: Path) -> List[Dict[str, str]]:
    if not path.is_file():
        raise IntakeError("file not found: %s" % path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = [str(name or "").strip().lower() for name in (reader.fieldnames or [])]
        if not fields:
            raise IntakeError("CSV has no header: %s" % path)
        out = []
        for index, row in enumerate(reader, start=2):
            cleaned = {str(key or "").strip().lower(): _text(value) for key, value in row.items()}
            cleaned["__line__"] = str(index)
            out.append(cleaned)
    return out


def _jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        raise IntakeError("file not found: %s" % path)
    rows: List[Dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IntakeError("%s line %d is not JSON: %s" % (path, index, exc))
        if not isinstance(item, dict):
            raise IntakeError("%s line %d must be an object" % (path, index))
        item["__line__"] = str(index)
        rows.append(item)
    return rows


def load_manifest(bundle: Path) -> Dict[str, Any]:
    path = Path(bundle) / "manifest.json"
    if not path.is_file():
        raise IntakeError("missing manifest.json under %s" % bundle)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IntakeError("manifest.json is not valid JSON: %s" % exc)
    if not isinstance(manifest, dict):
        raise IntakeError("manifest.json must be an object")
    for field in ("case_id", "currency", "period", "sources"):
        if not manifest.get(field):
            raise IntakeError("manifest needs %s" % field)
    period = manifest.get("period")
    if not isinstance(period, dict) or not period.get("start") or not period.get("end"):
        raise IntakeError("manifest.period needs start and end")
    sources = manifest.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise IntakeError("manifest.sources must name at least one source")
    for name, entries in sources.items():
        if not isinstance(entries, list) or not entries:
            raise IntakeError("source %r must be a non-empty array" % name)
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("path"):
                raise IntakeError("each %r source needs a path" % name)
            if str(entry.get("format") or "") not in FORMATS and name != "invoices":
                raise IntakeError("source %r has unknown format %r; use one of %s"
                                  % (name, entry.get("format"), ", ".join(FORMATS)))
    return manifest


def _source_path(bundle: Path, entry: Dict[str, Any]) -> Path:
    path = (bundle / str(entry["path"])).resolve()
    try:
        path.relative_to(bundle.resolve())
    except ValueError:
        raise IntakeError("source path escapes the bundle: %s" % entry["path"])
    return path


def _open_item(items: List[Dict[str, Any]], kind: str, detail: str, source: str = "") -> None:
    items.append({"kind": kind, "detail": detail, "source": source})


def intake(bundle: Path) -> Dict[str, Any]:
    """Read a bundle and return {case, report}; never writes files itself."""
    bundle = Path(bundle).resolve()
    manifest = load_manifest(bundle)
    currency = _text(manifest.get("currency")).upper()
    period = {"start": _text(manifest["period"]["start"]), "end": _text(manifest["period"]["end"])}
    sources = manifest["sources"]
    open_items: List[Dict[str, Any]] = []
    report: Dict[str, Any] = {"status": "complete", "counts": {}, "currency_mismatches": [],
                              "duplicates": [], "unmatched_charges": [],
                              "reconciled_charges": [], "charge_amount_mismatches": [],
                              "open_items": open_items}

    tasks: Dict[str, Dict[str, Any]] = {}
    runs: Dict[str, Dict[str, Any]] = {}
    usage: Dict[str, Dict[str, Any]] = {}
    cost_items: List[Dict[str, Any]] = []
    documents: List[Dict[str, Any]] = []
    outcomes: List[Dict[str, Any]] = []
    rules = manifest.get("allocation_rules") or []

    def add_task(task_id: str, task_type: str = "") -> None:
        task_id = _text(task_id)
        if not task_id:
            return
        tasks.setdefault(task_id, {"task_id": task_id, "type": _text(task_type) or "unknown"})

    for entry in sources.get("tasks") or []:
        path = _source_path(bundle, entry)
        for row in _rows(path):
            add_task(row.get("task_id"), row.get("type"))

    for entry in sources.get("runs") or []:
        path = _source_path(bundle, entry)
        for row in _jsonl(path):
            run_id = _text(row.get("run_id"))
            task_id = _text(row.get("task_id"))
            if not run_id:
                _open_item(open_items, "missing_run_id", "run row without run_id", path.name)
                continue
            if run_id in runs:
                _open_item(open_items, "duplicate_run", "run %s repeated; later row ignored" % run_id,
                           path.name)
                continue
            runs[run_id] = {"run_id": run_id, "task_id": task_id}
            add_task(task_id)

    for entry in sources.get("usage") or []:
        path = _source_path(bundle, entry)
        for row in _jsonl(path):
            usage_id = _text(row.get("usage_id"))
            if not usage_id:
                _open_item(open_items, "missing_usage_id", "usage row without usage_id", path.name)
                continue
            if usage_id in usage:
                _open_item(open_items, "duplicate_usage", "usage %s repeated; later row ignored" % usage_id,
                           path.name)
                continue
            run_id = _text(row.get("run_id"))
            if run_id and run_id not in runs:
                runs[run_id] = {"run_id": run_id, "task_id": _text(row.get("task_id"))}
                add_task(_text(row.get("task_id")))
            item_currency = _text(row.get("currency")).upper() or currency
            if item_currency != currency:
                report["currency_mismatches"].append(
                    {"row": usage_id, "source": path.name, "currency": item_currency})
                _open_item(open_items, "currency_mismatch",
                           "usage %s is %s, case is %s; excluded" % (usage_id, item_currency, currency),
                           path.name)
                continue
            financialized = row.get("financialized")
            is_financial = financialized is True or _truth(financialized) is True
            record: Dict[str, Any] = {
                "usage_id": usage_id,
                "run_id": run_id,
                "financialized": bool(is_financial),
                "source_ref": _text(row.get("source_ref")) or "usage:%s" % usage_id,
            }
            if row.get("cost") not in (None, ""):
                record["cost"] = str(_amount(row.get("cost"), "usage %s cost" % usage_id))
                record["currency"] = currency
                if not is_financial:
                    _open_item(open_items, "unfinancialized_cost",
                               "usage %s carries a cost but is not financialized; kept as technical usage"
                               % usage_id, path.name)
            if row.get("charge_id"):
                record["charge_id"] = _text(row.get("charge_id"))
            usage[usage_id] = record

    bill_total = Decimal("0")
    usage_by_charge = {str(item.get("charge_id")): item for item in usage.values() if item.get("charge_id")}
    seen_bill_charges: set = set()
    for entry in sources.get("bills") or []:
        path = _source_path(bundle, entry)
        vendor = _text(entry.get("vendor"))
        file_total = Decimal("0")
        for row in _rows(path):
            charge_id = _text(row.get("charge_id")) or _text(row.get("invoice_id")) or _text(row.get("id"))
            if not charge_id:
                _open_item(open_items, "missing_charge_id", "bill row without charge_id", path.name)
                continue
            if charge_id in seen_bill_charges:
                report["duplicates"].append({"charge_id": charge_id, "source": path.name})
                _open_item(open_items, "duplicate_charge",
                           "charge %s appears twice; only the first row is kept" % charge_id, path.name)
                continue
            row_currency = _text(row.get("currency")).upper() or currency
            if row_currency != currency:
                report["currency_mismatches"].append(
                    {"row": charge_id, "source": path.name, "currency": row_currency})
                _open_item(open_items, "currency_mismatch",
                           "bill %s is %s, case is %s; excluded" % (charge_id, row_currency, currency),
                           path.name)
                continue
            amount = _amount(row.get("amount"), "bill %s amount" % charge_id)
            if amount < 0:
                _open_item(open_items, "negative_amount",
                           "bill %s has a negative amount; credits need explicit handling" % charge_id,
                           path.name)
                continue
            seen_bill_charges.add(charge_id)
            matched_usage = usage_by_charge.get(charge_id)
            if matched_usage is not None and matched_usage.get("financialized"):
                usage_cost = matched_usage.get("cost")
                if usage_cost is not None and _amount(usage_cost, "usage cost") != amount:
                    report["charge_amount_mismatches"].append({
                        "charge_id": charge_id, "bill": str(amount), "usage": str(usage_cost),
                        "source": path.name,
                    })
                    _open_item(open_items, "charge_amount_mismatch",
                               "charge %s: bill %s vs usage %s; review before trusting either"
                               % (charge_id, amount, usage_cost), path.name)
                report["reconciled_charges"].append(charge_id)
                file_total += amount
                continue
            item: Dict[str, Any] = {
                "cost_id": charge_id,
                "amount": str(amount),
                "currency": currency,
                "source_ref": "bill:%s:%s" % (path.name, row["__line__"]),
                "vendor": _text(row.get("vendor")) or vendor,
            }
            task_id = _text(row.get("task_id"))
            rule_id = _text(row.get("allocation_rule")) or _text(entry.get("allocation_rule"))
            if not task_id and matched_usage is not None:
                matched_run = runs.get(_text(matched_usage.get("run_id")))
                if matched_run and matched_run.get("task_id") in tasks:
                    task_id = str(matched_run["task_id"])
            if task_id:
                item["task_id"] = task_id
                add_task(task_id)
            elif rule_id:
                item["allocation_rule"] = rule_id
            else:
                _open_item(open_items, "unallocated_cost",
                           "bill %s has no task_id or allocation_rule; it will stay unallocated"
                           % charge_id, path.name)
            cost_items.append(item)
            file_total += amount
        bill_total += file_total
        documents.append({"document_id": "bill:%s" % path.stem, "type": "bill",
                          "total": str(file_total), "currency": currency,
                          "source_ref": path.name})

    for entry in sources.get("outcomes") or []:
        path = _source_path(bundle, entry)
        for row in _rows(path):
            task_id = _text(row.get("task_id")) or _text(row.get("conversation_id"))
            if not task_id:
                _open_item(open_items, "missing_task_id", "outcome row without task_id", path.name)
                continue
            add_task(task_id)
            verified = _truth(row.get("verified")) is True
            status = _text(row.get("status")).lower() or ("success" if verified else "unknown")
            item: Dict[str, Any] = {
                "task_id": task_id,
                "status": "success" if status in ("success", "succeeded", "resolved", "done") else status,
                "verified": verified,
                "source_ref": _text(row.get("source_ref")) or "outcome:%s:%s" % (path.name, row["__line__"]),
            }
            if row.get("value") not in (None, ""):
                row_currency = _text(row.get("currency")).upper() or currency
                if row_currency != currency:
                    report["currency_mismatches"].append(
                        {"row": "outcome:%s" % task_id, "source": path.name, "currency": row_currency})
                    _open_item(open_items, "currency_mismatch",
                               "outcome value for %s is %s, case is %s; value dropped"
                               % (task_id, row_currency, currency), path.name)
                else:
                    item["value"] = str(_amount(row.get("value"), "outcome value"))
                    item["value_verified"] = _truth(row.get("value_verified")) is True
                    item["currency"] = currency
            if not verified:
                _open_item(open_items, "unverified_outcome",
                           "outcome for %s is not verified; it cannot count as success" % task_id,
                           path.name)
            outcomes.append(item)

    for entry in sources.get("invoices") or []:
        target = _source_path(bundle, entry)
        files = sorted(target.glob("*")) if target.is_dir() else [target]
        for path in files:
            if not path.is_file():
                continue
            try:
                draft = invoice_intake.extract_invoice(str(path))
            except (invoice_intake.InvoiceIntakeError, OSError) as exc:
                _open_item(open_items, "invoice_pending",
                           "invoice %s is not extracted: %s" % (path.name, exc), path.name)
                continue
            documents.append({
                "document_id": "invoice:%s" % draft.get("sha256", path.stem)[:12],
                "type": "invoice",
                "total": draft.get("amount"),
                "currency": draft.get("currency"),
                "source_ref": path.name,
                "reviewed": False,
            })
            _open_item(open_items, "invoice_draft",
                       "invoice %s is an unreviewed draft; not booked as cost" % path.name, path.name)

    bill_charges = set(seen_bill_charges)
    usage_charges = {item.get("charge_id") for item in usage.values() if item.get("charge_id")}
    report["unmatched_charges"] = sorted(bill_charges - usage_charges)
    report["counts"] = {
        "tasks": len(tasks),
        "runs": len(runs),
        "usage": len(usage),
        "financialized_usage": sum(1 for item in usage.values() if item.get("financialized")),
        "cost_items": len(cost_items),
        "documents": len(documents),
        "outcomes": len(outcomes),
        "verified_outcomes": sum(1 for item in outcomes if item["verified"]),
    }
    report["reconciliation"] = {
        "billed_total": str(bill_total),
        "usage_source_total": str(sum(
            (_amount(item.get("cost"), "usage cost") for item in usage.values()
             if item.get("cost") is not None and item.get("financialized")), Decimal("0"))),
        "bill_charges": sorted(bill_charges),
        "usage_charges": sorted(value for value in usage_charges if value),
        "reconciled_charges": sorted(set(report["reconciled_charges"])),
        "boundary": ("账单合计与用量合计是两种口径：不要求相等，也不把差额当结论；"
                     "未匹配的 charge 只是待核对项。"),
    }
    report["coverage"] = {
        "tasks_without_run": sum(1 for task_id in tasks if not any(
            run.get("task_id") == task_id for run in runs.values())),
        "tasks_without_outcome": sum(1 for task_id in tasks if not any(
            item["task_id"] == task_id for item in outcomes)),
    }
    report["status"] = "blocked" if not tasks else ("partial" if open_items else "complete")
    report["case_id"] = _text(manifest.get("case_id"))
    report["currency"] = currency
    report["period"] = period
    report["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["boundary"] = ("自动归集只读本地文件；缺任务关联、币种不一致、未核验结果与未复核发票"
                          "都会保留为待补事项，不会被补成精确 ROI。")

    case = {
        "schema_version": "1.0",
        "case_id": report["case_id"],
        "currency": currency,
        "period": period,
        "allocation_rules": rules,
        "tasks": sorted(tasks.values(), key=lambda item: item["task_id"]),
        "runs": sorted(runs.values(), key=lambda item: item["run_id"]),
        "usage": sorted(usage.values(), key=lambda item: item["usage_id"]),
        "documents": documents,
        "cost_items": cost_items,
        "outcomes": outcomes,
    }
    return {"case": case, "report": report, "case_written": bool(tasks)}


def markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# 账期归集报告",
        "",
        f"- case：{report.get('case_id')}；币种：{report.get('currency')}；"
        f"期间：{report['period']['start']} → {report['period']['end']}",
        f"- 状态：{report['status']}",
        "",
        "## 计数",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in report["counts"].items())
    lines.extend(["", "## 对账", ""])
    reconciliation = report.get("reconciliation") or {}
    lines.append(f"- 账单合计：{reconciliation.get('billed_total')}")
    lines.append(f"- 用量合计（已财务化）：{reconciliation.get('usage_source_total')}")
    lines.append(f"- 未匹配 charge：{', '.join(report.get('unmatched_charges') or []) or '无'}")
    lines.extend(["", "## 待补事项", ""])
    if report["open_items"]:
        for item in report["open_items"]:
            lines.append(f"- [{item['kind']}] {item['detail']}")
    else:
        lines.append("- 无")
    lines.extend(["", "## 覆盖", ""])
    lines.extend(f"- {key}: {value}" for key, value in report["coverage"].items())
    lines.extend(["", "## 边界", "", report["boundary"], ""])
    return "\n".join(lines)
