"""Tool value from controlled task groups, not from call counts or cost share.

Input is a local CSV/JSONL of comparable tasks.  With a control group the module
reports success-rate and cost-per-success differences.  It never claims a causal
effect from a single arm, a last call, or a cost share.
"""
from __future__ import annotations

import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional


SCHEMA = "verovo.tool-value/1"
GROUPS = ("treatment", "control")


def _dec(value) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _money(value) -> Optional[str]:
    if value is None:
        return None
    return format(value.quantize(Decimal("0.01")), "f")


def _bool(value) -> bool:
    text = str(value or "").strip().lower()
    if text in ("1", "true", "yes", "success", "succeeded"):
        return True
    if text in ("0", "false", "no", "failure", "failed"):
        return False
    raise ValueError("success must be 0/1, true/false, yes/no or success/failed")


def _rows(path) -> List[Dict[str, Any]]:
    file_path = Path(path)
    if not file_path.is_file():
        raise ValueError("task file not found: %s" % file_path)
    if file_path.suffix.lower() == ".csv":
        with file_path.open(encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    rows = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def load_tasks(path, *, group_col="group", success_col="success",
               cost_col="cost", retry_col="retry_cost") -> List[Dict[str, Any]]:
    tasks = []
    for index, row in enumerate(_rows(path), start=1):
        group = str(row.get(group_col) or "").strip().lower()
        if group not in GROUPS:
            raise ValueError("row %d group must be treatment or control" % index)
        cost = _dec(row.get(cost_col))
        if cost is None:
            raise ValueError("row %d needs a finite cost" % index)
        tasks.append({
            "task_id": str(row.get("task_id") or "").strip() or "row-%d" % index,
            "group": group,
            "success": _bool(row.get(success_col)),
            "cost": cost,
            "retry_cost": _dec(row.get(retry_col)) or Decimal("0"),
            "comparable_group": str(row.get("comparable_group") or "").strip(),
        })
    return tasks


def _group(tasks, name):
    rows = [task for task in tasks if task["group"] == name]
    n = len(rows)
    successes = sum(1 for task in rows if task["success"])
    total = sum((task["cost"] for task in rows), Decimal("0"))
    retry = sum((task["retry_cost"] for task in rows), Decimal("0"))
    return {
        "n": n,
        "successes": successes,
        "success_rate": (successes / float(n)) if n else None,
        "total_cost": _money(total),
        "cost_per_task": (_money(total / n) if n else None),
        "cost_per_success": (_money(total / successes) if successes else None),
        "retry_cost": _money(retry),
    }


def analyze(tasks: List[Dict[str, Any]], *, value_per_success=None) -> Dict[str, Any]:
    if not tasks:
        return {"schema": SCHEMA, "status": "no_data", "causality": "not_claimed",
                "note": "no comparable tasks"}
    treatment = _group(tasks, "treatment")
    control = _group(tasks, "control")
    result = {
        "schema": SCHEMA,
        "status": "observed",
        "treatment": treatment,
        "control": control,
        "causality": "assisted" if control["n"] else "unknown",
        "boundary": ("Comparable local task groups only. No last-call, call-count or cost-share "
                     "attribution. Without a control group no incremental value is claimed."),
    }
    if not control["n"] or not treatment["n"]:
        result.update({"status": "no_control", "causality": "unknown",
                       "note": "one group is empty; no difference is estimated"})
        return result
    if treatment["n"] < 30 or control["n"] < 30:
        result.update({"status": "insufficient_evidence",
                       "note": "n < 30 per group; report the difference but do not quote confidence"})
    success_diff = treatment["success_rate"] - control["success_rate"]
    result["difference"] = {
        "success_rate": round(success_diff, 4),
        "cost_per_task": (_money(Decimal(treatment["cost_per_task"]) - Decimal(control["cost_per_task"]))
                          if treatment["cost_per_task"] and control["cost_per_task"] else None),
        "cost_per_success": (_money(Decimal(treatment["cost_per_success"]) - Decimal(control["cost_per_success"]))
                             if treatment["cost_per_success"] and control["cost_per_success"] else None),
    }
    if value_per_success is not None:
        value = _dec(value_per_success)
        if value is None:
            raise ValueError("value_per_success must be a finite amount")
        cost_diff = (Decimal(treatment["cost_per_task"]) - Decimal(control["cost_per_task"])
                     if treatment["cost_per_task"] and control["cost_per_task"] else None)
        result["incremental_value"] = {
            "value_per_success": _money(value),
            "success_value": _money(Decimal(str(success_diff)) * value),
            "cost_difference_per_task": _money(cost_diff) if cost_diff is not None else None,
            "net_per_task": (_money(Decimal(str(success_diff)) * value - cost_diff)
                             if cost_diff is not None else None),
            "claim": "assisted" if result["status"] == "observed" else "insufficient_evidence",
        }
    return result


def render(payload: Dict[str, Any]) -> str:
    lines = ["# 工具价值实验 · %s" % payload.get("status"), "",
             "> 因果口径：%s" % payload.get("causality"), ""]
    for name in GROUPS:
        row = payload.get(name) or {}
        lines.append("- %s：n=%s，成功率 %s，每次成功成本 %s" % (
            name, row.get("n"), row.get("success_rate"), row.get("cost_per_success")))
    difference = payload.get("difference") or {}
    if difference:
        lines.extend(["", "## 差异", "",
                      "- 成功率差：%s" % difference.get("success_rate"),
                      "- 每任务成本差：%s" % difference.get("cost_per_task"),
                      "- 每次成功成本差：%s" % difference.get("cost_per_success")])
    if payload.get("incremental_value"):
        lines.extend(["", "## 增量价值", ""])
        for key, value in payload["incremental_value"].items():
            lines.append("- %s：%s" % (key, value))
    lines.extend(["", "## 边界", "", "- 没有对照就没有增量结论；相关不等于因果。", ""])
    return "\n".join(lines)
