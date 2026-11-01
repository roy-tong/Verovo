# v0.10.0 — buyer review, task economics and period intake

CLI release 0.10.0. Provider SDK stays 0.3.0 (`@verovo/mcp`).

This release moves three main-preview capabilities into a tagged CLI release and
adds the step that was missing between them: turning a real billing period into
the economics case instead of hand-writing JSON.

## What is in it

- **Buyer review** (`verovo review`): scoped buyer tasks, independent charge and
  delivery criteria, local JSON evidence, offline HTML/JSON reports, and three
  conclusion levels that are never collapsed — rule consistency (Level 1),
  evidence credibility (Level 2) and the buyer business decision (Level 3).
  Missing or incompatible evidence stays **UNPROVABLE**.
- **Task economics** (`verovo economics`): direct, allocated and fully loaded
  task cost, cost per verified success, and ROI only when verified value exists
  in the same currency. Duplicate financial rows, cross-currency rows,
  unallocated items and unverified outcomes are reported, not merged.
- **Period intake** (`verovo intake`): a manifest plus local traces, usage rows,
  supplier bills, outcomes and optional invoices produces `case.json` and an
  intake report. Bills reconcile with financialized usage by `charge_id`;
  currency mismatches are excluded (no FX); unreviewed invoices stay documents;
  unallocated cost and unverified outcomes stay named open items.
- **Cost drivers and scenarios** (`verovo drivers`, `verovo optimize`): cost
  buckets by vendor/category/task/model/retry/allocation with recorded versus
  attributable totals; explicit assumptions are simulated on a case copy and
  every proposal carries evidence, a verification plan, `requires_human: true`
  and `automatic_apply: false`.
- Read-only MCP tools for review, invoice intake, period intake and
  optimization; the human decision and any dispute remain CLI-only.

## Install

```bash
pipx install "git+https://github.com/roy-tong/verovo@v0.10.0#subdirectory=healthcheck"
verovo demo
```

The PyPI project `verovo` is still not published; the pinned Git install above
is the supported path for this tag. The previous `agentmeasure` project stays at
0.8.0.

## Verification

- 563 tests pass locally (Python 3.9), including review lanes/levels, economics
  duplicate and unallocated handling, period-intake reconciliation and currency
  exclusion, cost-driver shares, scenario simulation and proposal human gates.
- New documentation: [period intake](period-intake.md),
  [cost drivers and scenarios](cost-optimization.md).

## Boundary

A committed platform action is not a business outcome; a lower simulated cost is
not a realized saving; a computed ROI is not proof of causality. Contract terms,
tax, FX and insurance remain outside this release, and the buyer decision stays
with the buyer.
