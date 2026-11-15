# Why your AI invoice doesn't match your usage export: 7 failure modes

> A reproducible checker for OpenAI/Anthropic cost exports, usage exports and invoices.
> Every number below is synthetic or public; the method is the point.

Your AI invoice and your usage export are two different documents, produced by
two different systems, for two different purposes. They will not match. The
reasons are boring, and each one costs money.

This is the checklist I use before I trust either number. It is written for the
person who has to explain the AI line at month-end: a FinOps lead, a controller,
or the founder who is also the finance team.

---

## 0. The short version

| # | Failure mode | Typical symptom | Who owns it |
|---|---|---|---|
| 1 | Cache reads omitted from the export, or rounded per line | The cost export has no cache-read line, or per-line amounts are under $0.01 | Engineering |
| 2 | Input tokens are four buckets | Naive `tokens × price` is off by 10–40% | Engineering |
| 3 | Seats and enterprise contracts | The cost API total is lower than the invoice | Finance / procurement |
| 4 | Credits, commitments, tax, amortization, FX | Invoice lines with no usage counterpart | Finance |
| 5 | Month-end revision window | The "final" number changes 7 days later | Finance / engineering |
| 6 | Currency normalization | Three clouds, three currencies, no row-level FX | Finance |
| 7 | Shared/historical/personal keys | 30–60% of spend is "unallocated" | Engineering |

The order matters: fix 1–2 before you argue about 3–4. Otherwise you are
comparing a wrong usage number to a right invoice number.

---

## 1. Cache reads omitted from the export, or rounded per line

Anthropic's Console gives you two CSVs. The **Cost** export is in dollars; the
**Usage** export is in tokens, with no dollars. Two different things can happen
to the cache-read bucket, and they need different evidence:

- **Omission**: the cost export has no cache-read line at all. The gap on a
  cache-heavy day equals that day's cache charge. At 50M–500M tokens and
  $0.30/M, that is $15–$150 per row — far above a cent, so it cannot be a
  rounding artifact.
- **Rounding**: each exported line is quantized to the cent. Only rows below
  half a cent can disappear; on a 90-row month this shifts the total by at most
  a few cents, not thousands of dollars.

Do not label an omitted bucket as "rounded to $0.00". The synthetic example in
this repository exercises the **omission** case; it does not model per-line
rounding.

**How to check**

```bash
cd examples/ai-invoice-reconciliation
python3 reconcile.py
# Then compare cost.csv and usage.csv for the same day:
# - no cache-read cost line, or gap == tokens/1e6 * cache price  -> omission
# - per-line gap < $0.005                                       -> rounding
```

**What the residual-like gap looks like**: a positive gap between recalculated
usage and the cost export, concentrated on high-cache-read rows. The pattern
supports a candidate; it does not by itself prove the cause.

---

## 2. Input tokens are four buckets, not one

Anthropic prices input in four non-overlapping buckets:

- uncached input: 1×
- cache read: 0.1×
- 5-minute cache write: 1.25×
- 1-hour cache write: 2×

OpenAI's `input_tokens` already includes cached tokens. If you multiply
`input_tokens × input_price`, you over-count cached tokens at the uncached
price. If you subtract cache reads twice, you under-count.

**How to check**

```text
recalc = uncached × 3.00
       + cache_read × 0.30
       + cache_write_5m × 3.75
       + cache_write_1h × 6.00
       + output × 15.00
# all per 1M tokens; replace with your contract prices
```

If your recalc is off by a clean 10–40%, this is usually why.

---

## 3. Seats and enterprise contracts

The Usage & Cost APIs are usage-based. For **seat-based Enterprise** plans,
Anthropic's docs say the cost/usage endpoints reflect usage credits only; the
seat spend itself does not appear. Pro/Team seats are a fixed fee, and per-person
usage is not shown as spend. Azure/Bedrock AI usage is mixed into the cloud
contract and commitments.

So the invoice can be higher than the cost API by the seat line, and no amount
of token math will find it.

**How to check**: reconcile the invoice to `usage + seats + credits + tax`, not
to usage alone. If the seat line is missing from your model, every month will
have an unexplained positive residual.

---

## 4. Credits, commitments, tax, amortization, FX

These lines exist on the invoice and not in the usage API:

- credits (including committed-use credits)
- commitment drawdown / true-up
- tax
- annual prepay amortization
- FX and payment fees

A credit is not a price change. A commitment is not a discount until it is
consumed. A true-up is not a run-rate. If you put them in the same column, the
month-over-month number becomes meaningless.

**How to check**: keep four columns separate: `usage`, `contract adjustments`,
`tax/fees`, `residual`. The invoice total should equal the sum; if it doesn't,
the residual is the line to chase.

---

## 5. The month-end revision window

Anthropic's docs say usage and cost data can still be revised for about **7 days
after month-end**. Finance close usually can't wait 7 days. So you estimate,
book, and adjust later.

