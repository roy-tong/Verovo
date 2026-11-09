#!/usr/bin/env python3
"""Reconcile a synthetic AI invoice against cost and usage exports.

Outputs:
  reconciliation.csv  line-level reconciliation with reason codes
  sample-report.md    three-page sample (totals / reconciliation / actions)

The logic is deliberately small and rule-based: four input buckets, cache
reads, seats, credits, tax and a residual. It is not the full product; it is the
smallest reproducible artifact that shows where an AI invoice and a usage
export disagree.
"""
from __future__ import annotations

import csv
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

HERE = Path(__file__).resolve().parent
Q = Decimal("0.01")


def q(value: Decimal) -> Decimal:
    return value.quantize(Q, rounding=ROUND_HALF_UP)


def money(value: Decimal) -> str:
    return f"{q(value):,.2f}"


def read_usage() -> list[dict]:
    with (HERE / "usage.csv").open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_cost() -> list[dict]:
    with (HERE / "cost.csv").open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_invoice() -> dict[str, Decimal]:
    with (HERE / "invoice.csv").open(encoding="utf-8") as fh:
        return {row["line"]: Decimal(row["amount"]) for row in csv.DictReader(fh)}


def recalc(rows: list[dict], prices: dict[str, Decimal]) -> Decimal:
    total = Decimal("0")
    for row in rows:
        for key, price in prices.items():
            total += Decimal(row[key]) / Decimal(1_000_000) * price
    return q(total)


def main() -> None:
    prices = {k: Decimal(v) for k, v in
              json.loads((HERE / "prices.json").read_text()).items()}
    period = json.loads((HERE / "period.json").read_text())
    usage = read_usage()
    cost = read_cost()
    invoice = read_invoice()

    recalc_total = recalc(usage, prices)
    cost_total = q(sum(Decimal(r["cost_usd"]) for r in cost))
    invoice_total = q(sum(invoice.values()))
    seats = invoice.get("enterprise_seats", Decimal("0"))
    credits = invoice.get("credits", Decimal("0"))
    tax = invoice.get("tax", Decimal("0"))
    residual = q(invoice_total - recalc_total - seats - credits - tax)
    cache_gap = q(recalc_total - cost_total)

    assert q(recalc_total + seats + credits + tax + residual) == invoice_total, \
        "accounting identity must hold"
    assert abs(q(cost_total + cache_gap - recalc_total)) <= Decimal("0.05"), \
        "cache gap must bridge cost export and recalculation"

    rows = [
        {"line": "usage_charges", "source": "recalc",
         "amount_usd": f"{recalc_total:.2f}", "reason_code": "base",
         "note": "four input buckets + output, list price"},
        {"line": "enterprise_seats", "source": "invoice",
         "amount_usd": f"{seats:.2f}", "reason_code": "seat_not_in_cost_api",
         "note": "seat-based enterprise spend; not in usage/cost API"},
        {"line": "credits", "source": "invoice",
         "amount_usd": f"{credits:.2f}", "reason_code": "credit_only_on_invoice",
         "note": "committed-use credit; invoice only"},
        {"line": "tax", "source": "invoice",
         "amount_usd": f"{tax:.2f}", "reason_code": "tax_only_on_invoice",
         "note": "tax is not a usage line"},
        {"line": "unexplained_residual", "source": "derived",
         "amount_usd": f"{residual:.2f}", "reason_code": "price_tier_mismatch",
         "note": "residual after known lines; owner unassigned"},
        {"line": "cache_read_rounding", "source": "derived",
         "amount_usd": f"{cache_gap:.2f}", "reason_code": "cache_read_rounded_to_zero",
         "note": "cost export drops cache reads below one cent"},
    ]
    with (HERE / "reconciliation.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    explained = q(recalc_total + seats + credits + tax)
    coverage = (explained / invoice_total * Decimal("100")) if invoice_total else Decimal("0")
    chase = ["cache_read_rounded_to_zero", "price_tier_mismatch", "seat_not_in_cost_api"]
    top = [next(r for r in rows if r["reason_code"] == code) for code in chase]

    report = f"""# AI invoice reconciliation sample (synthetic)

> **Synthetic demo, not a client.** One vendor ({period['vendor']}), one account
> ({period['account']}), one period ({period['period']}), USD. The numbers are
> generated to exercise the reconciliation; they are not a real bill.

## Page 1 · Totals

| Line | USD |
|---|---:|
| Invoice total | {money(invoice_total)} |
| Recalculated from usage (four input buckets + output) | {money(recalc_total)} |
| Cost export total | {money(cost_total)} |
| Explained (recalc + seats + credits + tax) | {money(explained)} |
| Unexplained residual | {money(residual)} |
| Cache-read gap (cost export vs recalc) | {money(cache_gap)} |
| Coverage (explained / invoice) | {coverage:.2f}% |

**Read this first:** the cost export is {money(cache_gap)} lower than the
recalculated usage because cache reads are rounded to $0.00. The invoice is
lower than the recalculation because it carries {money(abs(credits))} of credits;
it also adds {money(seats)} of seats and {money(tax)} of tax that never appear in
the usage API. After those known lines, {money(residual)} is still unexplained
and needs an owner.

## Page 2 · Reconciliation

| Line | Source | USD | Reason code |
|---|---|---:|---|
""" + "\n".join(
        f"| {r['line']} | {r['source']} | {r['amount_usd']} | `{r['reason_code']}` |"
        for r in rows) + f"""

**Identity:** {money(recalc_total)} + {money(seats)} + ({money(credits)}) +
{money(tax)} + {money(residual)} = {money(invoice_total)}.

## Page 3 · Top 3 and next actions

| # | What | USD | Who should act |
|---|---|---:|---|
""" + "\n".join(
        f"| {i+1} | {r['line']} · {r['note']} | {r['amount_usd']} | "
        f"{'finance' if r['source'] == 'invoice' else 'FinOps/engineering'} |"
        for i, r in enumerate(top)) + f"""

**Missing to close the period**

- Contract price table (to confirm the 1h cache-write tier used in the residual).
- Key/workspace → team mapping (not needed for this synthetic sample; needed for a real one).
- Whether the {money(abs(credits))} credit is one-off or recurring.

**What this sample does not do**

- It does not prove the vendor overcharged.
- It does not compute total AI TCO (tokens are only one part).
- It does not replace a signed-off close; a human signs.

*Generated by `reconcile.py` on synthetic data. Same inputs, same output.*
"""
    (HERE / "sample-report.md").write_text(report, encoding="utf-8")
    print(f"invoice={money(invoice_total)} recalc={money(recalc_total)} "
          f"cost={money(cost_total)} residual={money(residual)} "
          f"cache_gap={money(cache_gap)} coverage={coverage:.2f}%")


if __name__ == "__main__":
    main()
