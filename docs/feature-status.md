# Verovo: implementation status

## Agent Native service frame (2026-11-26)

- **Agent Spend Healthcheck**: accessible agent logs and usage records become cost distribution, retries, anomalies and the tasks behind inefficient spend. Subscription usage is not automatically a marginal cash bill.
- **Independent Bill Audit**: supplier bills and buyer-side records become recalculated charges, supported differences, unresolved amounts and dispute-ready evidence.
- **Agent Economics Optimization**: real tasks, costs and acceptance results become tool/model comparisons, controlled experiments and independent quality/cost review. Potential and realized savings remain separate.
- Existing gateways and runtimes execute controls; Verovo checks their scope, readback and subsequent evidence. Financial correctness, control, delivery and economic value remain independent conclusions.

## Notification delivery and resource hygiene (working tree, 2026-11-26)

- `monitor pending` lists alerts without confirmed delivery. `monitor_alerts.deliver` appends receipt-bound delivery records, leaves failed delivery pending and never rewrites the original anomaly or implies a host wake-up.
- The production share flow and six test files now close file handles explicitly; the suite runs with `ResourceWarning` promoted to error without emitting the previous warnings.
- **Verification:** 779 healthcheck tests with ResourceWarning promoted to error, 70 root tests, selftest and all 62 protocol vectors pass. An isolated wheel confirms selftest and one injected delivery receipt; empty receipts stay failed and pending. No external notification channel or production spend control is claimed.

## Measurement eligibility and control adapters (working tree, 2026-11-26)

- `economics` adds a read-only `ovo.task-result/1` projection. Fully covered cost and buyer-outcome evidence maps to `completed`; open allocation or acceptance evidence maps to `partial_result` while preserving attributed subtotals, source refs and scoped recovery actions. The projection does not alter cost, ROI, payment or realized-saving semantics.
- `monitor track` prefers explicit event or operation+attempt identity over content hash, retains same-event revisions without double counting and rejects unrevised identity conflicts. Changed revisions must be provably newer through an integer sequence or timezone-aware timestamp; older replays and unordered revisions cannot replace current state. Frozen baselines include historical unit prices; CSV `false`/`0` retries parse as false.
- `tool-value --economics-case CASE` derives task costs and acceptance directly from Canonical Task Economics; incomplete cost or unknown buyer acceptance is refused. CSV/JSONL remains a declared compatibility input and eligibility exposes `declared_csv_or_jsonl` vs `derived_canonical_task_economics`. Comparable group, currency, task type, rubric, uniqueness and evidence checks remain mandatory before statistics. MCP `tool_value_analyze` accepts the same `economics_case` input.
- `optimize-verify` validates task acceptance/cost contracts, requires identical task IDs for a sufficient `same_task_set` comparison and supports an explicit quality-score guardrail alongside success rate.
- `budget apply` executes only a named-authorized hold adapter matching the current decision budget/scope. `APPLIED` additionally requires readback with the same budget, scope and hold action, plus a timezone-aware observation no earlier than the decision. Wrong-scope and stale readbacks stay `UNPROVABLE`; subsequent-spend coverage remains separate through `budget verify`. Local fake-runtime tests do not prove production control.

## Incremental persistent monitoring and shared experiment adapters (working tree, 2026-11-25)

- Runtime discovery now selects the first supported runtime with actual files, so an empty Codex sessions directory cannot hide Claude sessions. Explicit Codex scans filter rollout filename dates and ignore non-rollout JSONL. Direct mixed-runtime tests and an isolated wheel discovery→Claude parse chain pass (`V1-RUNTIME-DISCOVERY-01`, synthetic fixture only).

