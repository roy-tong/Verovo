# AI invoice reconciliation sample (synthetic)

> **Synthetic demo, not a client.** One vendor (anthropic), one account
> (acme-prod), one period (2026-10), USD. The numbers are
> generated to exercise the reconciliation; they are not a real bill. The
> published sample confirms no cause: the generator's synthetic injection is
> available only as ground-truth.example.json, and tests copy it to exercise
> the confirmed path (synthetic_ground_truth, never independent evidence).

## Page 1 · Totals

| Line | USD |
|---|---:|
| Invoice total | 26,563.25 |
| Recalculated from usage (four input buckets + output) | 38,462.06 |
| Cost export total | 31,207.33 |
| Explained (recalc + seats + credits + tax) | 26,419.02 |
| Unexplained residual (invoice_higher) | 144.23 |
| Cache-read gap (cost export vs recalc) | 7,254.73 |
| Coverage (net, incl. credits) | 99.46% |
| Coverage (gross, excl. credits) | 99.69% |

**Read this first:** the cost export is 7,254.73 lower than the
recalculated usage — the cost export carries no cache-read bucket, and each
row's gap equals that row's cache-read charge (an omission, not a cent-level
rounding). Whether that omission is the whole cause still has
3 candidates (omission, time window, async
revision); none is confirmed. The invoice carries 20,000.00 of credits,
6,000.00 of seats and 1,956.96 of tax that never appear in the usage
API (classified adjustments, not open differences). After those known lines,
144.23 is still unexplained: qualification `unresolved`, status `UNPROVABLE`, root cause `unknown`; the price-tier hypothesis needs the contract price table.

All 90 rows agree between usage×prices and the cost export, and the invoice usage line equals the recalculation (38462.06).

## Page 2 · Reconciliation (facts and qualification, not fixed reason codes)

| Line | Source | USD | Kind | Qualification | Hypotheses |
|---|---|---:|---|---|---|
| usage_charges | recalc | 38462.06 | `component` | `matched_by_line_checks` | - |
| enterprise_seats | invoice | 6000.00 | `invoice_only_adjustment` | `not_a_vendor_error` | - |
| credits | invoice | -20000.00 | `invoice_only_adjustment` | `not_a_vendor_error` | - |
| tax | invoice | 1956.96 | `invoice_only_adjustment` | `not_a_vendor_error` | - |
| unexplained_residual | derived | 144.23 | `observed_difference` | `unresolved` | price_tier_mismatch(heuristic); commitment_true_up(heuristic); usage_window_revision(heuristic) |
| cache_read_gap | derived | 7254.73 | `observed_difference` | `unresolved` | cache_read_omitted_from_export(heuristic); time_window_revision(heuristic); async_export_revision(heuristic) |

**Identity (sanity only):** 38,462.06 + 6,000.00 + (-20,000.00) +
1,956.96 + 144.23 = 26,563.25. This holds by
definition of the residual and is not a proof of correctness; the row-level
checks in `line-checks.csv` are the independent cross-check.

## Page 3 · Differences, evidence and next actions

### unexplained_residual — 144.23 (invoice_higher)

- qualification: `unresolved` · status: `UNPROVABLE` · root cause: `unknown` · state: **open (multi-candidate, unconfirmed)**
- candidate/confirmed: `price_tier_mismatch` (heuristic) — basis: reconciliation.json#totals.unexplained_residual
- candidate/confirmed: `commitment_true_up` (heuristic) — basis: reconciliation.json#totals.unexplained_residual
- candidate/confirmed: `usage_window_revision` (heuristic) — basis: reconciliation.json#totals.unexplained_residual
- missing evidence: approved contract price table (currency, validity, unit, cache tier)
- missing evidence: vendor confirmation for the residual line

### cache_gap — 7,254.73 (cost_export_lower)

- qualification: `unresolved` · status: `UNPROVABLE` · root cause: `unknown` · state: **open (multi-candidate, unconfirmed)**
- candidate/confirmed: `cache_read_omitted_from_export` (heuristic) — basis: line-checks.csv (90/90 rows: gap equals the row's cache-read amount)
- candidate/confirmed: `time_window_revision` (heuristic) — basis: period.json#period
- candidate/confirmed: `async_export_revision` (heuristic) — basis: cost.csv (regeneration timestamp unknown)
- missing evidence: export generation timestamps for usage.csv and cost.csv
- missing evidence: vendor statement on cache-read export policy (omitted bucket vs rounding)

**Missing to close the period**

- Approved contract price table (to confirm or drop the 1h cache-write tier hypothesis).
- Export generation timestamps for usage and cost (to test the time-window/async candidates).
- Key/workspace → team mapping (not needed for this synthetic sample; needed for a real one).
- Whether the 20,000.00 credit is one-off or recurring.

**What this sample does not do**

- It does not prove the vendor overcharged; `supplier_dispute_candidate` is a follow-up state, not a verdict.
- It does not fix a cause on any difference without independent evidence.
- It does not compute total AI TCO (tokens are only one part).
- It does not replace a signed-off close; a human signs.

*Generated by `reconcile.py` on synthetic data. Same inputs, same output.*
