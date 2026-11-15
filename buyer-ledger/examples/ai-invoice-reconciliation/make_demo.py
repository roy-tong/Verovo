#!/usr/bin/env python3
"""Generate a synthetic one-vendor, one-account, one-period AI bill.

The numbers are synthetic and clearly labeled. They exist to exercise the
reconciliation logic: four input buckets, the cost export omitting the
cache-read bucket entirely (an omission, not a cent-level rounding artifact),
seats/credits/tax only on the invoice, and a small unexplained residual
injected as a 1h-cache-write tier difference.

V-01: the injected scenario is written to ground-truth.example.json. The
published demo does NOT ship ground-truth.json, so reconcile.py keeps the
residual UNPROVABLE; tests copy the example file to ground-truth.json to
exercise the `confirmed` path only under an explicit `synthetic_ground_truth`
label. The cache omission is deliberately NOT
declared as ground truth — the cache gap must stay multi-candidate, because
in a real reconciliation no generator exists to vouch for it.
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

    # Cost export: the cache-read bucket is omitted on every row. Each omitted
    # amount is $15-$150 (50M-500M tokens at $0.30/M), i.e. far above a cent,
    # so the gap demonstrates an omitted line item, not rounding to $0.00.
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
    # V-01 合成 ground truth：生成器对注入情景的自证声明。它允许 reconcile.py
    # 把 residual 标为 confirmed，但必须显式携带 synthetic_ground_truth，
    # 不得冒充真实独立证据；cache 扣除故意不声明，留给多候选假设。
    # 注意：发布用的示例不随附 ground-truth.json，只写 ground-truth.example.json；
    # reconcile.py 不会读取示例文件，公开样例的 residual 因此保持 UNPROVABLE。
    ground_truth = {
        "schema": "verovo.example-ground-truth/1",
        "synthetic": True,
        "note": "Declared injections behind the synthetic bill. This file is "
                "self-declared by the generator; it is synthetic_ground_truth "
                "for demo purposes only and is never independent evidence in "
                "the V-01 sense.",
        "injections": [
            {"affects": "unexplained_residual",
             "hypothesis": "price_tier_mismatch",
             "how": f"residual = recalc_total x 0.375% charged as if the 1h "
                    f"cache-write tier were miscalculated ({residual:.2f})",
             "synthetic_ground_truth": True},
        ],
        "not_declared": [
            {"affects": "cache_gap",
             "why": "the cache-read omission is deliberately not declared; "
                    "reconcile.py must keep the cache gap multi-candidate "
                    "(omitted bucket / time window / async revision) because "
                    "no independent evidence exists"},
        ],
    }
    (HERE / "ground-truth.example.json").write_text(
        json.dumps(ground_truth, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8")
    # 发布样例不带 ground-truth.json：复制示例文件才会启用 confirmed 路径。
    (HERE / "ground-truth.json").unlink(missing_ok=True)
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
    print(f"wrote usage/cost/invoice/ground-truth.example for {len(usage_rows)} rows")
    print(f"recalc={recalc_total} cost_export={cost_total} "
          f"cache_gap={cache_gap} invoice={invoice_total} residual={residual}")


if __name__ == "__main__":
    main()