- `experiment run` executes pre-registered local control/treatment argv for each task in fresh isolated processes, with named local-only authorization, timeout, output caps, success conditions and output hashes. The offline core requires an injected process adapter; CLI/MCP explicitly use the separately packaged `verovo_local_runner`. Cost stays unknown rather than inferred from runtime. CLI, Skill and MCP `experiment_run` share the implementation; no external actions or causal winner are claimed.
- `human-baseline` compares matching human/agent task arms using direct cost plus declared human-minute cost. It requires the same task group, acceptance rubric, currency and quality requirements; quality drops and zero-success denominators stay inconclusive. CLI, Skill and MCP action `human_baseline` share descriptive, unreconciled and non-causal output.
- `optimize-verify` reviews before/after comparable tasks with cost, quality and sample contracts. Reversed or overlapping windows are rejected; `keep` additionally requires a `change_effective_at` after baseline and no later than final observations. Missing effective time stays `inconclusive`. It recommends `keep/rollback/inconclusive` without causal attribution, realized-saving claims or automatic rollback.
- `optimize-loop create/tick/status/pause/resume` persists a named local-only optimization contract, task-file paths, interval and registration fingerprint. Each explicit due tick reuses `optimize-verify`, records source hashes and detects source changes; quality breaches produce `rollback_required`, never an applied rollback. The scheduler is an external cron/host, not a daemon, and no model route, external runtime or supplier bill is contacted. CLI, Skill and MCP `optimization_loop_*` actions share the implementation. Verification: 761 healthcheck tests, 69 root tests, selftest and protocol vectors pass; the isolated wheel completes two ticks and MCP reads the same state (`V7-OPTIMIZATION-LOOP-01`, synthetic only).
- `budget check` evaluates a `verovo.spend-budget/1` contract against monitor state and emits advisory `allow/warn/hold/review` with scope, half-open period, separate currencies and explicit `execution_applied=false`.
- `budget resume` requires named confirmation, a current non-hold/non-review budget and a running runtime readback. It preserves observed spend, never resets the budget and appends conflict-safe local recovery history; outcome transitions append new records and only identical source+outcome repeats deduplicate. Current hold/review fails, missing running evidence stays UNPROVABLE, and no external runtime is contacted. CLI, Skill and MCP action `budget_resume` share the implementation.
- `budget verify` reconciles the decision with a `verovo.spend-control-receipt/1`: matching budget/action/scope, an explicit stopped runtime readback and a complete post-stop observation window. Positive subsequent charges produce FAIL and cannot be hidden by credits; incomplete coverage, unconfirmed stop or ambiguous event order stays UNPROVABLE. CLI, Skill and MCP action `budget_verify` share the local read-only evaluator. PASS proves local receipt/monitor consistency, not external-record authenticity, actual runtime contact or production control coverage.
- `monitor series` projects persistent records into UTC minute/hour/day buckets with half-open windows and task/tool/model scope. Amount, record and retry counts stay per currency, unknown currency is separate and no cross-currency total is produced. CLI, Skill and MCP action `monitor_series` share the read-only projection.
- `monitor track` persists deduplicated records, an explicitly frozen historical baseline and anomaly first/last-seen history. `monitor freeze-baseline` rebuilds the baseline only on request; late records remain pending and damaged state is never overwritten.
- `monitor notify` appends active anomalies to a deduplicated local JSONL outbox. Alert identity is stable across repeated tracks; inactive anomalies are not emitted. Every record remains `pending_local_delivery` with external delivery, host wake-up and spend control explicitly unperformed. Damaged outbox JSONL fails closed. CLI, Skill and MCP (`verovo_economics_ops` action `monitor_notify`) share the same emitter.
- `monitor resolution` projects one alert into `ovo.problem-resolution/1`: orphan calls request task mappings, price drift requests applicable rules, and retry/spend anomalies remain business decisions. No pause, retry or supplier disposition is executed. CLI, Skill and MCP action `alert_resolution` share the projection.
- `tool-value` now reports per-arm Wilson 95% success-rate intervals and a Newcombe 95% interval for the treatment-minus-control difference, including whether it covers zero. Empty arms keep null intervals, small samples select no winner, and population selection bias remains unassessed. These are descriptive intervals, not causal or procurement verdicts.
- `monitor forecast` estimates remaining task cost from same-task/currency observations in a half-open period and explicit progress evidence. Zero progress or no matching records keeps estimates null. The linear projection is `estimate_not_cap`, never a final bill or applied budget limit. CLI, Skill and MCP action `monitor_forecast` share the evaluator.
- `tool_value_analyze` MCP action exposes the same cost/success comparison and Wilson/Newcombe intervals as `verovo tool-value`, without writing an experiment contract or selecting a winner.
- Askovo/Growovo/Verovo now have separate read-only `ovo.experiment-contract/1` exporters. Verovo `tool-value-export` emits task-level success and cost observations with source hashes and no automatic causal upgrade.
- Verification: 750 healthcheck tests and 69 root tests pass, alongside selftest, metric registry and protocol vectors. The rebuilt wheel passes an isolated install and synthetic `monitor track → budget check → budget verify` chain (`V3-CONTROL-RECEIPT-01`, evidence under `runs/V3-CONTROL-RECEIPT-01-evidence/`); no external spending, runtime control or payment action occurs.

