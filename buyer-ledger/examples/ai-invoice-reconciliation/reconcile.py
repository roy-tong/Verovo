#!/usr/bin/env python3
"""Reconcile a synthetic AI invoice against cost and usage exports.

Outputs:
  reconciliation.csv   line-level reconciliation with qualification, not fixed reason codes
  reconciliation.json  layered difference model (observed_difference /
                       candidate_explanation / confirmed_explanation / unresolved)
  line-checks.csv      row-level independent cross-checks across sources
  sample-report.md     three-page sample (totals / reconciliation / actions)

V-01 difference-cause qualification rules (差额事实与原因资格分离):

- An amount difference is an observed fact; its cause is a separate claim.
  任何原因不从 `residual != 0` 直接推断。
- Without independent evidence the qualification stays ``unresolved`` and the
  default reason code is ``unexplained``; matched rule patterns surface as
  ``candidate_explanation`` entries with ``basis_refs`` and
  ``confidence_kind=heuristic``.
- A ``confirmed_explanation`` requires independent evidence (an approved
  contract price table, a vendor confirmation, ...). In this synthetic demo
  the only available form is the generator's declared injection
  (``ground-truth.json``), which must be labeled ``synthetic_ground_truth``
  and never poses as real independent proof.
- The accounting identity is kept as a sanity check but is never the sole
  proof of correctness: every (date, team) row is cross-checked between
  usage×prices and the cost export, and the invoice usage line is checked
  against the recalculation independently.
- Negative differences keep their direction and a negative amount; coverage
  above 100% (or a non-positive denominator) is reported as
  ``not_comparable`` with gross/net shown separately, never as a normal
  coverage percentage.

The logic is deliberately small and rule-based: four input buckets, cache
reads, seats, credits, tax and a residual. It is not the full product; it is
the smallest reproducible artifact that shows where an AI invoice and a usage
export disagree — and what would be needed to call the disagreement a cause.
"""
from __future__ import annotations

import csv
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

HERE = Path(__file__).resolve().parent
Q = Decimal("0.01")

SCHEMA = "verovo.example-reconciliation/1"
# 差额原因资格（qualification）值域；默认 unresolved。
QUAL_UNRESOLVED = "unresolved"
QUAL_CONFIRMED = "confirmed"
QUAL_NOT_A_VENDOR_ERROR = "not_a_vendor_error"
ROW_TOLERANCE = Decimal("0.005")  # 行级舍入容差：半分钱以内视为一致


def q(value: Decimal) -> Decimal:
    return value.quantize(Q, rounding=ROUND_HALF_UP)


def money(value: Decimal) -> str:
    return f"{q(value):,.2f}"


def read_usage(data_dir: Path) -> list[dict]:
    with (data_dir / "usage.csv").open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_cost(data_dir: Path) -> list[dict]:
    with (data_dir / "cost.csv").open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_invoice(data_dir: Path) -> dict[str, Decimal]:
    with (data_dir / "invoice.csv").open(encoding="utf-8") as fh:
        return {row["line"]: Decimal(row["amount"]) for row in csv.DictReader(fh)}


def read_ground_truth(data_dir: Path) -> dict | None:
    """合成 ground truth 是生成器的自证声明，不是 V-01 意义上的独立证据。"""
    path = data_dir / "ground-truth.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def row_recalc(row: dict, prices: dict[str, Decimal]) -> Decimal:
    total = Decimal("0")
    for key, price in prices.items():
        total += Decimal(row[key]) / Decimal(1_000_000) * price
    return q(total)


def recalc(rows: list[dict], prices: dict[str, Decimal]) -> Decimal:
    return q(sum((row_recalc(row, prices) for row in rows), Decimal("0")))


