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

## Post-optimization effect review

```bash
verovo optimize-verify --baseline baseline-tasks.jsonl \
  --final final-tasks.jsonl --contract optimization-review.json
```

The `verovo.optimization-review/1` contract binds the optimization action, task type, acceptance rubric, currency, non-overlapping before/after windows, `change_effective_at`, comparable task group, minimum sample and maximum success-rate drop. The final window must start after the baseline, and the change must take effect after the baseline and no later than final observations begin. Reversed or overlapping windows are rejected. `keep` requires a known effective time, lower observed cost per task, no increase in cost per verified success and a passing quality guardrail. A guardrail breach recommends `rollback`; missing effective time, insufficient data and unknown cost-per-success remain `inconclusive`.

The result separates observed before/after differences from buyer confirmation. It never claims causal attribution or realized invoice savings, applies no rollback, contacts no external runtime and does not reconcile the supplier bill.

### Persistent optimization review loop

```bash
verovo optimize-loop create --contract optimization-loop.json --out ./optimization-loop
verovo optimize-loop tick --dir ./optimization-loop --at 2026-11-21T00:00:00Z
verovo optimize-loop status --dir ./optimization-loop
verovo optimize-loop pause --dir ./optimization-loop
verovo optimize-loop resume --dir ./optimization-loop
```

A `verovo.optimization-loop/1` contract binds a named owner, local-only authorization, registration time, interval, baseline/final task files and the existing review contract. Each due, explicit tick reloads those files, records their SHA-256 values and reuses `optimize-verify`; changed sources trigger a new review record without rewriting history. Registration drift, naive timestamps and unavailable files are rejected. Pause blocks future ticks and resume makes the loop due. A quality breach produces `rollback_required`, while `rollback_applied` and external runtime contact remain false. External cron/host scheduling is required; this is not a daemon, model router, configuration writer or realized-saving ledger. MCP exposes `optimization_loop_create/tick/status/pause/resume` through the same local implementation.

## Advisory budget control signals

```bash
verovo budget check --state monitor-state.json --budget budget.json
```

A `verovo.spend-budget/1` contract declares budget ID, currency, limit, `warn_at`, `hold_at`, a half-open period, optional task/tool/model scope and an anomaly severity that triggers review. Decisions use `Decimal` arithmetic and never sum currencies. Unknown currency and current qualifying anomalies produce `review` unless the hold threshold already requires a stronger stop signal. Every result is `advisory_only` with `execution_applied=false`; an external runtime must separately prove whether spending actually stopped.

### Stop-readback reconciliation

```bash
verovo budget verify --state monitor-state.json \
  --decision control-decision.json --receipt control-receipt.json --json
```

A `verovo.spend-control-receipt/1` contains a receipt ID, the same budget ID, requested action and task/tool/model scope as the decision. Its `readback` must report `status=stopped`, an observation time at or after the decision, `source_type=runtime_readback` and a source reference. Its `coverage` declares a complete post-stop observation window and source reference; persistent monitoring must have tracked through that window.

`budget verify` returns PASS only for a matching stopped readback with no positive same-scope charges in the complete window. Later positive charges return FAIL; credits cannot mask them. Missing readback, incomplete coverage or same-timestamp ordering remains UNPROVABLE. Budget, action or scope mismatches fail closed. This checks local receipt and monitor consistency only: it contacts no runtime and does not prove that the external receipt or source records are authentic.

## Spend time series

```bash
verovo monitor series --state monitor-state.json --granularity day --json
verovo monitor series --state monitor-state.json --granularity hour \
  --task-id TASK --start 2026-11-01T00:00:00Z --end 2026-11-02T00:00:00Z
```

`series` reads persistent monitoring state without writing it. Minute, hour and day buckets use UTC; the optional window is half-open and task/tool/model filters share the budget scope semantics. Each currency is a separate series, including an explicit UNKNOWN bucket. Rows contain observed amount, record count and retry count. There is no cross-currency total, final-bill claim or causal interpretation. MCP exposes the same projection as `monitor_series` with `state`, `granularity`, optional `scope`, `start` and `end`.

## Named budget recovery

```bash
verovo budget resume --state monitor-state.json --budget current-budget.json \
  --recovery recovery.json --out recovery-history.jsonl --json
```

A `verovo.spend-budget-recovery/1` requires a recovery ID, matching budget/scope, named confirmer, confirmation time/reference, reason and a runtime readback. Recovery passes only when the current re-evaluated budget is `allow` or `warn` and the readback reports `running` at or after confirmation. A current `hold` or `review` cannot be overridden by the declaration alone; missing running evidence stays UNPROVABLE.

Observed spend is preserved and the budget is not reset. The append-only local history binds a recovery ID to its source content and rejects conflicting reuse. Only an identical source and identical outcome are deduplicated; a FAIL→PASS or other outcome transition appends a new record. Verovo contacts no external runtime; PASS is local confirmation/readback consistency, not authenticated production recovery. MCP exposes this as `budget_resume` with `state`, `budget`, `recovery` and `out`.