## Business-first close report and spend monitoring (v0.19.0, 2026-11-25)

- Buyer-close reports lead with the payment/renewal recommendation, invoice and open difference, accepted tasks, cost per verified success and whether value can be judged. Root-cause groups replace raw item IDs in the main answer; axes, verdict codes, line-level reconciliation and replay metadata stay in the appendix. Open items never become payment authorization.
- Unresolved items are ordered by amount importance (same currency only), due date, evidence confidence, persistent loss and systemic-error signal. Same-line findings collapse to one exposure; gross and de-duplicated exposure are both shown, and different/unknown currencies stay out of the sum.
- `verovo monitor` builds a local median/MAD baseline per tool/model/currency and surfaces spend spikes, unit-price drift, retry storms and orphan calls. It is advisory-only: no gateway, no auto-block, no invoice total.
- `verovo tool-value` compares comparable task groups on success rate, cost per task and cost per verified success, with optional value-per-success. Single-arm or call-share inputs never produce a causal claim.
- **Verification:** 445 Buyer Ledger, 673 healthcheck and 69 root tests pass; `verovo selftest` passes. Economics still never turns unknown value into zero or ROI.


## Native real-task case gate (2026-11-24)

- `verovo case list|show|incidents|index|validate|replay|verify` delegates to the shared `ovo-acceptance` runtime. The default registry supplies the family workspace root, so commands also work from this repository. `OVO_ACCEPTANCE_HOME` overrides the shared runtime location and `OVO_CASE_REGISTRY` overrides the registry for isolated tests; `OVO_CASE_ROOT` explicitly overrides the evidence root.
- `task_rerun` and `evidence_only` remain separate. External-write cases are evidence-only and never resend, pay or sign. The runtime and 24-case registry passed validation; all 24 cases passed, with no blocked cases. V2/V4/V5 scoped journeys are registered. See `../../ai输出/ovo-真实任务资产库/README.md` and `../../ai输出/ovo-真实任务资产库/GOLDEN_JOURNEYS.md`.

## Buyer-close original-scenario fixes (2026-11-24)

- English `to` in an OCR period preserves its stated exclusive boundary instead of adding a day. Inclusive display ranges can still convert explicitly; date-only values record `calendar_date` and `date_only_no_timezone_conversion`.
- `close revise --source FILE --role outcomes` adds a named source. Bundle revisions reject undeclared tables/documents instead of silently ignoring them. `close review --revision rN` reviews the selected revision under the same root-case lock used by signoff, then synchronizes the report fingerprint used by signoff.
- Signoff requires an exact reviewer match in `canonical manifest.buyer.acceptance_signers`, with `AUTH_REQUIRED` otherwise. `signoff_revision` and `signoff_state` are separate. Reviewed reports render the actual signoff record instead of a conflicting status.
- Booking provenance verifies the reviewer/time, original document hash, bill-export hash and declared bills source. A reviewed booked OCR document no longer creates a false `invoice_draft`; it still is not independent buyer evidence.
- Final reports project task-level costs and root-cause groups. V2 has 20 task rows and 3 groups over 2001 open items. Missing `outcomes.value` is named explicitly instead of asking for an already supplied file; unresolved amounts appear in the conclusion area.
- Current verification: **440 buyer-ledger tests plus healthcheck, root, Lab and protocol suites pass**. V2/V4/V5 use frozen task fixtures; synthetic signoff is not payment authorization or a real buyer outcome.