def build_line_checks(usage: list[dict], cost: list[dict],
                      prices: dict[str, Decimal]) -> tuple[list[dict], dict]:
    """行级独立核对：usage×prices 与 cost export 两个来源逐行互证。

    每行记录三个可复核事实：重算金额、导出金额、两者之差；result 只声明
    「差额是否恰好等于该行 cache-read 金额」这一模式一致性，不宣称根因。
    该模式一致性是 cache_read_omitted_from_export 候选的行级证据（basis），
    但时间窗/异步修订候选无法被它排除。
    """
    cost_by_key = {(r["date"], r["team"]): Decimal(r["cost_usd"]) for r in cost}
    cache_price = prices.get("cache_read", Decimal("0"))
    rows_out = []
    matched = mismatched = missing = 0
    for row in usage:
        key = (row["date"], row["team"])
        usage_recalc = row_recalc(row, prices)
        cache_part = q(Decimal(row["cache_read"]) / Decimal(1_000_000) * cache_price)
        cost = cost_by_key.get(key)
        if cost is None:
            missing += 1
            result, gap = "no_cost_row", ""
        else:
            gap = q(usage_recalc - cost)
            if abs(gap - cache_part) <= ROW_TOLERANCE:
                result, matched = "match", matched + 1
            else:
                result, mismatched = "mismatch", mismatched + 1
        rows_out.append({
            "date": row["date"], "team": row["team"],
            "usage_recalc_usd": f"{usage_recalc:.2f}",
            "cost_usd": "" if cost is None else f"{cost:.2f}",
            "gap_usd": f"{gap:.2f}" if gap != "" else "",
            "cache_read_usd": f"{cache_part:.2f}",
            "result": result,
        })
    summary = {"rows": len(rows_out), "matched": matched,
               "mismatched": mismatched, "missing_cost_rows": missing,
               "sources": ["usage.csv x prices.json", "cost.csv",
                           "invoice.csv#usage_charges (total level)"]}
    return rows_out, summary


def qualify_residual(residual: Decimal, ground_truth: dict | None) -> dict:
    """observed_difference: invoice 与重计价+已知行之间的差额事实。

    金额本身不证明任何根因；confirmed 只可能来自显式声明的证据（本示例
    中仅为 synthetic_ground_truth 注入声明），否则默认 unresolved。
    """
    explanations = [
        {"hypothesis": "price_tier_mismatch", "confidence_kind": "heuristic",
         "basis_refs": ["reconciliation.json#totals.unexplained_residual"],
         "rule_id": None, "reviewer": None,
         "note": "plausible if the contract tier for 1h cache write differs; "
                 "requires the approved price table"},
        {"hypothesis": "commitment_true_up", "confidence_kind": "heuristic",
         "basis_refs": ["reconciliation.json#totals.unexplained_residual"],
         "rule_id": None, "reviewer": None,
         "note": "commitment drawdown or true-up line; requires the contract"},
        {"hypothesis": "usage_window_revision", "confidence_kind": "heuristic",
         "basis_refs": ["reconciliation.json#totals.unexplained_residual"],
         "rule_id": None, "reviewer": None,
         "note": "usage revised inside the vendor's month-end window; "
                 "requires export timestamps"},
    ]
    qualification = QUAL_UNRESOLVED
    confirmed_note = None
    confirmed_hypothesis = None
    injection = None
    if ground_truth:
        injection = next((i for i in ground_truth.get("injections", [])
                          if i.get("affects") == "unexplained_residual"
                          and i.get("synthetic_ground_truth")), None)
    if injection is not None:
        # 合成注入声明允许 confirmed，但必须显式携带 synthetic_ground_truth
        # 与 basis_refs，报告中同样声明其合成性质——不得冒充真实独立证据。
        qualification = QUAL_CONFIRMED
        confirmed_note = (
            "confirmed ONLY by the generator's declared synthetic injection; "
            "a real confirmation would need an approved contract price table "
            "or a vendor confirmation")
        confirmed_hypothesis = injection.get("hypothesis", "price_tier_mismatch")
        explanations.insert(0, {
            "hypothesis": confirmed_hypothesis,
            "confidence_kind": "confirmed",
            "basis_refs": ["ground-truth.json#injections[unexplained_residual]"],
            "rule_id": None, "reviewer": None,
            "synthetic_ground_truth": True,
            "note": confirmed_note,
        })
        # 同一假设已被 confirmed 覆盖时，不再以 heuristic 形式重复并列
        explanations = [e for e in explanations
                        if not (e["confidence_kind"] == "heuristic"
                                and e["hypothesis"] == confirmed_hypothesis)]
    return {
        "id": "unexplained_residual",
        "kind": "observed_difference",
        "amount_usd": f"{residual:.2f}",
        "direction": ("invoice_higher" if residual > 0
                      else "invoice_lower" if residual < 0 else "equal"),
        "comparison": {
            "formula": "invoice_total - recalc_total - seats - credits - tax",
            "left_source": "invoice.csv",
            "right_source": "usage.csv x prices.json + invoice-only lines",
        },
        "qualification": qualification,
        "status": "UNPROVABLE" if qualification == QUAL_UNRESOLVED else "CONFIRMED_SYNTHETIC",
        "root_cause": confirmed_hypothesis if qualification == QUAL_CONFIRMED else "unknown",
        "closed": qualification == QUAL_CONFIRMED,
        "follow_up": "supplier_dispute_candidate" if qualification == QUAL_UNRESOLVED else None,
        "explanations": explanations,
        "missing_evidence": [
            "approved contract price table (currency, validity, unit, cache tier)",
            "vendor confirmation for the residual line",
        ],
    }


