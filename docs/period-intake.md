# Period intake: from a billing-period bundle to a case

`verovo intake` turns a folder of local records into the task-economics case
that `verovo economics` already understands. The point is not to hide the
finance work; it is to stop hand-writing a case JSON and to keep every
unresolved row visible.

```bash
verovo intake --bundle ./period-2026-10 --out ./period-2026-10-out
# writes case.json, intake-report.json, intake-report.md
verovo economics --case ./period-2026-10-out/case.json --markdown economics.md
```

Exit codes: `0` case assembled (possibly `partial`), `3` blocked (no tasks could
be assembled), `2` manifest/input/output error.

## Bundle layout

```
period-2026-10/
  manifest.json
  tasks.csv
  runs.jsonl
  usage.jsonl
  bills.csv
  outcomes.csv
  invoices/            # optional PDFs/images; invoice2data required
```

`manifest.json`:

```json
{
  "case_id": "period-2026-10",
  "currency": "USD",
  "period": {"start": "2026-10-01", "end": "2026-10-31"},
  "allocation_rules": [{"id": "equal", "version": "1", "method": "equal"}],
  "sources": {
    "tasks":    [{"path": "tasks.csv", "format": "tasks-csv"}],
    "runs":     [{"path": "runs.jsonl", "format": "runs-jsonl"}],
    "usage":    [{"path": "usage.jsonl", "format": "usage-jsonl"}],
    "bills":    [{"path": "bills.csv", "format": "bills-csv", "vendor": "openai"}],
    "outcomes": [{"path": "outcomes.csv", "format": "outcomes-csv"}],
    "invoices": [{"path": "invoices"}]
  }
}
```

Supported formats: `tasks-csv`, `runs-jsonl`, `usage-jsonl`, `bills-csv`,
`outcomes-csv`; `invoices` is a file or directory handled by the optional
`invoice2data` adapter. Unknown formats are rejected rather than guessed.

## Column contracts

| Source | Required | Optional |
| --- | --- | --- |
| `tasks-csv` | `task_id` | `type` |
| `runs-jsonl` | `run_id` | `task_id` |
| `usage-jsonl` | `usage_id`, `run_id` | `cost`, `currency`, `financialized`, `source_ref`, `charge_id`, `task_id` |
| `bills-csv` | `charge_id` (or `invoice_id`/`id`), `amount` | `currency`, `task_id`, `allocation_rule`, `vendor` |
| `outcomes-csv` | `task_id` (or `conversation_id`), `status`, `verified` | `value`, `value_verified`, `currency`, `source_ref` |

`verified` / `value_verified` / `financialized` accept literal `true/false`,
`1/0`, `yes/no`; anything else is treated as **false**, never as true.

## What intake does with a row

- **Bill matched to a financialized usage row** (same `charge_id`): kept once as
  usage cost and listed under `reconciled_charges`; an amount disagreement is
  recorded as `charge_amount_mismatch` instead of silently picking a side.
- **Bill with `task_id`**: a direct cost item.
- **Bill with `allocation_rule`**: a shared cost item allocated by the rule.
- **Bill with neither**: kept as a cost item, but it stays unallocated and the
  case's ROI becomes `null` with the reason `unallocated records make total cost
  incomplete`.
- **Currency not equal to the case currency**: excluded and listed; no FX is
  applied.
- **Unverified outcome**: kept as a row with `verified: false`; it cannot enter
  the verified-success denominator or ROI.
- **Unreviewed invoice**: a document draft; it is never booked as cost.

## Intake report

`intake-report.json` (and the Markdown mirror) contains:

- `status`: `complete` / `partial` / `blocked`;
- `counts` and `coverage` (tasks without runs or outcomes);
- `reconciliation`: billed total, financialized usage total, bill/usage charge
  ids, reconciled charges and the explicit boundary that the two totals are not
  required to match;
- `open_items`: every excluded or unlinked row, each with a `kind` and the
  source file;
- `currency_mismatches`, `duplicates`, `unmatched_charges`.

## Boundary

Intake is local and deterministic. It does not call suppliers, does not convert
currency, does not infer tax/credits/refunds, does not verify an invoice and does
not decide whether a charge is correct. It only assembles evidence and names
what is missing, so a finance reviewer can finish the period without trusting a
precise-looking number.
