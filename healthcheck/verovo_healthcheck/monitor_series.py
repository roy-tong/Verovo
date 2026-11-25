"""Read-only UTC minute/hour/day spend series from persistent monitor state."""
from __future__ import annotations

from datetime import timezone
from decimal import Decimal
from pathlib import Path

from . import budget_control, monitor_state

SCHEMA = "verovo.spend-time-series/1"
GRANULARITIES = ("minute", "hour", "day")


def _bucket(value, granularity):
    observed = budget_control._instant(value).astimezone(timezone.utc)
    if granularity == "day":
        observed = observed.replace(hour=0, minute=0, second=0, microsecond=0)
    elif granularity == "hour":
        observed = observed.replace(minute=0, second=0, microsecond=0)
    else:
        observed = observed.replace(second=0, microsecond=0)
    return observed.isoformat().replace("+00:00", "Z")


def build(state_path, *, granularity="day", scope=None, start=None, end=None):
    granularity = str(granularity or "day")
    if granularity not in GRANULARITIES:
        raise ValueError("granularity must be minute, hour or day")
    scope = scope if isinstance(scope, dict) else {}
    start_at = budget_control._instant(start) if start else None
    end_at = budget_control._instant(end) if end else None
    if start_at and end_at and end_at <= start_at:
        raise ValueError("end must be after start")
    state = monitor_state._load(Path(state_path))
    groups = {}
    excluded_scope = 0
    matched_records = 0
    for entry in state["records"]:
        record = entry["record"]
        if not budget_control._scope_matches(record, scope):
            excluded_scope += 1
            continue
        observed = budget_control._instant(record.get("observed_at"))
        if start_at and observed < start_at:
            continue
        if end_at and observed >= end_at:
            continue
        matched_records += 1
        bucket_start = _bucket(record["observed_at"], granularity)
        currency = str(record.get("currency") or "unknown").upper()
        key = (bucket_start, currency)
        bucket = groups.setdefault(key, {"amount": Decimal("0"), "records": 0, "retry_records": 0})
        bucket["amount"] += budget_control._decimal(record.get("amount"), "amount")
        bucket["records"] += 1
        bucket["retry_records"] += int(bool(record.get("retry")) or str(record.get("attempt") or "") not in ("", "1"))
    rows = []
    for (bucket_start, currency), bucket in sorted(groups.items()):
        rows.append({
            "bucket_start": bucket_start, "currency": currency,
            "amount": budget_control._text(bucket["amount"]),
            "records": bucket["records"], "retry_records": bucket["retry_records"],
            "cost_state": "local_observed_not_final_bill",
        })
    return {
        "schema": SCHEMA,
        "granularity": granularity,
        "timezone": "UTC",
        "window": {"start": start, "end": end, "end_exclusive": True},
        "scope": dict(scope),
        "series": rows,
        "coverage": {
            "state_records": len(state["records"]),
            "matched_records": matched_records,
            "buckets": len(rows),
            "unknown_currency_buckets": sum(row["currency"] == "UNKNOWN" for row in rows),
            "excluded_other_scope_records": excluded_scope,
        },
        "boundary": "Observed local records are grouped by UTC bucket and currency. Unknown currencies stay separate; there is no cross-currency total, final-bill or causal interpretation.",
    }


def render(payload):
    lines = ["费用时间序列：%s（UTC），共 %d 个桶。" % (
        payload["granularity"], payload["coverage"]["buckets"])]
    for row in payload["series"]:
        lines.append("- %s｜%s %s｜记录 %d｜重试 %d" % (
            row["bucket_start"], row["currency"], row["amount"],
            row["records"], row["retry_records"]))
    lines.append("仅汇总本地观察；未知币种单列，不跨币种求和，也不是最终账单。")
    return "\n".join(lines) + "\n"