def qualify_cache_gap(cache_gap: Decimal, line_summary: dict) -> dict:
    """observed_difference: cost export 与重计价之间的差额事实。

    AC V01 C：该差额可能来自导出遗漏 cache-read 桶、时间窗或异步修订。
    行级模式一致性（每行差额恰等于该行 cache-read 金额）支持「导出口径
    遗漏」候选，但不能排除其他解释，因此这里永远保持多候选 heuristic、
    资格 unresolved。注意：每条记录的遗漏金额为 $15-$150，不是半分钱
    量级的舍入误差，因此候选名不使用 rounded_to_zero。
    """
    matched, total = line_summary["matched"], line_summary["rows"]
    return {
        "id": "cache_gap",
        "kind": "observed_difference",
        "amount_usd": f"{cache_gap:.2f}",
        "direction": "cost_export_lower" if cache_gap > 0 else "cost_export_higher",
        "comparison": {
            "formula": "recalc_total - cost_total",
            "left_source": "usage.csv x prices.json",
            "right_source": "cost.csv",
        },
        "qualification": QUAL_UNRESOLVED,
        "status": "UNPROVABLE",
        "root_cause": "unknown",
        "closed": False,
        "follow_up": "buyer_data_review",
        "explanations": [
            {"hypothesis": "cache_read_omitted_from_export", "confidence_kind": "heuristic",
             "basis_refs": [f"line-checks.csv ({matched}/{total} rows: gap equals "
                            "the row's cache-read amount)"],
             "rule_id": None, "reviewer": None,
             "note": "the cost export has no cache-read bucket; each row's gap "
                     "equals that row's cache-read charge ($15-$150), far above "
                     "a cent-level rounding. The pattern supports omission but "
                     "cannot exclude the candidates below"},
            {"hypothesis": "time_window_revision", "confidence_kind": "heuristic",
             "basis_refs": ["period.json#period"],
             "rule_id": None, "reviewer": None,
             "note": "vendor revises usage/cost for ~7 days after month-end"},
            {"hypothesis": "async_export_revision", "confidence_kind": "heuristic",
             "basis_refs": ["cost.csv (regeneration timestamp unknown)"],
             "rule_id": None, "reviewer": None,
             "note": "cost export may be generated from a different revision "
                     "than the usage export"},
        ],
        "missing_evidence": [
            "export generation timestamps for usage.csv and cost.csv",
            "vendor statement on cache-read export policy (omitted bucket vs rounding)",
        ],
    }


def coverage_view(explained: Decimal, invoice_total: Decimal,
                  negative_adjustments: Decimal) -> dict:
    """覆盖率口径：负调整存在时 gross/net 分列；净口径 >100% 或分母不可用
    时报告 not_comparable，绝不呈现为正常覆盖率（AC V01 F）。"""
    net_pct = (explained / invoice_total * Decimal("100")) if invoice_total else None
    gross_explained = explained - negative_adjustments  # 去掉负调整项
    gross_invoice = invoice_total - negative_adjustments
    gross_pct = (gross_explained / gross_invoice * Decimal("100")) if gross_invoice else None
    status = "comparable"
    if net_pct is None or net_pct > 100 or net_pct < 0:
        status = "not_comparable"
    return {
        "status": status,
        "net_pct": f"{net_pct:.2f}" if net_pct is not None else None,
        "gross_pct": f"{gross_pct:.2f}" if gross_pct is not None else None,
        "net_definition": "explained (incl. negative credits) / invoice total",
        "gross_definition": "explained (excl. negative credits) / invoice total excl. credits",
        "negative_adjustments_usd": f"{negative_adjustments:.2f}",
    }


