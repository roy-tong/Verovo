# Cost drivers, scenarios and ROI-driven proposals

Three deterministic steps on top of the task-economics case:

```bash
verovo drivers  --case case.json --json drivers.json
verovo optimize --case case.json --scenario scenarios.json \
                --json proposals.json --markdown proposals.md
verovo optimize --case case.json          # proposals without simulation
```

Nothing here calls a provider, converts currency or changes the case file.

## 1. Cost drivers

`drivers` re-buckets the same rows along six dimensions:

| dimension | source of truth |
| --- | --- |
| `vendor` | `cost_items.vendor`, `usage.vendor` |
| `category` | `cost_items.category` (falls back to `not-recorded`, usage rows are `usage`) |
| `task_type` | resolved through `task_id` / `run_id → task.type`; shared rows are `shared` |
| `model` | `usage.model` / `cost_items.model`; missing is `not-recorded` |
| `retry` | `retry`/`attempt` fields; missing is `not-separable` |
| `allocation` | `direct`, `shared:<rule>` or `shared:unallocated` |

Two totals are reported separately:

- `total_recorded_cost` — every cost row in the case;
- `attributable_cost` — the part the economics engine can actually load onto
  tasks; `unattributed_cost` is the rest.

The difference is the point: spreading unattributed cost across tasks would make
every number look complete and none of them true. `drivers` also lists `risks`
(concentration, unobservable retries) and `unknowns` (missing vendor, unlinked
rows, failed economics evaluation).

## 2. Scenario simulation

A scenario document is explicit assumptions, not a forecast:

```json
{
  "scenarios": [
    {"id": "openai-30pct", "description": "assume 30% lower OpenAI cost (unverified)",
     "rules": [{"match": {"vendor": "openai"}, "action": "scale_cost", "factor": 0.7}]},
    {"id": "drop-shared", "description": "assume the shared tool fee ends",
     "rules": [{"match": {"cost_id": "shared-1"}, "action": "drop"}]}
  ]
}
```

Rules match rows by any case field (`vendor`, `task_id`, `cost_id`, `model`,
`task_type`, `retry`, …) and apply one of:

- `scale_cost` with `factor` (0–10);
- `drop` (sets the row's amount to zero).

The engine copies the case, applies the rules, re-runs `task_economics.evaluate`
and reports baseline vs scenario: fully-loaded cost, cost per verified success,
ROI and the delta. If assumptions make the case invalid the scenario is
`invalid` with a reason — it is never silently skipped.

A computed simulation answers "if this assumption held, this is the arithmetic".
It is not evidence that the assumption holds, not a saving, and not a causal
claim.

## 3. Proposals

`optimize` turns drivers plus baseline economics into review items:

- unallocated cost → reconcile before trusting ROI;
- no verified success → instrument outcomes first;
- one vendor ≥ 40% of cost → test substitution on a sample with a quality gate;
- retry cost not separable → record `attempt`/`retry` first;
- shared cost ≥ 40% → review the allocation rule and its version;
- each computed scenario → turn the assumption into a pre-registered test.

Every proposal carries `evidence`, `verification_plan`, `requires_human: true`
and `automatic_apply: false`. The tool cannot change budgets, providers or the
case.

## Boundary

Cost visibility is not profitability: revenue, gross margin and contract terms
are not in the case. A lower simulated cost is not a realized saving, and a
higher ROI is not proof that the change caused it. Those remain finance and
buyer decisions, with a verification period.