**Current install release, 2026-11-25 · [v0.19.0](../CHANGELOG.md).** Buyer-close reports now lead with the payment/renewal decision and business measures; unresolved exposure is amount-ordered and line-deduplicated. `monitor` adds advisory spend observation and `tool-value` adds controlled cost-per-success comparison. Canonical verdicts, signed revisions and proof boundaries remain unchanged.

Earlier baseline reviewed 2026-11-04 against the **v0.13.0 release**. Available means local implementation, not production validation on every platform.

## Prove Anything period proof (2026-11-22)

`verovo proof export --bundle PERIOD --out NEW_DIR --by NAME` projects `canonical_finance.run` into a read-only Proof Bundle without modifying source inputs. Rule repricing, invoice reconciliation, vendor reconciliation and buyer verification are P1/P2/P1/P1 checks respectively; verified delivery acceptance is P4. Independent professional review is a separate P3 check and remains UNCHECKED unless independently supplied. UNKNOWN/UNPROVABLE become INDETERMINATE and completion axes never become a blanket PASS.

`verovo proof replay NEW_DIR --bundle PERIOD` recomputes every input hash and the canonical output. Changed sources return EXPIRED, output drift returns FAIL, and a matching replay returns PASS without modifying proof bytes. The semantic fingerprint excludes only the volatile `intake_report.generated_at` clock; every financial field remains hashed. Five dedicated tests, 20 repeated proof runs and the full healthcheck/conformance suite pass on the synthetic canonical fixture; a real customer close and buyer disposition remain unverified.

## Canonical finance: completion axes (V1)

The canonical JSON now exposes four completion states separately. These states describe whether an axis is closed, not whether its business result is favourable.

| Field | Meaning of `complete` | When it is not complete |
|---|---|---|
| `finance_status` | Known financial records have no open financial/cost-allocation gap, and the Task Economics cost status is complete | `partial` for an open amount/currency/allocation/reconciliation gap; `blocked` when no economics case can be built |
| `verification_status` | Applicable task outcomes have decisive buyer-procedural verification; a verified failure is still decisively verified | `partial` for missing, conflicting or unqualified outcome evidence; `not_applicable` only when no outcomes source was supplied |
| `delivery_status` | Every task has decisive `Delivery Accepted` PASS or FAIL evidence and no submitted delivery row remains unresolved | `partial` for UNKNOWN/UNPROVABLE acceptance evidence or uncovered tasks; `not_applicable` only when no outcomes source was supplied |
| `economics_status` | Applicable outcome economics have complete cost, success-denominator and submitted value evidence | `partial` while those inputs remain open; `not_applicable` only when no outcomes source was supplied |

The legacy top-level `status` is only a compatibility summary of these completion axes. A finance-only period with no outcomes source can therefore be `finance_status=complete` and `status=complete` while `verification_status`, `delivery_status` and `economics_status` are `not_applicable`. Its cost subtotal/total can still be calculated; Cost per Verified Success remains null with an explicit unknown denominator. An outcomes source that is present but empty does **not** hide the gap: the applicable axes remain partial.

`complete` does not mean PASS, accepted delivery, a non-null ratio, ROI, or a business success claim. PASS/FAIL/UNKNOWN/UNPROVABLE remain evidence verdicts and are not collapsed into these completion states.

## Evidence conclusions and reconciliation naming (V2)

The canonical evidence output now distinguishes the following bases:

- **`Rule Repriced`** — `basis=approved_rule_recalculation`. It compares available bill/usage amounts with the approved rule. Any available repricing difference makes the charge-level conclusion FAIL; a rule match is not ledger reconciliation.
- **`Invoice Reconciled`** — `basis=distinct_source_ledger_comparison`. It compares two distinct bill/usage source records. Equal records PASS, different amounts FAIL, missing amount/currency remains UNKNOWN, and the absence of a second distinct record is UNPROVABLE. `reconciled_charges` now comes only from this cross-record comparison, never from a bill-only rule match.
- **`Invoice Matched`** — retained as a compatibility alias of `Rule Repriced`, with `alias_of=Rule Repriced` and the explicit repricing basis. It must not be read as cross-source reconciliation.
- **`Vendor Reconciled`** — still compares explicitly vendor-controlled records and never establishes buyer independence.
- **`Buyer Verified`** — retains PASS/FAIL/UNKNOWN/UNPROVABLE semantics, but PASS now requires complete, internally consistent buyer provenance plus rule agreement. A PASS on buyer usage does not close or erase a different vendor-bill amount; the invoice reconciliation and finance axes remain open.
- **`Delivery Accepted`** — retains PASS for verified accepted delivery, FAIL for verified non-acceptance, UNKNOWN for missing acceptance fields and UNPROVABLE for unqualified evidence.

### Independence evidence: assertion versus qualification

Intake preserves `source_party`, `source_affiliation`, `control_domain`, `collector_party`, `collection_method`, `independence`, `confirmation_process` and the observed manifest `source_path`. The input aliases `source_association` and `collected_by` are normalized to the canonical names. Conclusions also expose `independence_assertion` and `independence_basis`.

`independence=buyer_independent` is only an assertion. Buyer qualification fails closed when required provenance is missing, source/control/collector domains conflict, the collection or confirmation process is self-declared/unknown, or an explicit `vendor` / `vendor_export` marker occurs in the source reference, physical source path, collection path or confirmation process. Repeated vendor exports and OCR/invoice2data drafts do not become buyer evidence by relabelling their source party.

This is a deterministic **procedural metadata consistency check**, not cryptographic proof, external identity verification, or proof that the declared reviewer actually acted independently. The vendor-path guard only catches explicit known markers; a disguised or undocumented control path can evade string matching. Legacy records that carried only the old three self-declared fields no longer qualify as buyer evidence. The existing verdict vocabulary and `Invoice Matched` output key remain for compatibility.

## Spend actions and Provider admission (v0.17.x)

`verovo spend-actions --case AUDIT_CASE` reads an existing Audit Case and its invoice sources. It emits `verovo.spend-action-list/0.1` with six actions: `ready_for_payment`, `evidence_required`, `review_required`, `unreviewed`, `partially_settled` and `settled`. Only versioned `charge_reviews` can make a line ready: invoice path, line ID, source SHA-256, contract version, reviewer and review time must match. Text digests normalize LF/CRLF for cross-platform checkouts. Legacy `clean_charge_lines` alone does not clear a line, and changed content invalidates the previous review. `realized` no longer implies settled: a credit must be applied and match the finding amount, currency and charge identity. Partial credits produce `partially_settled`; missing, unapplied or mismatched evidence remains `review_required`. Missing affected lines and duplicate finding IDs fail closed. Amounts use the shared Money Contract, including three minor units for BHD/IQD/JOD/KWD/LYD/OMR/TND, and invoice-line and case-level amounts are reported separately. The command is a local projection only: it does not execute or authorize payment, and the buyer's actual disposition remains a separate record.

`verovo provider check --manifest MANIFEST` validates `verovo.provider-manifest/0.1` and emits local `admit`, `degrade` or `refuse` admission. Required check failure/unknown and retained/unknown cleanup refuse; optional check failure/unknown degrades. Process engines require an executable SHA-256. Resource limits and coverage counts are strict integers, raw snapshot references are digest-bound, and output always states `live_engine_contacted=false`. This is an adapter contract, not an OTel/LiteLLM/Langfuse live integration, a control effectiveness claim or proof of complete production coverage.