## Persistent incremental monitoring

```bash
verovo monitor freeze-baseline --file usage.jsonl --state monitor-state.json
verovo monitor track --file usage.jsonl --state monitor-state.json
```

`track` deduplicates normalized records and keeps the frozen baseline separate from pending observations. Repeating an input file creates no new records or new anomalies; active anomalies retain first/last-seen history. Late records remain pending even when their timestamps precede the baseline's latest observation. Rebuilding a baseline is explicit through `freeze-baseline`. State writes are locked and atomic, and damaged state is not overwritten. Monitoring remains advisory: it never blocks spending or replaces the supplier bill.

### Local alert outbox

```bash
verovo monitor notify --state monitor-state.json --out spend-alerts.jsonl --severity medium
```

`notify` appends only active anomalies that are not already in the outbox. Repeating `track` or `notify` does not spam the same anomaly; inactive history is not re-emitted. A damaged outbox fails closed. Every `verovo.spend-alert/1` stays `pending_local_delivery`, with external delivery and host wake-up explicitly false. This file is a local handoff for a separately authorized notifier or host poller, not proof that an email, IM, webhook or Agent wake-up was delivered.

To turn one alert into a shared next-action contract:

```bash
verovo monitor resolution --file spend-alerts.jsonl --alert ALERT_ID > alert-resolution.json
python3 -m ovo_acceptance resolution alert-resolution.json --json
```

The projection maps orphan calls to task-mapping data, price drift to applicable-rule data, and retry/spend anomalies to buyer business options. It executes no retry, pause, purchase or supplier disposition. A retry-path pause option remains a recommendation whose external effect needs separate verification.

## Task-spend forecast

```bash
verovo monitor forecast --state monitor-state.json --contract spend-forecast.json --json
```

A `verovo.spend-forecast/1` contract names the task, currency, half-open period and actual progress (`completed_units`, `planned_units`, unit, source reference and observation time). The estimator uses only matching local observations: `observed cost / completed units × remaining units`. Other currencies are excluded, unknown currencies remain visible, and zero progress or no matching cost returns null estimates.

The result is `estimate_not_cap`: future retries, price changes, unfinished work, late records and final supplier charges can differ. It applies no runtime control and is not a causal prediction or final bill.

## Statistical uncertainty in tool-value comparisons

```bash
# Preferred: derive costs and buyer acceptance from Canonical Task Economics
verovo tool-value --economics-case task-case.json --json

# Compatibility: declared CSV/JSONL input; eligibility marks the lower trust level
verovo tool-value --file comparable-tasks.jsonl --json
```

Canonical input refuses incomplete cost coverage or unknown buyer acceptance instead of trusting a caller-declared `cost_basis` string. The task rows carry group, comparable group, rubric, observation time and success evidence from the canonical case.

Each non-empty arm reports a Wilson 95% interval for task success rate. When both arms are present, the treatment-minus-control success difference also reports a Newcombe 95% interval and whether it covers zero. Empty arms keep null intervals; fewer than 30 tasks per arm remains `insufficient_evidence`, and no interval automatically selects a tool winner.

These intervals describe uncertainty in the observed comparable task sample. They do not establish population representativeness, remove selection bias, prove causal incremental value or replace a procurement decision. Cost-per-success changes remain descriptive arithmetic, not confidence intervals for cost.

## Bounded control/treatment task execution

```bash
verovo experiment run --contract controlled-task-run.json --out ./run-evidence --json
```

A `verovo.controlled-task-run/1` declares task IDs, task type, named local-only authorization, control/treatment argv, timeout, output cap and objective success conditions. Each task/arm pair runs in a fresh process and isolated working directory with only OVO experiment environment variables. The offline core accepts a required injected `process_runner`; CLI/MCP explicitly select the separately packaged `verovo_local_runner` adapter. Without that adapter, the core refuses execution. Results preserve exit code, success/failure, stdout/stderr hashes, output-file hashes and source fingerprints; timeouts and output-limit breaches fail instead of silently passing.

The output directory is never overwritten. Cost and currency remain null/unknown until separate financial evidence is linked; runtime duration is not a price. These process observations are not a causal tool winner or production outcome. MCP exposes the same bounded action as `experiment_run` with `contract` and `out`.

## Shared experiment export

```bash
verovo tool-value-export --file comparable-tasks.jsonl \
  --design experiment-design.json --out experiment-contract.json
```

Each input task needs `observed_at`; a `same_task_set` design also needs one non-empty `comparable_group`. The exporter emits separate task-success and task-cost observations with the source file hash. The design must explicitly declare the hypothesis, arms, metrics, comparability, window, registration, evidence grade and eligibility. Existing `tool-value` analysis remains descriptive and never becomes a causal claim merely because it is exported. The command writes a new file only.

## Boundary

Cost visibility is not profitability: revenue, gross margin and contract terms
are not in the case. A lower simulated cost is not a realized saving, and a
higher ROI is not proof that the change caused it. Those remain finance and
buyer decisions, with a verification period.
