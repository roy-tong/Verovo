# AGENTS.md — implementation metadata for coding agents

> Agent-readable metadata for Verovo. Product boundary reviewed: 2026-11-23.
> If you are a coding agent asked to implement, integrate, or verify Verovo
> claims, this file is the shortest correct path. It is normative only where it
> cites a normative document.

## 1. What you are implementing

Verovo is buyer-first: independently measure and audit AI agent charges, delivery and task economics, without collapsing financial, control, delivery and value conclusions.
Read [product-definition.md](docs/product-definition.md) before changing product scope.
The released `review` command compares explicit buyer criteria with local JSON
observations; missing evidence remains UNPROVABLE. `delivery` is an effort journal,
not the buyer acceptance command. Technical tools and POCs do not redefine the product.

The underlying telemetry tools answer two questions:

1. **What counts as one?** (operation vs attempt vs delegation)
2. **Can the result be proven?** (PASS / FAIL / UNPROVABLE)

If a task asks you to compute a metric, first resolve the grain. **Different grains
are not interchangeable** (invariant 16): 10 attempts ≠ 10 operations ≠ 10
delegations.

## 2. The four observability states

```
TRUE          confirmed to have happened
FALSE         able to judge, and confirmed not to have happened
UNKNOWN       should be judgeable, but data is missing or conflicting
UNOBSERVABLE  the current observation surface cannot see it at all
```

These four come from CORE §4. **Never** collapse UNOBSERVABLE or UNPROVABLE into FALSE. This is invariant 17. Adjacent vocabularies are separate, not extra states:

- **Verdicts** — PASS / FAIL / UNPROVABLE (`review`, the conformance pack, `docs/review-method/`): the decision about a claim. UNPROVABLE means evidence was seen but cannot be decided; never guess it into FAIL.
- **Settlement verdicts** — settled / assumed / escalated / cannot settle / out of scope (VSS-1 §0): billing-settlement outcomes, never observability states.
- **UNPROVABLE-by-surface** — a known-limit class (the measurement topology cannot observe it; a design output, not a missing implementation) defined in `standard/known-limits-unprovable-by-surface.md`; UNOBSERVABLE ≠ UNPROVABLE.

## 3. Canonical namespaces

```
verovo.dev/usage.*        operation, attempt, retry, consumption
verovo.dev/outcome.*      outcome_class, observer_grade, effect confirmation
verovo.dev/delegation.*   delegation scope, depth, lineage, cost attribution
verovo.dev/quality.*      coverage, observability, qualification
```

Do not invent parallel names. The registry is the single source of truth for managed enums: `registry/vocabularies.yaml` holds 21 vocabularies (revision 0.4.3-2). Nine of them are wired into the observation schema (`usage_context`, `validity`, `context_source`, `validity_source`, `caller_type`, `caller_identity_strength`, `observer_side`, `provenance`, `observation_type`); five also have TS unions and eight Python tuples that CI checks against the YAML. The remaining entries (e.g. `outcome_class`, `choice_mode`, `delegation_scope`) are canonical names with no generated consumer yet.

## 4. Where the enums live (do not redefine them)

`registry/vocabularies.yaml` does not generate every consumer:
- `python3 scripts/gen_vocab.py --build` rewrites the nine managed enums in `schemas/observation.schema.json` and regenerates `registry/VOCABULARIES.md`;
- `python3 scripts/gen_vocab.py --check` verifies those schema enums plus five TS unions in `sdk/src/index.ts` and eight Python tuples in `reference/collector/usage.py` against the YAML;
- other YAML entries are registry-only for now: canonical, but with no generated consumer.

CI runs `--check` and fails on any drift.
**Change the YAML, run `--build`, never hand-edit a consumer.**

The four observability states are normative in CORE §4; PASS / FAIL / UNPROVABLE verdicts come from the review/conformance vocabulary, and VSS-1 §0 owns settlement verdicts. None of these is a YAML enum — do not add or redefine them in the registry.

## 5. Core object chain

```
Task → Decision Opportunity → Selection
                                  │
                                  ▼
                              Operation ──▶ Attempt 1 ──▶ Result / Effect
                                  │             │  (retry)
                                  │             ▼
                                  │          Attempt 2 ──▶ Result / Effect
                                  │
                             Delegation ──▶ [sub-agent scope]
                                  ▼
                              Outcome
```

