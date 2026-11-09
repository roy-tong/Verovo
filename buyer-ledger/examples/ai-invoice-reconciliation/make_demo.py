#!/usr/bin/env python3
"""Generate a synthetic one-vendor, one-account, one-period AI bill.

The numbers are synthetic and clearly labeled. They exist to exercise the
reconciliation logic: four input buckets, cache reads rounded to $0.00 in the
cost export, seats/credits/tax only on the invoice, and a small unexplained
residual.
"""
from __future__ import annotations

import csv
import json
import random
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

HERE = Path(__file__).resolve().parent
random.seed(42)

PRICES = {
    "input_uncached": Decimal("3.00"),
    "cache_read": Decimal("0.30"),
    "cache_write_5m": Decimal("3.75"),
    "cache_write_1h": Decimal("6.00"),
    "output": Decimal("15.00"),
}
TEAMS = ["search", "support", "research"]
DAYS = [f"2026-10-{d:02d}" for d in range(1, 31)]


def q(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def main() -> None:
    usage_rows = []
    recalc_total = Decimal("0")
    for day in DAYS:
        for team in TEAMS:
            tokens = {
                "input_uncached": random.randint(5_000_000, 100_000_000),
                "cache_read": random.randint(50_000_000, 500_000_000),
                "cache_write_5m": random.randint(2_500_000, 25_000_000),
                "cache_write_1h": random.randint(1_000_000, 10_000_000),
                "output": random.randint(1_000_000, 15_000_000),
            }
            cost = sum(Decimal(tokens[k]) / Decimal(1_000_000) * PRICES[k]
                       for k in tokens)
            cost = q(cost)
            recalc_total += cost
            usage_rows.append({"date": day, "team": team, **tokens,
                               "recalc_usd": f"{cost:.2f}"})

    # Cost export: cache reads are dropped (rounded to $0.00) on every row.
    cost_rows = []
    cost_total = Decimal("0")
    for row in usage_rows:
        cache_cost = q(Decimal(row["cache_read"]) / Decimal(1_000_000)
                       * PRICES["cache_read"])
        export = q(Decimal(row["recalc_usd"]) - cache_cost)
        cost_total += export
        cost_rows.append({"date": row["date"], "team": row["team"],
                          "cost_usd": f"{export:.2f}"})

    recalc_total = q(recalc_total)
    cost_total = q(cost_total)
    cache_gap = q(recalc_total - cost_total)

    seats = Decimal("6000.00")
    credits = Decimal("-20000.00")
    subtotal = q(recalc_total + seats + credits)
    tax = q(subtotal * Decimal("0.08"))
    residual = q(recalc_total * Decimal("0.00375"))
    invoice_total = q(subtotal + tax + residual)

    invoice_lines = [
        {"line": "usage_charges", "amount": f"{recalc_total:.2f}",
         "note": "vendor usage charges (synthetic)"},
        {"line": "enterprise_seats", "amount": f"{seats:.2f}",
         "note": "seat-based enterprise, not in cost API"},
        {"line": "credits", "amount": f"{credits:.2f}",
         "note": "committed-use credit"},
        {"line": "tax", "amount": f"{tax:.2f}", "note": "8% synthetic tax"},
        {"line": "unexplained_residual", "amount": f"{residual:.2f}",
         "note": "price-tier difference on 1h cache write"},
    ]
    with (HERE / "usage.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(usage_rows[0]))
        w.writeheader()
        w.writerows(usage_rows)
    with (HERE / "cost.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "team", "cost_usd"])
        w.writeheader()
        w.writerows(cost_rows)
    with (HERE / "invoice.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["line", "amount", "note"])
        w.writeheader()
        w.writerows(invoice_lines)
    (HERE / "prices.json").write_text(json.dumps(
        {k: str(v) for k, v in PRICES.items()}, indent=1), encoding="utf-8")
    (HERE / "period.json").write_text(json.dumps({
        "vendor": "anthropic", "account": "acme-prod", "period": "2026-10",
        "currency": "USD", "synthetic": True,
        "recalc_total": f"{recalc_total:.2f}",
        "cost_total": f"{cost_total:.2f}",
        "cache_gap": f"{cache_gap:.2f}",
        "invoice_total": f"{invoice_total:.2f}",
        "residual": f"{residual:.2f}",
    }, indent=1), encoding="utf-8")
    print(f"wrote usage/cost/invoice for {len(usage_rows)} rows")
    print(f"recalc={recalc_total} cost_export={cost_total} "
          f"cache_gap={cache_gap} invoice={invoice_total} residual={residual}")


if __name__ == "__main__":
    main()
