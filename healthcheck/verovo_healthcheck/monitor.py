"""Continuous spend observation: ingest usage, build a baseline, surface anomalies.

This module is advisory.  It does not block a gateway, rewrite a bill or claim a
business result.  Missing task/currency/price fields stay visible as gaps.
"""
from __future__ import annotations

import csv
import json
import math
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional


SCHEMA = "verovo.spend-monitor/1"
ANOMALY_SCHEMA = "verovo.spend-anomaly/1"


def _dec(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _rows(path) -> List[Dict[str, Any]]:
    file_path = Path(path)
    if not file_path.is_file():
        raise ValueError("usage file not found: %s" % file_path)
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


def load_records(path) -> List[Dict[str, Any]]:
    records = []
    for index, row in enumerate(_rows(path), start=1):
        observed_at = str(row.get("observed_at") or row.get("timestamp") or "").strip()
        tool = str(row.get("tool") or row.get("vendor") or "").strip()
        if not observed_at or not tool:
            raise ValueError("row %d needs observed_at and tool" % index)
        quantity = _dec(row.get("quantity"))
        unit_price = _dec(row.get("unit_price"))
        amount = _dec(row.get("amount") or row.get("cost"))
        if amount is None and quantity is not None and unit_price is not None:
            amount = quantity * unit_price
        if amount is None:
            raise ValueError("row %d needs amount or quantity + unit_price" % index)
        currency = str(row.get("currency") or "").strip().upper() or None
        records.append({
            "observed_at": observed_at,
            "tool": tool,
            "model": str(row.get("model") or "").strip() or "not-recorded",
            "task_id": str(row.get("task_id") or "").strip(),
            "quantity": str(quantity) if quantity is not None else None,
            "unit_price": str(unit_price) if unit_price is not None else None,
            "amount": str(amount),
            "currency": currency,
            "retry": bool(row.get("retry")) if "retry" in row else False,
            "attempt": str(row.get("attempt") or "").strip(),
            "source": str(row.get("source") or "").strip(),
        })
    return records


def _day(record) -> str:
    return str(record["observed_at"])[:10]


def _daily(records, tool, model):
    buckets: Dict[str, Decimal] = {}
    for record in records:
        if record["tool"] != tool or record["model"] != model:
            continue
        buckets[_day(record)] = buckets.get(_day(record), Decimal("0")) + Decimal(record["amount"])
    return buckets


def baseline(records: List[Dict[str, Any]], *, min_days: int = 7) -> Dict[str, Any]:
    groups: Dict[tuple, List[Decimal]] = {}
    currencies = set()
    for record in records:
        if record["currency"]:
            currencies.add(record["currency"])
        key = (record["tool"], record["model"])
        groups.setdefault(key, []).append(Decimal(record["amount"]))
    result = []
    for (tool, model), amounts in sorted(groups.items()):
        daily = _daily(records, tool, model)
        values = sorted(daily.values())
        if len(values) < min_days:
            result.append({"tool": tool, "model": model, "status": "insufficient_baseline",
                           "days": len(values), "required_days": min_days})
            continue
        center = median([float(value) for value in values])
        mad = median([abs(float(value) - center) for value in values])
        result.append({
            "tool": tool, "model": model,
            "status": "ok" if mad > 0 else "zero_mad",
            "days": len(values),
            "daily_median": round(center, 6),
            "daily_mad": round(mad, 6),
            "records": len(amounts),
        })
    return {"schema": SCHEMA, "groups": result,
            "currencies": sorted(currencies),
            "single_currency": len(currencies) <= 1,
            "boundary": "baseline is a local median/MAD over observed days; no seasonal or vendor-mix adjustment."}


def anomalies(records: List[Dict[str, Any]], baseline_payload: Dict[str, Any], *,
              z_threshold: float = 3.5, price_drift: float = 0.2,
              retry_threshold: int = 5) -> List[Dict[str, Any]]:
    groups = {(row["tool"], row["model"]): row for row in baseline_payload.get("groups") or []
              if row.get("status") in ("ok", "zero_mad")}
    found = []
    for (tool, model), base in groups.items():
        daily = _daily(records, tool, model)
        center = float(base.get("daily_median") or 0)
        mad = float(base.get("daily_mad") or 0)
        for day, value in sorted(daily.items()):
            amount = float(value)
            robust_z = (0.6745 * (amount - center) / mad) if mad > 0 else None
            spike = (robust_z is not None and robust_z >= z_threshold) or (
                robust_z is None and center > 0 and amount > center * 1.5)
            if spike:
                found.append({
                    "schema": ANOMALY_SCHEMA, "kind": "spend_spike",
                    "tool": tool, "model": model, "day": day,
                    "amount": str(value), "baseline_median": center,
                    "robust_z": (round(robust_z, 3) if robust_z is not None else None),
                    "severity": ("high" if (robust_z or 0) >= 6 or (robust_z is None and amount > center * 2)
                                 else "medium"),
                    "suggestion": "inspect task volume, retry rate and unit price before paying or blaming the vendor",
                })
        prices = [Decimal(record["unit_price"]) for record in records
                  if record["tool"] == tool and record["model"] == model and record["unit_price"]]
        if prices:
            center_price = median([float(value) for value in prices])
            for price in sorted(set(prices)):
                if center_price > 0 and abs(float(price) - center_price) / center_price >= price_drift:
                    found.append({
                        "schema": ANOMALY_SCHEMA, "kind": "unit_price_drift",
                        "tool": tool, "model": model, "unit_price": str(price),
                        "baseline_median": round(center_price, 6),
                        "severity": "medium",
                        "suggestion": "compare the approved rule/version and the vendor price sheet",
                    })
    retry_counts: Dict[tuple, int] = {}
    for record in records:
        if record["retry"] or record["attempt"] not in ("", "1"):
            key = (record["task_id"] or "unlinked", _day(record))
            retry_counts[key] = retry_counts.get(key, 0) + 1
    for (task_id, day), count in sorted(retry_counts.items()):
        if count > retry_threshold:
            found.append({
                "schema": ANOMALY_SCHEMA, "kind": "retry_storm",
                "task_id": task_id, "day": day, "retries": count,
                "severity": "high", "suggestion": "inspect the failing step; retries may be billed more than once",
            })
    orphan_days: Dict[str, int] = {}
    for record in records:
        if not record["task_id"]:
            orphan_days[_day(record)] = orphan_days.get(_day(record), 0) + 1
    for day, count in sorted(orphan_days.items()):
        found.append({
            "schema": ANOMALY_SCHEMA, "kind": "orphan_call",
            "day": day, "records": count, "severity": "low",
            "suggestion": "map these calls to a task or keep them as unallocated cost",
        })
    return found


def report(records: List[Dict[str, Any]], baseline_payload: Dict[str, Any],
           found: List[Dict[str, Any]]) -> Dict[str, Any]:
    totals: Dict[str, Decimal] = {}
    for record in records:
        currency = record["currency"] or "unknown"
        totals[currency] = totals.get(currency, Decimal("0")) + Decimal(record["amount"])
    by_kind: Dict[str, int] = {}
    for row in found:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
    return {
        "schema": SCHEMA,
        "status": "observed" if records else "no_data",
        "records": len(records),
        "totals": {currency: str(amount) for currency, amount in sorted(totals.items())},
        "baseline": baseline_payload,
        "anomalies": found,
        "anomaly_counts": by_kind,
        "control": "advisory_only",
        "boundary": ("Local observation, not a gateway. No auto-block, no invoice total, no causal claim. "
                     "Missing currency/task/price stays visible."),
    }


def render(payload: Dict[str, Any]) -> str:
    lines = [
        "# 持续成本观测 · %s" % payload.get("status"),
        "",
        "> 记录 %d 条；控制模式：%s" % (payload.get("records", 0), payload.get("control")),
        "",
        "## 总额（按币种）",
        "",
    ]
    for currency, amount in (payload.get("totals") or {}).items():
        lines.append("- %s %s" % (amount, currency))
    lines.extend(["", "## 基线", ""])
    for row in (payload.get("baseline") or {}).get("groups") or []:
        lines.append("- %s / %s：%s，%s 天，日中位 %s" % (
            row["tool"], row["model"], row["status"], row.get("days"),
            row.get("daily_median")))
    lines.extend(["", "## 异常", ""])
    if payload.get("anomalies"):
        for row in payload["anomalies"]:
            lines.append("- [%s] %s %s：%s" % (
                row.get("severity"), row.get("kind"), row.get("tool") or row.get("task_id") or "",
                row.get("suggestion")))
    else:
        lines.append("- 未发现异常。这不等于没有异常，只表示当前记录没有触发规则。")
    lines.extend(["", "## 边界", "", "- 只读观测，不阻断支付，不替代账单审计。", ""])
    return "\n".join(lines)