Rules that bite:
- `attempt_id` is Verovo's own identity; protocol ids go in `external_ids`
- Attempts are an append-only ledger; never rewrite or merge them
- Operation is a *derived* grouping; without evidence, do not group (invariant 23)
- A delegation must NOT be counted as an operation (invariant 27)

## 6. Outcome semantics (the part most implementations get wrong)

```yaml
outcome_class:   resolved | assumed_resolved | escalated | abandoned | reopened | not_outcome
observer_grade:  self_attested | affected_party | third_party_corroborated
```

- `assumed_resolved` MUST NOT be aggregated into `resolved`
- An `escalated` outcome is not a resolution, regardless of observer grade
- Settlement-grade requires `resolved` **and** `affected_party` (cross the two axes;
  marginal aggregates will over-count)

## 7. Evidence postures (COMMERCIAL 5.2)

```
provider_recognized   may enter a claim
bill_bounded          measured loss, not recognised, stays visible
exposure_only         verify against invoice, never summed into a variance
review_only           insufficient evidence; list what is missing
```

**Missing evidence lowers the posture. It never fills the gap.**
Postures are detector output, not human judgement.

## 8. When to abstain

Do not emit a claim when: the window is partial or lossy; the outcome is observed
but attribution is only correlational; the metric definition version is unregistered;
the denominator is unknown; the value is derived rather than observed; the outcome is
asserted by a party other than the observer; or the method changed mid-window.

Abstention is a valid output. See `standard/hard-questions.md` Q4.

## 9. Current implementation boundaries

- `spend-actions` projects an existing Audit Case into ready/evidence/review/unreviewed/partially_settled/settled work items. Only versioned `charge_reviews` bound to normalized source content and contract versions become ready for payment; text digests normalize LF/CRLF for cross-platform checkouts. Legacy `clean_charge_lines` alone does not clear a line. `realized` requires independently verified credit evidence before becoming settled, and partial credit produces `partially_settled`. Missing affected lines and duplicate finding IDs fail closed. `provider check` validates a local Provider Manifest and returns admit/degrade/refuse without contacting the engine or claiming production coverage. Both are Python 3.9 standard-library, read-only workflows.
- `buyer_ledger.period_case.PeriodCase` owns the read-only buyer-close projection; `CaseRepository` owns registered state paths, strict JSON reads, atomic case writes and byte-level projection snapshots. Legacy `close` init/ingest/mapping writes use the same Repository boundary. Direct projections refuse unfinished journals and detect state drift; `close status` still runs recovery first and may report unresolved recovery state, so it is not a side-effect-free read. Projections also require report revision, report/calculation/canonical fingerprints and signoff metadata to bind one revision. Revision-built reports carry their actual revision id. Selected-revision review and signoff share the root-case lock, and signing requires an exact reviewer match in `manifest.buyer.acceptance_signers`. `close.build_case` remains a compatibility facade.
- `healthcheck/verovo_healthcheck/money.py` owns display rounding for task economics, optimization, cost drivers and spend actions. Default two-decimal behavior is unchanged; BHD/IQD/JOD/KWD/LYD/OMR/TND explicitly use three minor units. Zero-decimal currency tolerances and allocation rules remain explicit separate contracts.
- `reproduce` builds and verifies in a sibling staging directory before replacing the output. Canonical finance and usage replay remain separate engines and proof scopes.
- The `verovo` wheel includes canonical proof commands. Pre-M1 `python3 -m buyer_ledger` close/reproduce commands are source-checkout tools and are not implied by `pipx install verovo`.

## 10. Before you push

```bash
cd healthcheck && python3 -m unittest discover -s tests
python3 healthcheck/verovo selftest
python3 registry/validate_metrics.py
python3 conformance/runners/run_metrics.py
python3 conformance/runners/run_outcome_audit.py
python3 conformance/runners/run_delegation.py
python3 conformance/runners/run_sse_usage.py
```

CI red means do not merge. See `CI-DISCIPLINE.md`.

## 11. What not to claim

- Do not state an attribution as a cause (invariant 14)
- Do not present a participating-network observation as market share (QUALITY 2)
- Do not sum sub-token fields (cached, reasoning) into totals — they are subsets
- Do not describe a synthetic fixture as production data