## Other implemented feature status

| Feature | Scope | Status |
|---|---|---|
| V01–V02, V05 | Usage adapters, supported bill recounts and charge review | Available for documented formats; no universal billing connector. `verovo intake` assembles a case from a manifest plus generic usage rows, bill CSV and outcome CSV, and records unmatched/duplicate charges instead of summing them |
| V03–V04 | Invoice extraction and normalization | Optional `invoice-intake` via invoice2data produces source-linked, unreviewed drafts (documents only, never booked). Intake reconciles bill and usage charges by `charge_id` and excludes currency mismatches; tax/credit/refund/FX normalization remains planned |
| V06–V07 | Task/run linkage and direct/shared allocation | Available with explicit IDs and versioned rules |
| V08–V10 | Business outcomes, verification flags and task costs | Outcome CSV ingestion requires a literal `verified` value; unverified or procedurally unqualified outcomes are kept but cannot count as success or ROI. Local case ingestion available; source truth requires review |
| Buyer Ledger OCR candidate | Visible text/screenshot → draft → field review → existing close | Local text and caller-supplied OCR lines are portable; real screenshot OCR uses the independent macOS Vision adapter and is explicitly unavailable on non-darwin. Missing fields stay null, raw text/source hashes and field evidence are retained, and only explicit human `book` exports reviewed values. Synthetic tests cover the close path, bad OCR, duplicates, line-total mismatch and mixed currencies; real OCR/layout quality remains unvalidated |
| ROI | Verified value / cost and cost per verified success | Available; incomplete input remains a coverage limitation. `verovo drivers` separates recorded from attributable cost and keeps unattributed cost visible |
| Optimization / assurance | Cost-driven workflow changes and risk pricing | `verovo optimize` proposes vendor/task/retry/allocation tests with explicit scenario arithmetic and a verification plan; every item needs a human and is never auto-applied. No causal savings or insurance claims |

Build: reconciliation semantics, task identity, allocation, outcome rules and economics. Reuse: existing telemetry/billing adapters; optional Docling/invoice2data and DuckDB remain integration candidates, not shipped dependencies. Optional invoice2data performs extraction into a non-booked draft, and period intake reuses that draft without booking it; no vendored fork or mandatory core dependency.

The read-only/local `verovo_invoice_intake` MCP tool returns the same unreviewed, unbooked draft as the CLI; it never updates the economics case. `verovo_period_intake` returns the assembled case plus the intake report (open items, reconciliation, currency mismatches) and writes nothing.

Reliability behavior: largest-remainder allocation preserves cents and zero weights; explicit `charge_id` deduplicates usage/bill representations; duplicate outcome references do not multiply value; conflicting verified task states abstain; ROI requires a source and literal boolean verification. Zero verified value produces -100% ROI. Confirmed loss totals exclude estimates and duplicates.

## Verification in this working tree and remaining boundaries

- PASS: 663 healthcheck unit tests via healthcheck discovery, 69 root tests, 440 Buyer Ledger tests, 91 Lab tests, and `python3 healthcheck/verovo selftest`.
- PASS: metric vectors 22/22, outcome-audit vectors 15/15, delegation vectors 23/23, SSE vectors 2/2; external fixture runners, receipt/correlation vectors, aggregate-join, replay-digest, commerce-retry and conformance-pack selftest. Metric registry and generated vocabulary checks also pass.
- The canonical fixture and new adversarial cases are synthetic. No real customer close, supplier dataset, production identity flow, external signature, cryptographic attestation or OCR accuracy was validated.
- Supported canonical scope remains known CSV/JSONL, one vendor/account/currency, approved per-unit pricing and approved allocation drivers. Unsupported formats and cross-scope input fail closed. General contract parsing, arbitrary invoice layouts, tax/credit/refund/FX handling, external business-system confirmation and real-world independence remain outside the verified boundary.
