# Audit golden case

Synthetic, local-only case that pins the Audit Case / Audit Finding contract and
the difference between a proven finding, a settled finding and an undecidable
one. It is not a real vendor relationship and makes no claim about a real bill.

## Files

| File | Role |
|---|---|
| `manifest.json` | Case metadata and the list of source records. |
| `contract-rules.json` | Confirmed rules, each with `rule_id`, `version`, `confirmed_by`, `confirmed_at` and the numeric `parameters` the builder consumes (unit prices, discount rate/threshold, allocation policy). A missing parameter fails the build; the builder has no built-in defaults. |
| `tasks.csv` | Task records, including the cancelled task. |
| `runs.jsonl` | Run records linked to tasks. |
| `outcomes.csv` | Buyer and vendor outcome records. `outcome-003` is explicitly unverified. |
| `invoices/invoice-2026-10.json` | USD invoice lines: correct charges, duplicate, discount, rate, allocation, missing task and cancellation. |
| `invoices/invoice-2026-10-eur.json` | EUR line with no confirmed FX rate. |
| `invoices/credit-register-2026-11.json` | Credit register as of 2026-11-02. It contains the applied duplicate credit and no refund credit for `chg-1008`. A credit counts as settlement only when `applied` is true with `applied_at`; issuance alone is not settlement. |
| `audit-case.json` | The Audit Case consumed by the validator. |
| `expected-findings.json` | Independent oracle: expected classification, status, provability, amount, rule version and evidence location per finding. |

## Run the checks

From the repository root:

```bash
python3 scripts/validate_audit_case.py examples/audit-golden-case/audit-case.json
python3 -m unittest discover -s tests
```

## Reading the case

- `F-001` is realized: the duplicate credit `CN-2026-11-004` is applied
  (`applied: true` with `applied_at` in the register), so it has a
  `settlement_ref`. Accepted is not the same state as realized, and an
  issued-but-unapplied credit would keep the finding `identified`.
- `F-002` (missing task id) and `F-007` (unverified business outcome) are
  UNPROVABLE: `provable=false`, a reason, and `amount.value=null`. They are not
  zero.
- `F-003` is a proven currency mismatch. Its EUR 90.00 amount is preserved, but
  it stays outside USD coverage because no confirmed FX rate exists.
- All amounts are two-decimal decimal strings ("120.00"), computed with
  `Decimal` end to end; JSON numbers never appear in an amount field.
- Coverage is pre-credit USD invoice value that is either reconciled
  (`verified_amount`) or under an open finding (`unverified_amount`). Each
  affected invoice line contributes once - its worst single open finding, so
  two findings disputing the same line cannot double count. The settled
  duplicate is excluded from unverified coverage; the EUR line is outside USD
  coverage. The USD 600 shared-cost finding changes task attribution, not the
  USD 900 invoice total. `verified + unverified + settled` reconciles to the
  USD 2,200.00 invoice total.
- The USD 75.00 line behind `F-002` is retained in `unverified_amount` as a
  billed amount, while the finding's own `amount.value` stays `null`: the
  invoice could be read, but the task attribution needed to decide the finding
  cannot.
- Correct charges are `line-004` and `line-009`; the first occurrence of
  `chg-1002` (`line-002`) is also a correct charge, while `line-003` is the
  duplicate.