def invoice_only_lines(invoice: dict[str, Decimal]) -> list[dict]:
    """invoice 独有行是已分类的合理调整（not_a_vendor_error），不是待定差额。"""
    specs = [
        ("enterprise_seats", "seat-based enterprise spend; not in usage/cost API"),
        ("credits", "committed-use credit; invoice only"),
        ("tax", "tax is not a usage line"),
    ]
    return [{"id": line_id, "amount_usd": f"{invoice.get(line_id, Decimal('0')):.2f}",
             "kind": "invoice_only_adjustment",
             "qualification": QUAL_NOT_A_VENDOR_ERROR,
             "basis_refs": [f"invoice.csv#{line_id}"],
             "note": note} for line_id, note in specs]


def main(data_dir: Path = HERE) -> None:
    prices = {k: Decimal(v) for k, v in
              json.loads((data_dir / "prices.json").read_text()).items()}
    period = json.loads((data_dir / "period.json").read_text())
    ground_truth = read_ground_truth(data_dir)
    usage = read_usage(data_dir)
    cost = read_cost(data_dir)
    invoice = read_invoice(data_dir)

    recalc_total = recalc(usage, prices)
    cost_total = q(sum(Decimal(r["cost_usd"]) for r in cost))
    invoice_total = q(sum(invoice.values()))
    seats = invoice.get("enterprise_seats", Decimal("0"))
    credits = invoice.get("credits", Decimal("0"))
    tax = invoice.get("tax", Decimal("0"))
    residual = q(invoice_total - recalc_total - seats - credits - tax)
    cache_gap = q(recalc_total - cost_total)

    # 金额平衡恒等式保留为 sanity check，但它按定义成立（residual 即定义差），
    # 不构成正确性证明；正确性证据来自下面的行级独立核对。
    assert q(recalc_total + seats + credits + tax + residual) == invoice_total, \
        "accounting identity must hold"

    check_rows, line_summary = build_line_checks(usage, cost, prices)
    with (data_dir / "line-checks.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(check_rows[0]))
        w.writeheader()
        w.writerows(check_rows)

    # 第三来源独立核对：invoice usage 行（vendor 账单）vs 重计价（usage×prices）
    invoice_usage_line = invoice.get("usage_charges")
    invoice_vs_recalc = {
        "invoice_line_usd": f"{invoice_usage_line:.2f}" if invoice_usage_line is not None else None,
        "recalc_total_usd": f"{recalc_total:.2f}",
        "equal": invoice_usage_line is not None
                 and q(invoice_usage_line) == recalc_total,
    }
    line_summary["invoice_usage_line_check"] = invoice_vs_recalc

    residual_diff = qualify_residual(residual, ground_truth)
    cache_gap_diff = qualify_cache_gap(cache_gap, line_summary)
    only_lines = invoice_only_lines(invoice)
    explained = q(recalc_total + seats + credits + tax)
    negative_adjustments = credits if credits < 0 else Decimal("0")
    coverage = coverage_view(explained, invoice_total, negative_adjustments)

    differences = [residual_diff, cache_gap_diff]
    doc = {
        "schema": SCHEMA,
        "period": {"vendor": period.get("vendor"), "account": period.get("account"),
                   "period": period.get("period"), "currency": period.get("currency", "USD"),
                   "synthetic": bool(period.get("synthetic", True))},
        "totals": {
            "invoice_total": f"{invoice_total:.2f}",
            "recalc_total": f"{recalc_total:.2f}",
            "cost_total": f"{cost_total:.2f}",
            "explained_known_lines": f"{explained:.2f}",
            "unexplained_residual": f"{residual:.2f}",
            "cache_gap": f"{cache_gap:.2f}",
        },
        "identity_check": {
            "formula": "recalc + seats + credits + tax + residual = invoice_total",
            "holds": True,
            "note": "holds by definition of residual; a sanity check, not proof",
        },
        "line_checks": line_summary,
        "observed_differences": differences,
        "invoice_only_lines": only_lines,
        "coverage": coverage,
        "notes": [
            "observed_difference and cause qualification are separate layers; "
            "no cause is inferred from a non-zero difference alone",
            "confirmed explanations in this run rest on the generator's "
            "declared synthetic injection (synthetic_ground_truth); they are "
            "not real independent evidence",
        ],
    }
    (data_dir / "reconciliation.json").write_text(
        json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    def hypotheses_label(diff: dict) -> str:
        if diff["qualification"] == QUAL_UNRESOLVED and not diff["explanations"]:
            return "unexplained"
        return "; ".join(f"{e['hypothesis']}({e['confidence_kind']})"
                         for e in diff["explanations"])

    rows = [
        {"line": "usage_charges", "source": "recalc",
         "amount_usd": f"{recalc_total:.2f}", "kind": "component",
         "qualification": "matched_by_line_checks" if line_summary["mismatched"] == 0
                          and invoice_vs_recalc["equal"] else "open_line_mismatches",
         "hypotheses": "-",
         "basis_refs": "line-checks.csv; invoice.csv#usage_charges",
         "note": "four input buckets + output, list price"},
        *[{"line": l["id"], "source": "invoice",
           "amount_usd": l["amount_usd"], "kind": l["kind"],
           "qualification": l["qualification"], "hypotheses": "-",
           "basis_refs": "; ".join(l["basis_refs"]), "note": l["note"]}
          for l in only_lines],
        {"line": "unexplained_residual", "source": "derived",
         "amount_usd": f"{residual:.2f}", "kind": "observed_difference",
         "qualification": residual_diff["qualification"],
         "hypotheses": hypotheses_label(residual_diff),
         "basis_refs": "reconciliation.json#observed_differences",
         "note": "residual after known lines; cause not proven by the amount; "
                 "owner unassigned"},
        {"line": "cache_read_gap", "source": "derived",
         "amount_usd": f"{cache_gap:.2f}", "kind": "observed_difference",
         "qualification": cache_gap_diff["qualification"],
         "hypotheses": hypotheses_label(cache_gap_diff),
         "basis_refs": "line-checks.csv",
         "note": "cost export vs recalculation; multi-candidate, not fixed"},
    ]
    with (data_dir / "reconciliation.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # ---- report -----------------------------------------------------------
    coverage_block = (
        f"| Coverage (net, incl. credits) | {coverage['net_pct']}% |\n"
        f"| Coverage (gross, excl. credits) | {coverage['gross_pct']}% |"
        if coverage["status"] == "comparable" else
        f"| Coverage | not_comparable (net {coverage['net_pct']}% would be "
        f"inflated by negative lines / a negative residual; gross "
        f"{coverage['gross_pct']}% shown separately) |")

    def diff_block(diff: dict) -> str:
        state = ("closed" if diff["closed"] else
                 "open (multi-candidate, unconfirmed)" if len(diff["explanations"]) > 1
                 else "open (unconfirmed)")
        lines = [
            f"### {diff['id']} — {money(Decimal(diff['amount_usd']))} ({diff['direction']})",
            "",
            f"- qualification: `{diff['qualification']}` · status: `{diff['status']}` · root cause: `{diff['root_cause']}` · state: **{state}**",
        ]
        for e in diff["explanations"]:
            tag = " · synthetic_ground_truth" if e.get("synthetic_ground_truth") else ""
            lines.append(f"- candidate/confirmed: `{e['hypothesis']}` "
                         f"({e['confidence_kind']}{tag}) — basis: "
                         f"{', '.join(e['basis_refs'])}")
        for missing in diff["missing_evidence"]:
            lines.append(f"- missing evidence: {missing}")
        return "\n".join(lines)

    mismatch_note = (
        f"All {line_summary['rows']} rows agree between usage×prices and the "
        f"cost export, and the invoice usage line equals the recalculation "
        f"({invoice_vs_recalc['recalc_total_usd']})."
        if line_summary["mismatched"] == 0 and invoice_vs_recalc["equal"] else
        f"**Open row mismatches: {line_summary['mismatched']} row(s) differ "
        f"between usage×prices and the cost export; "
        f"invoice usage line equals recalc: {invoice_vs_recalc['equal']}. "
        f"The totals identity still holds by definition — it cannot clear "
        f"these rows.**")

    residual_read = (
        f"{money(residual)} carries qualification `{residual_diff['qualification']}`, "
        f"confirmed only by the synthetic declaration in ground-truth.json — "
        f"never by real independent evidence."
        if residual_diff["qualification"] == QUAL_CONFIRMED else
        f"{money(residual)} is still unexplained: qualification "
        f"`{QUAL_UNRESOLVED}`, status `UNPROVABLE`, root cause `unknown`; "
        f"the price-tier hypothesis needs the contract price table.")

    report = f"""# AI invoice reconciliation sample (synthetic)

> **Synthetic demo, not a client.** One vendor ({period['vendor']}), one account
> ({period['account']}), one period ({period['period']}), USD. The numbers are
> generated to exercise the reconciliation; they are not a real bill. The
> published sample confirms no cause: the generator's synthetic injection is
> available only as ground-truth.example.json, and tests copy it to exercise
> the confirmed path (synthetic_ground_truth, never independent evidence).

## Page 1 · Totals

| Line | USD |
|---|---:|
| Invoice total | {money(invoice_total)} |
| Recalculated from usage (four input buckets + output) | {money(recalc_total)} |
| Cost export total | {money(cost_total)} |
| Explained (recalc + seats + credits + tax) | {money(explained)} |
| Unexplained residual ({residual_diff['direction']}) | {money(residual)} |
| Cache-read gap (cost export vs recalc) | {money(cache_gap)} |
{coverage_block}

**Read this first:** the cost export is {money(cache_gap)} lower than the
recalculated usage — the cost export carries no cache-read bucket, and each
row's gap equals that row's cache-read charge (an omission, not a cent-level
rounding). Whether that omission is the whole cause still has
{len(cache_gap_diff['explanations'])} candidates (omission, time window, async
revision); none is confirmed. The invoice carries {money(abs(credits))} of credits,
{money(seats)} of seats and {money(tax)} of tax that never appear in the usage
API (classified adjustments, not open differences). After those known lines,
{residual_read}

{mismatch_note}

## Page 2 · Reconciliation (facts and qualification, not fixed reason codes)

| Line | Source | USD | Kind | Qualification | Hypotheses |
|---|---|---:|---|---|---|
""" + "\n".join(
        f"| {r['line']} | {r['source']} | {r['amount_usd']} | `{r['kind']}` | "
        f"`{r['qualification']}` | {r['hypotheses']} |"
        for r in rows) + f"""

**Identity (sanity only):** {money(recalc_total)} + {money(seats)} + ({money(credits)}) +
{money(tax)} + {money(residual)} = {money(invoice_total)}. This holds by
definition of the residual and is not a proof of correctness; the row-level
checks in `line-checks.csv` are the independent cross-check.

## Page 3 · Differences, evidence and next actions

{diff_block(residual_diff)}

{diff_block(cache_gap_diff)}

**Missing to close the period**

- Approved contract price table (to confirm or drop the 1h cache-write tier hypothesis).
- Export generation timestamps for usage and cost (to test the time-window/async candidates).
- Key/workspace → team mapping (not needed for this synthetic sample; needed for a real one).
- Whether the {money(abs(credits))} credit is one-off or recurring.

**What this sample does not do**

- It does not prove the vendor overcharged; `supplier_dispute_candidate` is a follow-up state, not a verdict.
- It does not fix a cause on any difference without independent evidence.
- It does not compute total AI TCO (tokens are only one part).
- It does not replace a signed-off close; a human signs.

*Generated by `reconcile.py` on synthetic data. Same inputs, same output.*
"""
    (data_dir / "sample-report.md").write_text(report, encoding="utf-8")
    print(f"invoice={money(invoice_total)} recalc={money(recalc_total)} "
          f"cost={money(cost_total)} residual={money(residual)}"
          f"({residual_diff['qualification']}) "
          f"cache_gap={money(cache_gap)}"
          f"({cache_gap_diff['qualification']},"
          f"{len(cache_gap_diff['explanations'])} candidates) "
          f"line_checks={line_summary['matched']}/{line_summary['rows']} match"
          f"{' WARN:' + str(line_summary['mismatched']) + ' row mismatch(es)' if line_summary['mismatched'] else ''} "
          f"coverage_net={coverage['net_pct']}%({coverage['status']})")


if __name__ == "__main__":
    main()
