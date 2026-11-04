# v0.12.0 — buyer-side finance with explicit unknowns

CLI release 0.12.0. Provider SDK stays 0.3.0 (`@verovo/mcp`).

This release adds a narrow canonical finance chain on top of Period Intake,
approved Rule Maps and Task Economics. It connects bills, approved pricing,
allocation drivers, delivery evidence and task outcomes without turning a
vendor export into buyer verification.

## What is in it

- **Canonical chain and runner**
  (`healthcheck/verovo_healthcheck/canonical_finance.py`,
  `scripts/run_canonical_finance.py`): known CSV/JSONL sources, one
  vendor/account/currency, approved per-unit rules, Decimal recalculation and
  Task Economics. Unsupported or unapproved input fails closed.
- **Unknowns are first-class**: missing amount, currency or quantity is
  `unknown/open`, never zero or a default currency. Complete totals, CPS and ROI
  stay null when their required inputs are unknown; attributed subtotals remain
  visible separately.
- **Allocation and reconciliation are orthogonal**: coverage records
  `allocatable = allocated + unallocated + non_allocatable`; amount mismatches
  are `difference`, not reconciled. Shared costs require versioned drivers and
  approval; missing weights are not averaged.
- **Four evidence states**: `Invoice Matched`, `Vendor Reconciled`,
  `Buyer Verified` and `Delivery Accepted`, each with source party, collection
  method, independence and recalculation status. Two vendor exports can support
  Vendor Reconciled but never Buyer Verified.
- **Delivery and cost**: missing delivery evidence makes `verified_success`
  null, not false. Cost per Verified Success is distinct from ROI; unverified
  business value never fills the ROI gap.
- **Complex synthetic fixture**: shared API key, seat fee, buyer-accepted and
  buyer-rejected delivery, and an unsupported vendor value claim. It produces
  fully loaded cost 86.00 USD and Cost per Verified Success 86.00 USD while ROI
  remains null.

## Install

```bash
pipx install "verovo==0.12.0"
verovo demo
```

For a pinned source install:

```bash
pipx install "git+https://github.com/roy-tong/verovo@v0.12.0#subdirectory=healthcheck"
```

## Verification

- Healthcheck: 583 tests pass.
- Repository root: 59 tests pass.
- Buyer-ledger: 110 tests pass.
- `verovo selftest`: PASS.
- 18 registry metrics valid; 21 vocabularies in sync.
- Conformance: metrics 22/22, outcome 15/15, delegation 23/23, SSE 2/2.

## Boundary

The canonical slice is deliberately narrow: per-unit pricing and approved
allocation only, no FX, general contract parsing, arbitrary invoice formats or
external signature/identity verification. OCR accuracy and real supplier data
remain unverified. The complex fixture is synthetic, not a production customer
result. A computed cost or coverage number is not a recovered amount, and
acceptance is not settlement.