That is not a reconciliation failure; it is a timing difference. But it means
"the number changed" is expected, and you need a versioned estimate-vs-final
record, not a Slack argument.

**How to check**: run the same period twice, once on day 1 and once on day 8;
diff the totals and tag the delta `month_end_revision`.

---

## 6. Currency normalization

FOCUS 1.4 normalizes the schema for AI, cloud, SaaS and data-center bills. It
does not normalize currency. AWS defaults to USD, Azure uses the enrollment
currency, GCP uses the billing account currency. All three can be valid FOCUS
rows with no row-level FX rate.

**How to check**: pull the ECB (or your policy) month-end rate, join on
`BillingCurrency`, and keep the rate date as a column. Do not let a currency
conversion hide inside "other".

---

## 7. Shared, historical and personal keys

The cost API can group by `api_key_id`, `workspace_id`, `project_id`, `model`.
That does not mean your keys are named by team. Shared keys, legacy workspace
keys, personal keys and Claude Code auto-created keys all break the mapping.
Reorgs break it again.

**How to check**: build a key → team/cost-center table, mark every unmapped key
as `unallocated`, and report the unallocated share next to the allocated share.
Do not average shared cost by headcount unless a human has approved that driver.

---

## 8. The residual is the product

After 1–7, there is always a residual: invoice total minus everything you can
explain. The useful question is not "is the vendor wrong?" It is:

> Who owns the residual, and what would make it smaller next month?

A good month-end pack has four numbers:

1. invoice total
2. explained amount
3. unexplained residual
4. coverage (explained / invoice) — with a caliber note: if negative lines
   (credits) or a negative residual are present, net coverage can drift above
   100% and must be reported as gross/net separately or flagged
   `not_comparable`, never presented as a normal coverage percentage.

If coverage is 99%+, the residual is a line item. If it is 80%, you have a data
problem, not a vendor problem.

The residual is an **observed difference**, not a cause. The amount alone
proves nothing: a cause is only `confirmed` with independent evidence (an
approved contract price table, a vendor confirmation), stays `unresolved`
otherwise, and plausible patterns (a tier mismatch, a rounding gap) surface as
candidate explanations with `basis_refs` and `confidence_kind=heuristic`.

---

## 9. A reproducible checker

The smallest useful version is three files in, two files out:

```bash
cd examples/ai-invoice-reconciliation
python3 reconcile.py
# writes reconciliation.csv, reconciliation.json, line-checks.csv and sample-report.md
```

Output:

- `reconciliation.csv`: every invoice line, its source, amount, kind and
  **qualification** (`unresolved` / `confirmed` / `not_a_vendor_error`) — no
  fixed reason codes;
- `reconciliation.json`: the layered difference model — `observed_difference`
  facts, `candidate_explanation` entries with `basis_refs` and
  `confidence_kind=heuristic`, `confirmed_explanation` only with declared
  evidence, `unresolved` as the default state;
- `line-checks.csv`: row-level cross-checks between two independent sources
  (usage×prices vs the cost export), plus the invoice usage line checked
  against the recalculation;
- `sample-report.md`: totals, reconciliation with qualifications, coverage
  calibers (gross/net, `not_comparable` when inflated), open differences and
  the evidence each one is missing.

Two rules the checker enforces on itself:

1. **金额事实与原因资格分离** — no cause is inferred from a non-zero
   difference. In this synthetic demo the generator declares its injected
   scenario in `ground-truth.json` under an explicit `synthetic_ground_truth`
   label, which is the only way the residual reaches `confirmed` here; that
   declaration is a synthetic convenience, never real independent evidence.
   The cache gap is deliberately kept multi-candidate (rounding / time window
   / async revision), because row-level amounts cannot separate them.
2. **恒等式只是 sanity，不是证明** — the accounting identity
   `recalc + seats + credits + tax + residual = invoice total` holds by
   definition of the residual; correctness evidence comes from the row-level
   checks in `line-checks.csv`. If a row disagrees, the identity still holds
   and the mismatch is what gets reported.

---

## 10. What this does not prove

- It does not prove the vendor overcharged.
- It does not fix a cause on any difference: without independent evidence the
  qualification stays `unresolved`, and `supplier_dispute_candidate` is a
  follow-up state, not a verdict.
- It does not compute total AI TCO; tokens are often only a third of it.
- It does not replace a signed-off close. A human signs; the checker prepares.
- It does not make a bad allocation model good. If the key→team mapping is
  wrong, the reconciliation is still "correct but useless".

---

## 11. If you want a second pair of eyes

I am running a few free samples this week: one vendor, one account, one period,
one attribution unit. You send a desensitized invoice + cost export + usage
export; I send back a three-page discrepancy list within 24 hours. No charge,
no pitch. If it is useful, we can talk about the full period.

The checklist above is yours either way.
