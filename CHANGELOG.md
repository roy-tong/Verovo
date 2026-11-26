## 0.20.1rc4 — 2026-11-26

### Fixed

- Distribution metadata now reads `verovo_healthcheck.__version__`, so an installed wheel and `verovo --version` report the same prerelease instead of retaining the previous stable runtime version.

## 0.20.1rc3 — 2026-11-26

### Fixed

- Monitoring accepts only provably newer integer-sequence or timezone-aware timestamp revisions; historical replay and unordered revisions cannot overwrite current events.
- `tool-value --economics-case` and MCP `economics_case` derive costs and buyer acceptance from Canonical Task Economics. Incomplete coverage is refused and declared CSV/JSONL inputs carry a separate trust label.
- Budget `APPLIED` now binds runtime readback to the requested budget, scope and hold action, and requires an observation no earlier than the control decision. Wrong-scope and stale readbacks remain `UNPROVABLE`.

## 0.20.1rc2 — 2026-11-26

### Fixed

- Closed the production share-flow file handle and six test-file handles, eliminating the previously observed ResourceWarning instances without suppressing warnings.
- Alert delivery requires a non-empty adapter receipt; empty receipts remain failed and pending instead of being marked delivered.
- Monitoring uses explicit event identity and revisions before content-hash fallback, freezes unit-price baselines and parses CSV retry booleans correctly.
- Tool-value and optimization comparisons enforce task/currency/acceptance/cost eligibility before statistics and support multidimensional quality guardrails.

### Added

- `monitor pending` lists alerts without confirmed delivery. `monitor_alerts.deliver` appends receipt-bound delivery records and leaves failed delivery pending; the original anomaly remains unchanged.
- `budget apply` provides a named-authorized hold command adapter with runtime readback and a separate receipt for subsequent-spend verification.
- `economics` now includes an `ovo.task-result/1` projection. Complete cost and buyer-outcome coverage produces `completed`; attributed subtotals with open allocation or acceptance evidence produce `partial_result`, with scoped gaps and a rerun capability rather than an upgraded ROI or savings claim.

## v0.20.0 - 2026-11-26

### Added

- Persistent incremental spend monitoring with frozen baselines, UTC minute/hour/day series, task forecasts, deduplicated local alert outboxes and shared problem-resolution projections.
- Advisory budget contracts, stop-readback/subsequent-spend verification and named recovery history. Controlled local control/treatment process runs use an explicit separately packaged adapter; costs remain unknown when not connected.
- Comparable tool-value analysis with Wilson/Newcombe intervals, shared experiment export, equal-quality human baselines and post-optimization reviews. Persistent `optimize-loop` adds explicit due ticks, source hashes, source-change detection and pause/resume without external configuration or applied rollback.

### Fixed

- Reversed/overlapping optimization windows and missing change-effective times cannot produce a `keep` conclusion. Budget recovery no longer hides FAIL→PASS transitions or resets observed spend.
- Runtime discovery selects a supported runtime with actual files, so an empty Codex directory no longer masks Claude sessions.

### Tests

- 763 healthcheck and 69 root tests pass, alongside selftest, metric registry and all protocol vectors. Rebuilt wheels pass isolated monitoring, control, experiment, human-baseline, optimization-loop and discovery chains. Synthetic observations do not prove production control, supplier reconciliation, causality or realized savings.

## v0.19.0 - 2026-11-25

### Added

- `verovo monitor` ingests local usage CSV/JSONL, builds a per-tool/model median/MAD baseline and surfaces spend spikes, unit-price drift, retry storms and orphan calls. It is advisory-only: no gateway, no auto-block, no invoice total, and no cross-currency summation.
- `verovo tool-value` compares comparable task groups on success rate, cost per task and cost per success, with optional value-per-success. Single-arm or call-share inputs never produce a causal claim.

### Changed

- Buyer-close reports and terminal summaries now lead with the payment/renewal recommendation, invoice and open difference, accepted tasks, cost per verified success and whether value can be judged. Root-cause groups replace raw item IDs in the main answer; axes, verdict codes, line-level reconciliation and replay metadata remain in the appendix. Open items never become payment authorization, and unknown economics never become zero or ROI.
- Unresolved audit items are ordered by amount importance (same currency only), payment due date, evidence confidence, persistent loss and systemic-error signal. Findings on the same invoice line collapse to one exposure; the report shows both gross and de-duplicated exposure and keeps different/unknown currencies out of the sum instead of treating them as zero.

### Tests

- 3 priority regressions and 10 monitor/tool-value tests pass, together with 443 Buyer Ledger, 673 healthcheck and 69 root tests; selftest remains PASS. New coverage includes business-first report ordering and the combined risk-exposure line.

## v0.18.0 - 2026-11-24

### Added

- Buyer-close revisions accept named additional sources, reject undeclared bundle materials, and support `close review --revision rN` with report-fingerprint synchronization.
- Final reports include task-level economics, unresolved priority amounts, root-cause groups and actual signoff state. OCR booking provenance binds reviewer, original document, bill export and declared source hashes.
- `verovo case` delegates to the shared real-task runtime; the standalone conformance entry and packaged command share one implementation.

### Fixed

- English `to` in OCR periods preserves the stated exclusive boundary; date-only values retain explicit calendar-date semantics.
- Revision review and signoff now use the same root-case lock, and signoff requires an exact reviewer match in `manifest.buyer.acceptance_signers`; a name embedded in a longer string is not authority.
- `signoff_revision` and `signoff_state` remain separate, and reviewed booked OCR documents no longer create false invoice-draft gaps.

### Tests

- 440 Buyer Ledger, 663 healthcheck, 69 root and 91 Lab tests pass, together with selftest, protocol vectors, registry/vocabulary/rule-card checks and package build checks. New regressions cover revision/root locking, signer exact matching, named source addition, OCR period boundaries and task-economics report projections. Synthetic signoff remains neither payment authorization nor a real buyer outcome.

## v0.17.2 - 2026-11-23

### Changed

- Spend Actions now requires versioned `charge_reviews` for `ready_for_payment`: invoice path, line id, source SHA-256, contract version, reviewer and review time must all match. Text digests normalize LF/CRLF for cross-platform checkouts. Legacy `clean_charge_lines` alone no longer clears a line, and changed content invalidates the previous review.
- `realized` no longer implies `settled`. Applied credit evidence must match the finding amount, currency and charge identity. Added `partially_settled`, `resolution_claimed`, `credit_issued` and `settlement_mismatch` handling, with `settlement_verified` only for a complete match.
- Invoice-line and case-level amounts are reported separately. Missing affected lines and duplicate finding IDs fail closed.
- Added explicit three-minor-unit currency support for BHD, IQD, JOD, KWD, LYD, OMR and TND in the shared Money Contract; existing two-decimal behavior is unchanged.

### Tests

- Added 12 adversarial tests for cross-invoice ID collisions, stale reviews, CRLF normalization, missing affected lines, missing/unapplied/partial credits, KWD precision, case-level amount separation and duplicate finding IDs. 659 healthcheck tests and 69 root tests pass.

## v0.17.1 - 2026-11-23

### Changed

- Replaced the stale package introduction with the current independent agent measurement and audit positioning, working install path, spend-action and Provider Manifest commands, task economics, evidence boundaries and development checks. No financial behavior or API contract changed.

### Tests

- The v0.17.0 behavior remains covered by 647 healthcheck tests, 66 root tests, 430 Buyer Ledger tests and 91 Lab tests. Package installation, both new CLI workflows, spec-drift and SEO checks pass.

## v0.17.0 - 2026-11-23

### Added

- **`verovo spend-actions --case AUDIT_CASE`** projects an existing Audit Case into a line-level buyer worklist with `ready_for_payment`, `evidence_required`, `review_required`, `unreviewed` and `settled` actions, owner roles, reasons, finding links and per-currency amount coverage. Only manifest-confirmed clean lines can become ready for payment; the command never executes or authorizes payment.
- **`verovo provider check --manifest MANIFEST`** validates a REA-inspired `verovo.provider-manifest/0.1` document and computes local `admit`, `degrade` or `refuse` admission from explicit readiness checks, engine identity, resource limits, coverage and cleanup state. It never claims live engine contact or production completeness.
- Added Provider Manifest and Spend Action List JSON Schemas, a digest-checked synthetic Provider example, and documentation for both workflows.

### Tests

- 647 healthcheck tests, 66 root tests, 430 Buyer Ledger tests and 91 Lab tests pass locally. Both new CLI workflows pass end to end; the Audit golden case remains valid, and generated vocabularies stay in sync. These are synthetic and conformance checks, not a real customer disposition, live provider integration or runtime spending-control verification.

## v0.16.2 - 2026-11-23

### Changed

- Legacy close init/ingest/mapping writes now share the `CaseRepository` atomic-write boundary for job, scan, mapping, canonical tables, rule maps and manifests.

### Fixed

- Direct `PeriodCase` projections use byte-level state snapshots, reject unfinished journals and detect changes during projection instead of returning a mid-transaction signed view. `close status` still performs recovery first and preserves recovery diagnostics.
- Signoff projection now requires matching report revision and report/calculation/canonical fingerprints. Revision-built reports carry their actual revision id instead of always using r1, so an old signoff cannot endorse a new report.

### Tests

- 430 Buyer Ledger tests pass, including signoff-checkpoint refusal, revision/fingerprint binding, snapshot drift, repository write routing and the existing revision/recovery suites.

## v0.16.1 - 2026-11-23

Compatible architecture hardening for repeatable buyer-close work.

### Changed

- `PeriodCase` now owns the read-only buyer-close projection and `CaseRepository` owns registered state paths, strict JSON reads and atomic case writes. `close.build_case` remains compatible; malformed existing state fails loudly instead of looking like an absent artifact.
- Period Intake precomputes task/run/outcome coverage sets instead of rescanning every source row per task. The 4,000-row synthetic benchmark falls from about 0.66 s to 0.071 s on the review machine with identical coverage output.
- Money Contract v2 routes task economics, optimization and cost-driver display through the same HALF_UP rule. Booking, allocation and zero-decimal currency tolerance remain separate explicit contracts.

### Fixed

- `reproduce build` now writes and verifies a complete sibling staging directory before replacing the output. A failed build leaves no partial new bundle, and a failed overwrite preserves the previous bundle byte for byte.
- Release smoke now exports and replays a canonical period proof from the installed wheel. Documentation distinguishes those installed commands from source-checkout pre-M1 Buyer Ledger workflows.

### Tests

- 59 root tests, 424 Buyer Ledger tests and 621 healthcheck tests pass locally; selftest, 21 vocabularies, metric registry and all 62 protocol vectors pass. SEO reports zero errors and warnings. These are synthetic/conformance checks, not a real customer close or supplier acceptance.

## v0.16.0 - 2026-11-22

### Added

- **`python3 -m buyer_ledger reproduce proof BUNDLE --out NEW_DIR --by NAME`** projects a replay bundle into a read-only Prove Anything package. It writes whitelisted manifest/replay projections without source paths or row-level subjects, preserves the source verdict, marks external results UNCHECKED and replay UNAVAILABLE when private inputs are not copied, and never mutates the source or performs external actions.

- **`verovo proof export --bundle PERIOD --out NEW_DIR --by NAME`** exports canonical finance as a Prove Anything bundle with separate P1/P2/P3/P4 checks. **`verovo proof replay NEW_DIR --bundle PERIOD`** rechecks every source hash and recomputes canonical output; changed inputs return EXPIRED, output drift returns FAIL, and neither command mutates source or proof directories. The semantic replay fingerprint excludes only the volatile `intake_report.generated_at` clock; every financial field remains hashed, and a business-value mutation regression proves drift is still rejected.

## v0.15.4 - 2026-11-20


Reading a Growovo handoff as evidence, not as a booking.

### Added

- **`handoff normalize-growovo <handoff.json> --out <dir>`** rewrites a
  Growovo handoff into the ledger's own Period Intake files — `manifest.json`,
  `tasks.csv`, `runs.jsonl`, `outcomes.csv` — so a period can cite what the
  other product recorded without importing its vocabulary. The package is
  verified against its `integrity_sha256` before anything is written, and a
  mismatch exits 2 leaving the output directory untouched. Committed operations
  become tasks and runs; dry runs are excluded and counted in `excluded`;
  product events land in `outcomes.csv` as `status=source_declared` with
  `verified=false`, because Growovo observed its own action, not the buyer's
  outcome. No `usage` table is derived: a Growovo send has no monetary cost,
  and a zero-cost usage row would become a fake "unknown cost" in Task
  Economics. The manifest records `task_bindings_source` (`payload` for
  packages that carry bindings, `derived` otherwise),
  `receipt_verified_by_verovo: false`,
  `business_outcome_observed_by_verovo: false` and
  `terminal_state: DELIVERED_WITH_GAP` when events remain unverified.
  `buyer-ledger/tests/test_handoff_normalize.py` covers 7 cases: hash
  mismatch, dry-run exclusion, unverified outcomes, no usage table, output
  directory must be empty.

## v0.15.3 - 2026-11-20

Handoff premises, the shared error families, and a readable report surface.
No change to booking amounts or verification verdicts.

### Added

- **the Growovo handoff now carries premises, and Verovo keeps them verbatim**:
  a `grow handoff-verovo` package may include a `premises` block (source
  objects and versions, scope, does-not-cover, assumptions, limits, manual
  review state, evidence refs). `handoff import-growovo` validates that it is
  an object, stores it as `source_premises` in the evidence record, sets
  `premises_do_not_upgrade_grade: true`, and adds a fourth open item asking
  the buyer to re-check the premises against buyer-controlled sources. A
  handoff without premises still imports (the value is `null`). Premises are
  inside the handoff integrity fingerprint, so tampering with them refuses the
  import as a whole. `buyer-ledger/tests/test_growovo_handoff.py` covers
  preservation, the no-upgrade rule, old packages and a non-object refusal.
- **`buyer_ledger/error_semantics.py` maps the frozen C-01 codes to the seven
  shared families** (NEEDS_USER_INPUT, UNSUPPORTED_CAPABILITY, INVALID_INPUT,
  PERMISSION_DENIED, SAFETY_BLOCKED, STALE_OR_CONFLICT, CONTRACT_ERROR), with
  `describe()` returning what is missing, the impact and the legal next
  action. The module refuses to import if `close.py` gains or loses a code
  without a family. It is read-only: `close.py` codes, verdicts and booking
  statuses are untouched.
  `buyer-ledger/tests/test_error_semantics.py` freezes the coverage and the
  spot-checked families.
- **bill drafts check line arithmetic against the printed amount**: a draft's
  line items are re-computed at review time and at booking time. Quantity ×
  unit price must match the printed line amount within
  `max(one smallest printed unit, 1% of |line amount|)`; the old tolerance
  (smallest unit × `max(1, |amount|)`) accepted any row in zero-decimal
  currencies such as JPY and KRW. A line whose quantity, unit price or amount
  is not a number cannot be booked even when it was "accepted with a note" —
  `book` raises `REVIEW_REQUIRED` and names the row, so the reviewer corrects
  the number instead of waiving it. A candidate that only carries a pasted
  total keeps working. Period end dates written as an inclusive day are
  converted to a half-open `[start, end)` interval, and the draft records
  `period_end_converted_to_exclusive`, a `period_end_note`, and a ×0.9
  confidence so the reviewer sees the conversion.
  `buyer-ledger/tests/test_bill_candidate_line_trust.py` covers the currency
  tolerance, unparseable rows, the note-accepted row, the clean path and the
  inclusive-end conversion.

### Changed

- **the close report says what is unknown instead of printing dashes**: the
  report body gains `amount_layers` (confirmed amounts, unattributed cost,
  missing records, what cannot be computed and why, what input is needed) and
  `open_item_groups` (one row per root cause with an affected count, two
  sample item ids and one action). The Markdown keeps the per-item table and
  adds the grouped table above it, so a 20-row gap is one request to the
  supplier, not 20. The Task Economics section names the missing source and
  the responsible input instead of leaving blank fields. Review and signoff
  wording now distinguishes "reviewed", "signed" and "all differences
  resolved"; a signed report states the unresolved count, and a missing
  output snapshot reads "未生成" rather than an empty fingerprint. Verdicts,
  amounts and booking behaviour are unchanged.

### Tests

- buyer-ledger: 384 tests OK (10 new in
  `test_bill_candidate_line_trust.py`).
- healthcheck: 609 tests OK; `verovo selftest` PASS; metric registry VALID
  (18 metrics); conformance 22/22 metrics, 15/15 outcome, 23/23 delegation,
  2/2 SSE vectors PASS; vocab 21 in sync.

### Notes

- `INVALID_INPUT` has no C-01 code: Verovo reports bad input as
  `INPUT_REQUIRED`. The family exists in the shared vocabulary so the three
  products speak the same words, not so Verovo must invent a code.

## v0.15.2 - 2026-11-20

V-09b from the 2026-11-20 main review.

### Fixed

- **V-09b a booked bill must still be the bill that was reviewed**: a candidate
  records the source file's SHA-256 at creation. `review_field` and `book` now
  re-verify the source file and the OCR artefacts before using the earlier
  review: the raw source hash, the `raw_text_sha256` in `ocr.txt`, and the OCR
  payload's schema, lines and reconstructed text must all still match. Any
  change raises `STALE_SOURCE` and the old review can no longer be carried into
  a period bundle; `book` checks again immediately before copying the source.
  `buyer-ledger/tests/test_bill_candidate_integrity.py` covers a swapped
  source, edited OCR text, a tampered OCR payload and the clean path.
- **candidate locking fails closed where `fcntl` is unavailable**: the
  candidate store lock used to continue without a lock when `fcntl` could not
  be imported. It now raises `UNSUPPORTED_CAPABILITY` instead of pretending to
  hold a cross-process lock. `UNSUPPORTED_CAPABILITY` is registered in the
  close error-code contract.

### Tests

- buyer-ledger: 363 tests OK (9 new in `test_bill_candidate_integrity.py`).
- healthcheck: 609 tests OK; `verovo selftest` PASS; metric registry VALID
  (18 metrics); conformance 22/22 metrics, 15/15 outcome, 23/23 delegation,
  2/2 SSE vectors PASS.

## v0.15.1 - 2026-11-20

Release-notes repair for v0.15.0. No product behaviour changes.

### Fixed

- **v0.15.0 shipped without release notes**: the tag passed `release-guard`,
  but `github-release` failed because `docs/RELEASE-v0.15.0.md` was not in the
  tagged commit, and the workflow refuses the old silent fallback. No GitHub
  Release was created for v0.15.0; the tag is left in place. v0.15.1 carries
  `docs/RELEASE-v0.15.1.md` and the same product changes.

## v0.15.0 - 2026-11-20

V-21 / V-02 from the 2026-11-19/20 full-scenario end-to-end test.

### Fixed

- **V-21 the accepting party is a controlled identity, not a self-declared
  string**: acceptance authority now comes from the controlled organization
  record and credentials. A supplier accepting its own delivery, an unknown
  organization, or a forged buyer label is `UNPROVABLE` and can never reach
  Buyer Verified `PASS`. The report header shows who actually accepted.
  `AcceptanceAuthorityTests` covers supplier, unknown-org, forged-string and
  legitimate-buyer cases.
- **V-02 the delivered report is regenerated after signoff**: signoff now
  re-renders `report-delivered.md`/`.json` bound to the signed revision, the
  input fingerprint and the signoff reference. The pre-signoff snapshot is kept
  and labelled as pre-signoff. Evidence added after signoff can only produce a
  new revision; it never mutates the signed report.

### Note

The Growovo evidence handoff remains evidence-only: importing it does not
upgrade its grade, prove supplier acceptance or authorize payment.

## v0.14.3 - 2026-11-19

V-09 / V-10 from the 2026-11-18 review.

### Fixed

- **Close differences have explicit append-only dispositions**: `close action`
  records inquiry/correction/dispute/reserve/resolved against one report open
  item and calculation fingerprint, with owner, registrar and source reference.
  Only an explicit `resolved` closes the item; review/signoff never do. Amounts
  stay unchanged, later disputes can reopen an item, and signed revisions stay
  immutable. These are local declarations, not proof of supplier receipt or
  acceptance and never payment authorization.
- **Growovo handoff imports as evidence only**: `handoff import-growovo` checks
  the schema, run id, SHA-256 body fingerprint, sensitive-field ban and non-empty
  evidence before writing to a new/empty directory. Integrity success does not
  independently verify receipts or outcomes and does not enter canonical finance.
- **V-09 booking is a transaction**: `bill-candidate book` validates
  `--usage`/`--rule-map` before creating anything, writes the whole bundle in a
  staging directory, commits it with `booking.json` as the marker, and updates
  the candidate last. Failures leave no half-usable period bundle; a crash
  after commit is repaired by rerunning the same book (no duplicate export),
  and an interrupted finalize is rolled back from `.booking-pending.json`
  before retrying. A booked invoice or bundle can never be overwritten by
  another candidate.
- **Candidate ids are allocated under a store lock**: `add_candidate` builds
  each draft in a staging dir and renames it once under an exclusive
  `.bill-candidates.lock`, so two concurrent imports cannot share `cand-NNNN`
  or expose a half-written candidate; `candidate.json` is replaced atomically.
- **V-10 import integrity is tested**: `healthcheck/tests/test_import_integrity.py`
  imports every module in `buyer_ledger`, `verovo_lab` and
  `verovo_healthcheck`, resolves every relative import (including lazy ones),
  checks declared console scripts and imports the packages from outside the
  checkout — the deleted-`stats.py`/`prereg.py` class of breakage now fails CI.

Tests: `buyer-ledger/tests/test_bill_candidate_transaction.py` (10),
`buyer-ledger/tests/test_close_actions.py` (4),
`buyer-ledger/tests/test_growovo_handoff.py` (4), and
`healthcheck/tests/test_import_integrity.py` (4).

# Changelog

## v0.14.2 - 2026-11-18

Earlier review follow-ups rebased onto v0.14.1:

- **V-2 money contract v1**: one definition for cents-exact amounts
  (`healthcheck/verovo_healthcheck/money.py`) shared by every caller, so
  rounding rules cannot drift between components.
- **V-02 mapping disclosure**: close mapping previews name their open items
  and the confirm output discloses accepted gaps.
- **V-07 signoff timing**: the signoff race assertions follow the real
  ordering, plus a deterministic lock-contention case.
- **e2e quickstart trust**: the demo case passes the buyer-evidence gate it
  documents; CLI hints align with real paths.

240 buyer-ledger + 600 healthcheck tests, all vectors pass.


## v0.14.1 - 2026-11-18

Review follow-ups from the 2026-11-17 audit: the duplicated root test loaders
collapse into `tests/_root_support.py` (the V-4 dead-code claim for
`lab/stats.py`/`lab/prereg.py` turned out wrong — `lab/analysis.py` imports
them, so both files stay); the buyer-review example page gains outbound
navigation. No behaviour change.


## v0.14.0 - 2026-11-18

This release opens the review-mandated intake channel for bills (V-08 OCR
golden chain: candidate → field review → booked export into close) and makes
the delivery surface machine-checkable (P0.1 contract, bilingual close
reports, C-01-Lite delegation). buyer-ledger: 237 tests, healthcheck: 594
tests, all vectors pass.


V-08 (OCR bill golden chain): `bill-candidate add/review/book` opens the
review-mandated intake channel — a bill screenshot (local Vision OCR),
pasted text, or injected OCR lines becomes an unreviewed candidate draft with
field-level evidence (bbox region or text span + confidence per
vendor/invoice_no/period/currency/amount/line item). Key fields and
low-confidence/missing-evidence fields queue for named human review; the
draft never books itself, and booking is an explicit `--by` act exporting a
bills source (row-level provenance back to the candidate), the verbatim
document, and a `verovo.period-job/1` for the existing close chain. Bad OCR
ends BLOCKED_USER_INPUT with one user action and zero invented values; a
duplicate invoice_no refuses to double-book; `--engine paid-cloud-ocr`
returns the §5 `requires_procurement` JSON without any transaction. UNPROVABLE
stays a verification conclusion in the close report, never a delivery terminal
state. Follow-up hardening keeps every unextracted key field null, preserves the
image path's exact OCR text and hash, explicitly refuses real Vision OCR on
non-darwin, and blocks line-total mismatches and mixed-currency close imports.
Acceptance: 30 targeted candidate/close tests plus the replayable log
`buyer-ledger/docs/ocr-golden-chain-acceptance-20261117.json` (the log itself is
from the earlier 26-test synthetic replay, not a live Vision quality claim).

P0.1 (delivery contract): `python3 -m buyer_ledger contract show` prints
`buyer-ledger/docs/delivery-contract.json` — supported intents, §3.1 delivery
terminal states vs verification conclusions, exclusions, the §5 procurement
template and evidence requirements; consistency tests keep the file, the CLI
first interaction and the implementation aligned.

V-01 (differential cause qualification): an amount difference and its cause
are now separate claims in the ai-invoice-reconciliation example; no cause is
inferred from a non-zero difference alone.

V-SEO-01/02 (example attribution): the synthetic cost export now omits the
cache-read bucket instead of pretending cent-level rounding swallowed $15-$150
rows; the candidate is `cache_read_omitted_from_export`. The published sample
ships no `ground-truth.json`, so the 144.23 residual stays `unresolved` /
`UNPROVABLE` with `root_cause: unknown`; tests copy `ground-truth.example.json`
to exercise the confirmed path. A frozen-value test pins the 90-row totals.

V-02 (period import and human field mapping): one local billing period is
scanned and proposed as a mapping draft; only a human confirmation produces the
canonical bundle Period Intake consumes. Original files are never modified,
re-ingest is idempotent and keeps the original SHA-256, and OCR/invoice drafts
never enter the ledger.

V-03 (buyer-signable month-end report): `close calculate` runs the existing
canonical chain, `close report` emits six replayable report objects, and
`close review` records a named reviewer without closing unresolved items,
signing off the period or authorizing payment.

V-04 (versioned close and later adjustments): the root period is snapshotted as
r1; `close revise` appends an independently recalculated r2/r3 with a
PeriodRevision record and a delta, and `close signoff` binds a named decision
to one revision's source/rule fingerprints without rewriting older reports or
authorizing payment.

Adversarial audit follow-up: half-open rows are now excluded from totals,
duplicate bill rows cannot become a second report line, unknown currency and
missing unit prices fail closed, signed roots cannot be rebuilt in place, and
report replay is bound to period-job/mapping/calculation hashes.

V-06 (revision and signoff recovery): `close revise`, `close signoff` and the
read entries now write through a durable journal under `revisions/`, so a crash
between promoting `rN/` and updating `revisions/index.json` (or between the
signoff file and the index) is detected on the next command instead of leaving
an orphan version or a half-signed revision. `close recover` lists the pending
transaction and can `--apply` it or `--rollback` it; an unindexed `rN/` is
adopted rather than dead-ending the next revision, and a corrupted promoted
revision or journal is reported, never ignored. Signing re-verifies the report
fingerprints, the canonical snapshot and every source file hash first, and
refuses with `STALE_SOURCE` when they moved.

### C-01-Lite minimal delegation projection

`buyer-ledger close status` now also returns a `delegation` block with the eight
frozen fields (task_id / task_state / next_actor / next_required_action /
blocked_reason_code / result_ref / authorization_scope / recovery_entry) plus a
boundary line; the existing `c01_status` block is unchanged. `task_state` maps
the close-case state onto the six-word C-01 vocabulary and stays PARTIAL while
open items remain (signoff does not close them).

### V-07 cross-process lock for revisions and signoff

`revise`, `signoff` and `recover` now take an exclusive lock on the case
(`.close.lock`, `flock` with a non-blocking attempt). Two processes revising or
signing off the same period no longer race on the same `rN` journal file or the
signoff triple: one wins and the other gets an explicit `REVIEW_REQUIRED`
concurrency conflict. The journal keeps solving single-process crash recovery;
the lock solves "two writers at once". On platforms without `fcntl` the lock
degrades to an explicit refusal rather than proceeding unlocked.

### V-02 mapping review output: name the gaps before confirming

`close mapping` now prints every open item (source / field / reason) instead of
only a count, and the suggested confirm command carries the exact
`--control-domain role=vendor|buyer` flags the confirm step needs, so the
command printed by the tool runs as printed. A successful `--confirm` names the
fields it accepted as gaps (`pending_fields_approved_as_gaps`) — a required
field that stays unmapped can no longer be confirmed without being shown.

### Added

- **`buyer-ledger close`**: `init` / `ingest` / `mapping` / `calculate` /
  `report` / `review` / `revise` / `signoff` / `status` (and `show` as the same
  read-only projection); no later-ticket stub remains.
- **`verovo.period-job/1` import**: half-open `[from, to)`, one
  vendor/account/currency, IANA timezone, UTF-8/BOM handling, per-file SHA-256,
  path/symlink containment at ingest, duplicate-id diagnostics, four coverage
  categories, and a `BuyerCloseCase` read-only projection with C-01
  `task_state` / `next_required_action` / `blocked_reason_code`.
- **Human confirmation gate**: mapping draft -> `mapping-confirmed.json`
  (`attested_by_declaration`) -> `canonical/`; vendor-only exports keep
  `Buyer Verified = UNPROVABLE`; invoice/OCR documents stay `unreviewed` /
  `unbooked` and live under `manifest.documents`, never `manifest.sources`.
- **Price-table validity**: pricing rules must state currency, unit, version
  and `valid_from`/`valid_to`; an expired table refuses canonical generation,
  and reusable rules are proposed only when they cover the current period and
  always carry `requires_re_review_next_period`.
- **Six report objects (V-03)**: `report.md`, `report.json`,
  `line-reconciliation.csv`, `review-queue.csv`, `evidence-index.json` and
  `manifest.json` are generated from the current `calculation.json`; the
  manifest records input hashes, rule versions, code/algorithm versions and a
  stable output-snapshot fingerprint for V23 replay.
- **Mutually exclusive line amounts**: each invoice line is exactly one of
  matched posting, explained adjustment, excluded pending adjustment or
  residual; `invoice_total = matched_postings + known_adjustments +
  open_difference` holds per line, and multiple findings on one line never add
  a second amount. Missing invoice lines stay `null`, never `0`.
- **Independent axes vs verdicts**: the report keeps
  `finance/verification/delivery/economics_status` separate from
  `Rule Repriced` / `Invoice Reconciled` / `Vendor Reconciled` /
  `Buyer Verified` / `Delivery Accepted` PASS/FAIL/UNKNOWN/UNPROVABLE
  verdicts. A complete finance axis still carries the explicit “not a payment
  authorization” disclaimer.
- **Credit/tax/refund discipline**: applied tax/credit/discount lines enter
  known adjustments; issued/unapplied credits and refunds are open exposure;
  negative credit, positive tax and unapplied refunds never become realized
  savings.
- **Named-reviewer gate**: `close review --by ...` records reviewer, time,
  scope, reservations and next review; missing reviewer is
  `INPUT_REQUIRED`, and unresolved items are never promoted to closed.
- **Append-only PeriodRevision (V-04)**: the first revision snapshots the root
  to `revisions/r1` and every later version gets its own `rN/` directory with
  `period_job_id`, `revision_id`, `parent_revision_id`,
  `input_snapshot_hash`, `rule_map_hash`, `source_added/removed/changed`,
  `reviewer`, `closed_at` and `status`. Same input is idempotent; root
  reports/canonical are refused for in-place rebuild once a revision exists.
- **Three-way revision delta**: `delta.json` separates amount/cause/axis
  changes, newly available evidence, and applied versus merely issued credit.
  Issued credit stays open exposure with zero realized savings; applied credit
  reduces the amount due and is still not reported as savings.
- **Revision signoff**: `close signoff --by ...` records the case/revision,
  scope hash, source hashes, rule-map hash, reviewer, decision time,
  reservations and unresolved items; a new revision never inherits the old
  signoff, missing reviewer is `INPUT_REQUIRED`, and the result is explicitly
  not payment authorization.

### Fixed

- **buyer-ledger example**: `reconcile.py` no longer stamps the unexplained
  residual with a fixed `price_tier_mismatch` reason code or the cost-export
  gap with `cache_read_rounded_to_zero`. Differences are emitted as
  `observed_difference` facts with a layered qualification
  (`unresolved` default / `confirmed` / `not_a_vendor_error`); candidate
  explanations carry `basis_refs` and `confidence_kind=heuristic`. The cache
  gap stays multi-candidate (rounding / time window / async revision).
- **Confirmed explanations need evidence**: `confirmed` requires declared
  evidence; in the synthetic demo this is `make_demo.py`'s injection
  declaration in `ground-truth.json`, explicitly labeled
  `synthetic_ground_truth` and never presented as real independent proof.
- **Identity is not proof**: the residual identity
  `recalc + seats + credits + tax + residual = invoice_total` holds by
  definition and is kept only as a sanity check. Correctness evidence comes
  from new row-level cross-checks (`line-checks.csv`: usage×prices vs cost
  export per row, invoice usage line vs recalculation), which catch tampered
  rows the identity cannot.
- **Coverage caliber**: net coverage above 100% (or a non-positive
  denominator) is reported as `not_comparable` with gross/net shown
  separately; negative residuals keep their direction (`invoice_lower`)
  instead of being presented as normal residuals.
- **Period intake task links**: a bill-declared `task_id` is registered even
  when the bill is matched to a financialized usage row, and the matched usage
  run is linked only when the bills name exactly one task. Ambiguous run links
  stay unallocated instead of being guessed.
- **Half-open boundary actually closes the period**: rows at `to` or before
  `from` are copied into `period_outliers` but no longer written to canonical
  or `invoice_total`; they appear as `period_outlier` open items/queue entries.
- **Duplicate representations are not a second line**: `period_intake` now
  appends a bill row to `reconciliation_rows` only after the duplicate check,
  so a repeated `charge_id` is flagged but cannot produce a second V-03 line or
  a self-consistent double-counted identity.
- **Unknown currency fails closed**: lines without a period currency are
  `excluded_currency_unknown`; `invoice_total`/`open_difference` become `null`
  and `invoice_total_status=unknown`, never an assumed USD total.
- **Signed roots are immutable in place**: `close review`, `close calculate`
  and `close report` refuse when `signoff.json` exists (`REVIEW_REQUIRED`);
  report/signoff bytes and fingerprints stay unchanged until a new revision.
- **Replay binds the actual inputs**: `calculation.json` stores period-job,
  mapping-confirmed and rule-map hashes plus a calculation fingerprint;
  `build_report` rejects any mismatch or edited calculation as
  `STALE_SOURCE`. Missing unit prices produce `rule_repricing_status=partial`
  and a `null` subtotal instead of `0`.
- **Cross-platform path rejection**: Windows drive/UNC/backslash traversal
  paths are denied on POSIX too, alongside existing `..` and symlink checks.

### Verification

- Buyer-ledger: **173 tests pass** (110 pre-V-01 + 7 V-01 adversarial + 31 V-02
  acceptance tests covering AC V07–V15, BuyerCloseCase, half-open filtering,
  duplicate booking and cross-platform paths + 16 V-03 acceptance tests
  covering AC V16–V23, missing currency/amounts, rule-subtotal partiality and
  replay input hashes + 9 V-04 acceptance tests covering AC V24–V29,
  PeriodRevision, append-only signoff and the revise/signoff CLI; the relevant
  V-02/V-03/V-04 tests were committed red first).
- Healthcheck: **593 tests pass**.
- Repository root: **59 tests pass**.
- Python 3.9; synthetic fixtures only; no network calls and no real billing
  data.

## v0.13.0 - 2026-11-04

Independent completion axes and evidence provenance: finance can finish without
Task Economics, rule repricing is no longer called reconciliation, and
self-declared independence is no longer accepted as proof.

### Added

- **Four completion axes**: `finance_status`, `verification_status`,
  `delivery_status` and `economics_status`, each with explicit semantics. A
  finance-only period with no outcomes source can be `finance_status=complete`
  and compatibility `status=complete` while the other axes are
  `not_applicable`. An outcomes source that exists but is empty remains partial.
- **Rule Repriced / Invoice Reconciled**: `Rule Repriced` is approved-rule
  recalculation; `Invoice Reconciled` requires a distinct second source record.
  `Invoice Matched` remains a compatibility alias of `Rule Repriced` with an
  explicit basis and `alias_of`. `reconciled_charges` comes only from distinct
  source reconciliation.
- **Evidence provenance**: source affiliation, control domain, collector party,
  confirmation process and observed source path, with aliases for
  `source_association` / `collected_by`. `buyer_independent` is treated as an
  assertion, not proof; missing or conflicting provenance fails closed.
- **Adversarial coverage** for relabelled vendor evidence, vendor-path delivery
  acceptance, buyer usage PASS with a different vendor invoice, and two sources
  agreeing while both violate the approved rule.

### Verification

- Healthcheck: **592 tests pass**.
- Repository root: **59 tests pass**.
- Buyer-ledger: **110 tests pass**.
- Healthcheck selftest, calendar/registry checks, and the full conformance
  runner suite pass.
- Python 3.9 and 3.11 healthcheck suites pass.
- Synthetic fixture only; no real supplier or customer data.

## v0.12.0 - 2026-11-04

A narrow, fail-closed canonical finance chain built on the existing Period
Intake, approved Rule Map and Task Economics components. Unknown evidence stays
unknown; vendor control is never relabeled as buyer independence.

### Added

- **Canonical finance orchestrator** (`healthcheck/verovo_healthcheck/canonical_finance.py`,
  `scripts/run_canonical_finance.py`): known CSV/JSONL inputs → Period Intake →
  approved Rule Map → Decimal per-unit recalculation → allocation and Task
  Economics. Unsupported formats, mixed vendor/account/currency scope and
  unapproved rules fail closed. OCR/invoice2data remains unreviewed/unbooked.
- **Shared-cost drivers**: shared keys, seats and platform fees require a
  versioned driver and approval. Local driver rows are checked against approved
  weights; missing weights are never averaged or filled with zero.
- **Four independent evidence conclusions**: `Invoice Matched`,
  `Vendor Reconciled`, `Buyer Verified`, `Delivery Accepted`, each carrying
  source party, collection method, independence and recalculation status.
- **Complex synthetic canonical fixture** covering shared API key, seat fee,
  buyer-accepted and buyer-rejected delivery, and vendor-claimed value.
- **Regression coverage** for canonical intake/rules/economics, optimizer
  unknowns, buyer-ledger currency handling and audit mutation.

### Changed

- Missing amount, currency or quantity is `unknown/open` or a build error, never
  a default currency or zero. Complete totals are blocked while attributed
  subtotals remain explicitly named.
- Allocation coverage and reconciliation are orthogonal. Per currency,
  `allocatable = allocated + unallocated + non_allocatable`; unknown inputs keep
  the relevant values null. A matched amount difference is `difference` and
  never enters `reconciled_charges`.
- Cost per Verified Success is blocked by unallocated cost or unknown success
  denominator. It is not ROI.
- `verified_success` is null when delivery evidence is missing, not false.
  Missing success counts keep `verified_success_tasks` and CPS null.
- Buyer-ledger reconciliation no longer copies the only bill currency onto usage
  rows with missing currency; those rows remain unknown and incomplete.

### Fixed

- Duplicate vendor exports can no longer inflate Buyer Verified; two vendor
  exports can at most support Vendor Reconciled.
- The optimizer no longer turns unknown cost into zero in scenario simulation.
- Missing approval, driver, unit price or non-finite numbers fail closed in the
  audit builder.

### Verification

- Healthcheck: **583 tests pass**.
- Repository root: **59 tests pass**.
- Buyer-ledger: **110 tests pass**.
- `python3 healthcheck/verovo selftest`: PASS.
- Registry: 18 metrics valid. Vocabulary: 21 entries in sync.
- Conformance: metrics 22/22, outcome 15/15, delegation 23/23, SSE 2/2.
- Complex fixture: fully loaded cost 86.00 USD, task costs 58.00/28.00 USD,
  Cost per Verified Success 86.00 USD, ROI null because vendor-claimed business
  value is not independent evidence.

### Boundary

The canonical slice supports per-unit pricing and approved allocation only. It
does not support FX, general contract parsing, arbitrary invoice formats or
external signature/identity verification. OCR accuracy and real supplier data
remain unverified. The complex fixture is synthetic, not production data.

## v0.11.0 - 2026-11-02

### Changed

- **Audit amounts are Decimal end to end and serialize as two-decimal
  strings** (`"44.00"`), never JSON numbers, matching
  `task_economics` money semantics (`Decimal(str(value))`, cents quantized
  ROUND_HALF_UP, equal splits floored with cent redistribution). Previously
  the builder computed with `float`/`round` and the schemas typed amounts as
  numbers; both schemas and the validator now enforce `^\d+\.\d{2}$` so
  binary float rounding cannot enter an audit case. Previously failing
  sample: a USD 1,000.00 shared fee across three tasks produced shares
  summing to 999.99 and a misallocation (666.67) inconsistent with any split
  that adds back to the invoice total; it is now 666.66 with shares summing
  exactly to 1,000.00.
- **Contract rule parameters are data, not code.** Unit prices, the volume
  discount rate/threshold and the allocation policy come from `parameters`
  on each confirmed rule in `contract-rules.json`; a missing parameter fails
  closed (`BuildError`) instead of falling back to a built-in constant.
  Previously changing the confirmed CR-006 price in the rule catalog did not
  change what the builder billed against.
- **Settlement is evidenced by application, not issuance.** A duplicate only
  starts `realized` when the credit register shows `applied: true` plus
  `applied_at`; an issued-but-unapplied credit keeps the finding
  `identified`, cites the credit as evidence and keeps the amount inside
  open exposure. A refund on a cancelled line likewise stays open until the
  credit is applied.
- **Coverage no longer double counts a line.** Findings carry
  `affected_lines`; `unverified_amount` sums each affected line's worst
  single open finding (the maximum, never a sum over findings on the same
  line), and a line behind an UNPROVABLE finding contributes its billed
  amount once. The coverage computation no longer parses amounts out of
  human-readable basis text, and `verified + unverified + settled` must
  reconcile to the invoice total or the build fails.

### Added

- **Audit case contract** (`schemas/audit-finding.schema.json`,
  `schemas/audit-case.schema.json`): a finding carries classification,
  severity, amount (`null` when unprovable), business status
  (`identified`/`validated`/`accepted`/`realized`, plus `dismissed`),
  evidence references and a versioned rule reference with its confirmer.
  A case binds buyer, vendor, period, contract version, scope, coverage and
  findings. The schemas reject a `provable: false` finding without a reason,
  a zero amount standing in for an unknown one, and a `realized` finding
  without a settlement reference.
- **Audit golden case** (`examples/audit-golden-case/`): confirmed contract
  rules, invoices (including a EUR line), credit register, tasks, runs and
  outcomes, with eight independently expected findings across every
  classification and an explicit UNPROVABLE case that never becomes zero.
- **`scripts/validate_audit_case.py`**: standard-library deterministic
  validator for the two contracts (enums, referential integrity, evidence
  paths confined to the case root, UNPROVABLE and settlement rules).
- **`scripts/build_audit_case.py`**: builds an Audit Case from a bundle that
  carries a manifest, confirmed rule catalog, invoices, tasks, outcomes and
  credits. Rule handlers are deterministic and keyed to confirmed `rule_id`s;
  the builder emits `identified` findings and only marks a duplicate
  `realized` when a matching credit note exists. Running it on
  `examples/audit-golden-case` reproduces all eight expected findings and the
  USD 1,686 / 394 coverage split, and the output passes the validator.
- **Audit mutation tests** (`tests/test_audit_mutation.py`): four mutation
  groups (contract parameter change, two findings on one line, issued vs
  applied credit note, Decimal/split identities) pin the financial
  correctness rules above; each group names the previously failing sample it
  guards against.

## v0.10.0 - 2026-11-01

First release since v0.9.0. Buyer review and task economics leave main-preview,
and a billing period can now be assembled, audited and simulated from local
records.

All notable changes to Verovo (standard, SDK, and reference product) are
documented here. Format follows [Keep a Changelog](https://keepachangelog.com/).

> Entries below v0.9.0 are kept as written and use the former name
> (AgentMeasure). The rename is recorded in v0.9.0 and in
> [docs/RELEASE-v0.9.0.md](docs/RELEASE-v0.9.0.md).

### Added

- **Buyer review** (`verovo review`): scoped buyer tasks, independent
  charge/delivery criteria, local JSON observations, traceable HTML/JSON and
  synthetic examples. Missing/null/incompatible evidence stays UNPROVABLE, and
  every report separates rule consistency (Level 1), evidence credibility
  (Level 2) and the buyer business decision (Level 3). Six operating stages are
  mapped to shipped commands in
  [docs/review-method/buyer-review-stages.md](docs/review-method/buyer-review-stages.md).
- **Task economics** (`verovo economics`): explicit tasks, runs, financialized
  usage, cost items, allocation rules and verified outcomes produce
  direct/allocated/fully-loaded task cost, cost per verified success and ROI
  only when verified value exists in the same currency. Duplicate rows,
  cross-currency rows, unallocated items and unverified outcomes are reported,
  never silently combined.
- **Period intake** (`verovo intake --bundle DIR --out DIR`): a manifest plus
  local traces, usage rows, supplier bills, outcomes and optional invoices is
  assembled into the economics case. Bills matching financialized usage
  reconcile by `charge_id`; currency mismatches are excluded; unallocated cost,
  unreviewed invoices and unverified outcomes stay named open items.
- **Cost drivers** (`verovo drivers`): the same cost rows re-bucketed by vendor,
  category, task type, model, retry and allocation; recorded and attributable
  cost are reported separately, and missing dimensions stay `not-recorded` /
  `not-separable`.
- **Scenario simulation and proposals** (`verovo optimize`): explicit
  assumptions (`scale_cost`, `drop`) are applied to a case copy and
  re-evaluated against the baseline. Proposals cover reconcile-before-ROI,
  outcome verification, vendor substitution under a quality gate, retry
  instrumentation and allocation review; all are `requires_human` and
  `automatic_apply: false`.
- MCP read-only tools: `verovo_review`, `verovo_review_template`,
  `verovo_recount`, `verovo_invoice_intake`, `verovo_period_intake`,
  `verovo_optimize`.
- Skill task entries `verovo-review-charge` / `verovo-review-delivery` /
  `verovo-prepare-dispute` inside the single `verovo` skill.

### Changed

- Product definition, buyer workflows, README and public site aligned around
  buyer-first charge and delivery verification; provider work stays conditional.
- Documented tagged installation versus main preview; free measurement services
  retained without obsolete offers.
- Interruption/evidence handling hardened with regression coverage.

### Validation

- 563 tests pass locally, including review lanes and levels, economics
  duplicate/unallocated handling, period intake reconciliation and currency
  exclusion, cost-driver shares, scenario simulation and the human gate on
  proposals.

## [v0.9.0] - 2026-10-30

### Changed

- **Renamed to Verovo** (formerly AgentMeasure). CLI `agentmeasure` → `verovo`
  (0.9.0), PyPI distribution `agentmeasure` → `verovo`, SDK `@agentmeasure/mcp` →
  `@verovo/mcp` (0.3.0), environment variables `AGENTMEASURE_*`/`AM_*` →
  `VEROVO_*`, local data directory `~/.agentmeasure/` → `~/.verovo/`, schema
  namespaces `agentmeasure.*` → `verovo.*`, Python module `am_healthcheck` →
  `verovo_healthcheck`, settlement standard AMS-1 → VSS-1.
- **Repository and site**: `roy-tong/AgentMeasure` → `roy-tong/verovo`,
  `roy-tong.github.io/AgentMeasure` → `roy-tong.github.io/Verovo`. Canonical,
  Open Graph, sitemap and badge URLs point at the new paths.
- **New brand assets**: wordmark and owl mark in `assets/` and `website/assets/`,
  new favicon, regenerated Open Graph card and caller-identity ladder diagram,
  usage rules in [docs/brand.md](docs/brand.md).

### Compatibility

- External conformance vectors and recorded run logs are preserved unchanged.
  `funnel-event` and `report` schemas accept `agentmeasure.lab/*` as permanent
  legacy aliases, and the urusilla-002 runner reads the pinned fixture's original
  key, so the external vectors still validate and re-derive their metrics.
- JSON exports keep the tool tag `verovo-healthcheck`; schema version and field
  names are unchanged.

### Verified

- 511 healthcheck unit tests, healthcheck `selftest`, 91 lab tests, 106 buyer-ledger
  tests, 21 SDK tests, all conformance runners and the vocabulary/registry checks
  pass on the renamed tree.

## [v0.8.0] - 2026-10-15

### Added

- **Claude response evidence**: `response-audit` compares explicitly selected
  JSONL files by response ID, exposing repeats, conflicts and missing fields.
  It never calculates session or invoice totals or rewrites source logs.


- **Bill-recount identity**: README (EN/zh) and the website hero now lead with
  supported recount with a copy-paste synthetic sample; task routing also
  keeps developer diagnostics and metering QA visible. Buyer invoice review
  is not included in the $10/$20 services. Source-only features are labelled.
- **`recount --sample`**: run the bundled 5-row synthetic export (Zendesk rule
  set) with no CSV of your own — the same file the README shows.
- **Evidence-qualified headline**: recount labels published-price estimates,
  identified net variance and unsettled rows. These are not verified invoice
  totals or refunds; zero identified variance does not clear an incomplete bill.
- **Reproducible showcase** (`examples/showcase/`): three public cases
  (overcharge with refused guesses / findings in both directions / the
  contract lane) with frozen expected outputs; CI compares them on every
  change so the showcase cannot drift from shipped behaviour.
- **Monthly bill reconcile workflow** (`.github/workflows/reconcile.yml` +
  `RECONCILE.md`): opt-in hosting in an independent private repository; data
  is uploaded to GitHub. Requires a reviewed export hash and billing period.
  Stale scheduled inputs stop; optional issues track offsetting findings and
  unknowns per vendor/period, not only nonzero net amounts.
- **Vendor billing-rule scorecard** (website claims index): the six published
  rule sets with prices, triggers, dated sources, confidence grades and how
  far each is re-countable from a counts-only export — the page always
  matches the shipped registry.

## [v0.7.2] - 2026-10-15

- Add `inspect-input`: read-only JSONL format routing, explicit NOT-RUN validation/verdict, invalid-row counts and safe next steps.
- Conformance explains canonical/rollout/mixed input mismatches before emitting unrelated FMT-002 field errors.
- No implicit conversion, repair, upload or evidence upgrade.

## [v0.7.1] - 2026-10-15

### Fixed

- Include the bundled lab subpackage in the wheel so installed conformance works.
- Scaffold under the caller's working directory; explain missing fixture input.
- Correct share-export confirmation and zero-cost evidence wording.
- Exercise conformance in the installed-wheel smoke test.
- Add first-run recovery guidance, report examples and email-client fallbacks to the website.

## [v0.7.0] - 2026-10-14

### Added

- **`validate` now recognizes the standalone share-summary export** (`summary.json` written by `share --out`): whitelist + required-key checks via the same `summary_problems` the preview-then-export flow uses — a buyer-submitted summary is machine-checkable end to end (`validate summary.json` prints `valid share-summary`), and tampered/missing-key files are named, never waved through.
- **Windows, ahead of demand**: stdout/stderr degrade unencodable glyphs instead of crashing mid-report on legacy console code pages (cp1252/cp936), and CI gains a `windows-latest` job (full unit suite + a demo → share → validate smoke). Paid-diagnosis intake accepts Windows-generated summaries.
- **Paid diagnosis page live** (website/services.html): two fixed-price human-delivered diagnostics — $10 healthcheck anomaly diagnosis, $20 metering QA single-sample check — email application, refund terms on the page, and an explicit "free answers IF, paid answers WHAT" line; README (EN/zh) positioning updated to match.


- **Five-direction maturity wave (requirements pool §七)**: 
  - **S1/S3 SDK 0.2.0** — tool annotations declared in the SDK's own examples (readOnlyHint/destructiveHint/idempotentHint/openWorldHint, with the spec note that idempotentHint is Optional) plus a "declaring tool annotations" README section: the metering standard's own MCP now preaches what it practices. npm tests 21/21 on a clean install.
  - **C1 vector scaffolding** — `agentmeasure conformance --new-vector <name>` generates a CI-valid external vector directory (FMT-002 fixture + metadata + expected + claim-boundary README); the generated fixture passes the pack's FMT-002 validation as-is (verified: 4 PASS / 1 UNPROVABLE, rc 0). One command from "I want to contribute" to a pull request.
  - **C2 reproduction emit** — `agentmeasure certify --pack X --emit-reproduction WHO` prints the exact block to paste for a Stage-2 promotion (digest math included; annotations can't reference themselves).
  - **H1 adapter freshness canary** — pinned one current-generation session sample per runtime (`fixtures/canary/` + manifest) with `scripts/adapter_canary.py` in CI: when Codex/Claude ship a format change, the canary goes red and names the adapter BEFORE users see zero-session reports. Tamper-tested.
  - **B1 multi-vendor console** — `agentmeasure console --ledger a.json --ledger b.json`: one page across vendors, per-vendor journey groups with prefixed view ids, an overview table (findings / variance / outstanding per vendor), and an explicit rule that nothing is summed across vendors. Single-ledger rendering is byte-identical (tested).
  - **B3 monthly review SOP** — `docs/monthly-review.md` + `scripts/monthly_review.py`: the subscription ritual (rules-diff gate → verify → period-compare → journals) as one runnable script with `--demo` sandbox acceptance; material rule change exits 1 with re-verify-and-disclose instructions.
  - **X1/X2 five-entrance routing** — README (EN/zh) now opens with the five-entrance matrix (buyer / conformance / SDK / Lab / healthcheck, each with its user and one-line value); the website gains a matching "Pick the row that is yours" section.
- Skipped by discipline: S2 Python SDK (signal-gated), B2 non-developer testing (needs Roy-side users), L1 Lab claude live (needs a live window).


- **Distribution assets (requirements pool r3 §1.5, absorbing the M8ven case)**: public **vendor rule cards** (`scripts/gen_rule_cards.py` → `website/rules/`, one card per vendor: price, trigger, reopen handling, silence behaviour, closure timers, source link, registry version — with a CI `--check` so the page can never drift from the registry) and the **AMS-1 badge set** (`website/badges.html` + four stage SVGs under `assets/badges/`, embeddable markdown snippets, an adopters snapshot listing external conformance-vector authors with links, and an explicit no-composite-score statement — the anti-M8ven). status-sync now mirrors the whole `website/` to gh-pages instead of status.json only, so every new page ships on green CI.
- **`agentmeasure drilldown`** (BP r32 p09 promise ②, "ask the customer to re-check one finding"): replays ONE conversation on screen — the exported row, every rule step that fired in plain language, the verdict with its three-state, the amount at issue, and the exact command to reproduce it. `explain_one` walks the same branches as the recount in the same order; a test replays every fixture row through both and asserts the verdicts agree. Tier 1 only by design — the buyer's own standard is leverage, not part of the billing replay.
- **`agentmeasure decisions`** (the third value book from BP r32 p06): an append-only local journal recording how the buyer used the verification in a business decision (payment approved/held, discount requested, renewal signed, vendor review…) — enum-validated, linked to the finding it rests on, outcome stated, amounts context-only and never summed with recovered cash. Cash (recovery), hours (delivery), decisions (this) now each have their own book.
- **Release smoke job in CI**: builds the wheel and runs `--version` / `demo` / `selftest` / `recount --list-vendors` from a fresh venv — the packaged artifact must actually run (source-tree green ≠ installable; the 0.4.0 NameError lesson, now mechanical).
## [v0.6.0] - 2026-10-11

### Added

- **External conformance growth**: iwasinnam-003 (Metrecept session-aggregate receipt vector, litellm#39057 — 1 MISS + 2 HIT with per-crossing meter_event_id that never collapses, estimates forever unsigned), the rksharma-001 external-contribution card (PR #27 cross-file replay enforcement), and **chenhz01-001 — the first availability-certification vector, contributed via PR #32** (gunjanjaswal) with an airtight empty-events guard and documented real-trace boundaries. Metrecept public verification URLs pinned per iwasinnam2's 10/10 review.
- **Release plumbing, now proven live**: PyPI trusted publishing works (0.5.0 shipped on the re-run), npm works (@agentmeasure/mcp 0.1.1 live); the release workflow gained workflow_dispatch tag input and idempotent PyPI + npm steps so re-runs never fail on already-published versions.


## [v0.5.0] - 2026-10-03

### Added

- **The remaining BP r28 product gaps closed — six lanes, one ledger, one dashboard**:
  - `agentmeasure crosscheck --ledger billing.csv` — the 计费流水 input (p5): the buyer's billing ledger against the export. Charges the export doesn't flag, billed flags the ledger doesn't confirm, amounts off the published price, ids absent from either side — every disagreement is *named, never resolved* ("事后无从对起" now has a place to 对).
  - `agentmeasure verify` — the **Verified Ledger** (p8): one artifact joining Tier 1 + outcome lane + billing cross-check + Tier 2 + realized recovery, each lane labelled claim / leverage / realized and never summed across (json + md, sha256 provenance for every input).
  - `agentmeasure dashboard` — offline single-file HTML rendering a verified ledger or dispute pack: three-state cards, disputed lines, outcome lane (leverage tag), cross-check, recovery bar. No JS, no network, printable; renders, never recomputes; every value escaped.
  - `agentmeasure rules-diff old-snapshot.json` — the monthly subscription ritual (appendix A "规则变化"): semantic diff of two vendor-rules registries; material fields (trigger, reopen deduction, silence behaviour, price, timers) exit 1 so a cron can gate; a material change means re-verify under the new rule and disclose, never mix.
  - `agentmeasure narrative` two-step — the 叙述层 (p13), without breaking the package's offline guarantee: `--emit-prompt` writes the drafting prompt built from the pack; you run your own model; `--check-draft` verifies **every number in the draft against the pack** — invented or rounded numbers refuse the draft and the rule-based template ships with the violations printed. The model never touches the numbers; with no model, the template is the output (the BP's degradation promise).
  - `agentmeasure delivery` — appendix-E metrics made measurable: append-only local journal of engagement phases keyed by the mapping fingerprint `prepare` now emits (column provenance only, never values); reports first-look hours vs the 10h bar, third-period minutes vs 1h, repeat-period reuse vs 70%.
  - **Outcome-unit schema v1** (p14 Emerging row, "先做 schema"): [schemas/outcome-unit.schema.json](schemas/outcome-unit.schema.json) — qualified_lead / completed_case units with contract_ref, observer_grade aligned to the AMS-1 ladder, criteria_unproven and an `unproven` status as first-class states; three worked examples + a draft-07-subset conformance test.
  330 tests (+36). Narrative/delivery/crosscheck stay inside the offline discipline: the no-network import guard covers them, and no model, network, or dependency was added.
- **Agent Commerce operating layer (F2.10-F2.15)**: `pnl-trees` (F2.10: Organic and Paid metered separately — an order belongs to exactly one tree, a paid order must cite a campaign, and citing a campaign absent from the ledger is refused as mixed attribution; ROAS computed inside the Paid funnel only); `cm-ledger` (F2.11: attributed revenue − coupon − media spend − platform/payment cost − service fee − fulfillment variance − refund = Contribution Margin, closed category enum, negative costs refused — the data base of the monthly P&L template); `demand-audit` (F2.12: Channel Potential vs Operator Performance as two thresholded axes, unmeasured counts as NOT met never assumed, verdict Launch/Watch/Not ready names which axis failed); `intent-taxonomy` (F2.13: consumption-task clusters, Operating Cell = cluster × surface × proposition, stable ids with retire-and-successor, owner + review cadence, seed with worked examples — fill to 100 by copy-editing real tasks, not filler); `decision-audit` (F2.14: auto/approval/forbidden policy graded per execution with policy citation, `risk_policy_ref` to the Agentic Ops Action Risk Policy required, uncovered actions default-forbidden and the gap named as a finding about the policy); `benchmark-export` (F2.15: cross-brand aggregates only — identifier columns on a denylist, min-brands suppression with values withheld, median/p25/p75 over per-brand medians, self-verified before share; customer-level data never enters the shared pool). Suite 430 (+30).
 — measurement where the click disappears**: new commerce-profile schema (`schemas/commerce-profile.schema.json`: Operation=Commercial Intent, Attempt=Agent Decision Run, live/replay/synthetic segregated at the schema layer, decision observations record `observed_merchants` and the schema has no `candidate_set` field anywhere, attribution links graded observed/correlated/inferred/UNPROVABLE by named rules, `measurement_policy_version` required on every record) and the engine + CLI (`agentmeasure commerce-ledger --events --orders --payments --policy-version`): F2.1 mixed-mode aggregation is an error, not a blend; F2.4 canonical retry case pinned as a CI vector (`conformance/vectors/commerce-retry-baseline/` + runner: **1 Operation / 2 Attempts / 1 verified order / net GMV = 0**, naive dashboard 100% off); F2.5 receipt = metric+value+evidence table (invocations merchant-observed / verified orders direct mapping / net GMV strong / assisted correlated / incremental UNPROVABLE — always null, this receipt has no holdout); F2.6 naming lint (metric strength must never exceed evidence strength, synthetic_ mode prefix, self-linted on every receipt); F2.7 governance (policy version, input bytes sha256-anchored, definitions frozen under the policy); F2.8 merchant-side connector (documented generic CSV shapes + sandbox fixtures); F2.9 materiality (naive vs verified, discrepancy in currency AND percent with named categories; below the 0.3% floor the receipt says the error is nobody's business).

  - `agentmeasure crm-join` — the four-inputs story finished (p5): buyer CRM/business records join the export; empty judgement cells are FILLED with `crm:` provenance, disagreements are named as conflicts, nothing is silently overwritten.
  - `agentmeasure outcomes` — the **outcome verification engine** (p14 Emerging, schema-first is done): outcome-unit JSONL in, fail-closed ledger out. Core invariant: `counted` + `criteria_unproven` is an inconsistency and becomes INVALID, never smoothed; reopens inside the stability window are named, not judged; duplicates disclosed; claim sums counted units only with a single-currency check. Shares the draft-07-subset validator with the conformance tests.
  - Outcome-unit schema v0.2 — the unified Billable Unit across the three stages (p14): `unit_type` widens to `action` (Copilot premium request / Cursor credit / Devin ACU, allowance via `billing_context`) and `usage_aggregate` (A3 metering via `measured_quantity`); v0.1 examples still validate (additive only). registry/billable-unit.yaml gains `action_consumption`, `usage_aggregate`, `incrementality_claim`.
  - `agentmeasure period-compare` — the subscription's monthly ritual (appendix A): two ledgers in, an honest delta out — three-state movement, variance delta, new/cleared disputed lines, recovery progress; a rules-version change is flagged and the periods stay separate artifacts.
  - `agentmeasure template` — appendix-E reuse as an artifact: mapping fingerprint + contract overlay + boundary cases in one portable template; `replay_boundary_cases` re-runs the cases through the recount so a rules change that moves a boundary is caught before it moves a claim. Method only — no client rows travel.
  - `agentmeasure certify` — the adoption ladder, graded from evidence inside the artifact (Stage 0 self-attested → 1 recomputable → 2 independently reproduced → 3 counterparty accepted) + a **deterministic digest**: sha256 over the document minus timestamps and annotations, stable across machines; missing evidence is named, never rounded up; signing stays outside the package.
  - `agentmeasure incrementality` — the p15 third question as statistics, not verdicts: treatment vs holdout, pooled two-proportion z + Newcombe hybrid-score CI (stdlib only), honest bands (insufficient evidence below n=30, "covers zero" said plainly). The claim boundary is printed on every output — no PASS/FAIL leaves this module, by design.
  375 tests (+27).
 to the current vocabulary and discipline**: PASS / FAIL / UNPROVABLE summary cards and per-line three-state badges (the page now shows what recount.js emits); **对不齐不出数 enforced in the UI** — when required columns are missing the alignment error is shown and NO numbers render (previously a full zero-filled result appeared under the error); per-line findings CSV download for the finance hand-off; live re-render on price override; rules-registry version shown next to the rule source; `aria-live` on results. Inline JS syntax-checked; page logic exercised via node against the shared fixture.

- **Buyer-side verification pipeline completed to the BP r28 shape (`prepare` / `recount --contract` / `recovery`)**: `agentmeasure prepare` maps a native Intercom/Zendesk export onto the canonical seven columns — judgement columns stay EMPTY for human review, native signal columns travel as `hint_*` cells (leads, never verdicts), and `prov_*` cells record where every value came from; `--inspect` prints the alignment report and writes nothing. `recount --contract <json>` adds the buyer's outcome standard (reopen window in hours, human-takeover disqualification, issue-addressed requirement) as a **separate outcome lane**: per-line verdicts with reasons, an informational at-risk amount, and a hard rule that outcome findings are renewal leverage and never netted into the Tier 1 billing claim (a contractual reopen window is judged only from a recontact *timestamp* — the vendor-window boolean cannot decide it, so it reads UNPROVABLE instead). `agentmeasure recovery` joins vendor concessions (confirmed/credited/paid, with dates and evidence refs) to the disputed lines of a dispute pack: over-realization is refused as an input error, unmatched confirmation rows are listed and never counted, and realized cash is summed separately from the claim. Vendor-rules registry 0.5.0 gains `optional_columns` (`customer_recontacted_at`), the recontact-timestamp aliases, and per-vendor `prepare_hints` carrying the F1.1 memo's native-column caveats. The AMS-1 three-state vocabulary (PASS / FAIL / UNPROVABLE) now rides on every recount line and summary in both the Python and browser implementations (parity-guarded). 46 new tests (294 total); end-to-end smoke: native Zendesk export → prepare → human-filled recount → contract lane → dispute pack → recovery ledger.

- **conformance: [urusilla-002](conformance/vectors/external/urusilla-002/) — second external conformance vector** (pinned urusilla @ `cfb4ef3`, by @jaden3824, langfuse#16383): a different boundary from 001 — two operations in one assignment (cache-write failure → cache-read retry; local required-schema rejection → explicit JSON fallback), 10 FMT-002 rows, 4 attempts, 13 synthetic cost units. New guard `run_external_fixture_002.py` (wired into `conformance.yml`): schema validation, full metrics reproduction against the externally pinned `expected.json`, the AM-U-007 grain regression, and a fail-closed check (an operation whose declaration fails reconciliation contributes nothing to the median). Claim boundary per the source project, stated verbatim in the vector report: project-authored synthetic offline evidence only — no AgentMeasure endorsement, upstream acceptance, external reproduction, Urusilla adoption, live model run, or real provider-cost observation is implied.

- **Evidence bundle — [live-codex-desc-clarity-001](bundles/live-codex-desc-clarity-001/) (honest provenance, `stats-recomputable`)**: official report verbatim + `recompute_stats.py`, an independent stdlib recomputation of the statistics layer from per-arm counts alone (pooled two-proportion z-test p = 0.285049; Newcombe hybrid-score CI95 [-0.2808, 0.6994]; difference +25.0pp — 9/9 checks reproduce) + `MANIFEST.md` documenting the honest downgrade from `bundle-verifiable`: the run's raw events / preregistration manifest / run directory were written under gitignored paths and never exported, are not recoverable, and are not reconstructed. Closes the 8/25–28 trust-debt item ahead of schedule.
- **Process fixes so bundles cannot silently rot**: `conformance.yml` re-executes every `bundles/*/recompute_stats.py` on each run (`bundles/**` added to push paths — report or script edits now trip the build); `release.yml` zips every `bundles/<id>/` and attaches it to the GitHub Release at tag time; `lab/README.md` gains the "Releasing a live run" checklist (export the four-piece set before tagging; honest labels for gaps).
- **M3 execution-grain conformance vectors** ([metric-execution-grain](conformance/vectors/metric-execution-grain.json), runner in `run_metrics.py`): operationalizes DR-006 (emission grain ≠ analysis grain) with three vectors — a runtime retry chain (429 → timeout → success under one `operation_id`) resolving to M3.1 = 1 operation with M3.3 = 0.333 attempt success and `attempts_per_operation` = 3 disclosed; three independent executions with no grouping evidence staying unresolved (M3.1 = 0, fail-closed, `legacy_attempt_equivalent` disclosed instead of disguised); and the same three attempts read at both grains (the "33% vs one logical use" disclosure case). External evidence: external reviewers via direct replies 2026-08-24 (operation as additive grouping key, not the emitted unit; append-only attempt facts) — attribution anonymous pending consent.
- **DR-005 refinement — mandated use vs dependency by construction** (2026-09-18, Rajesh Sharma, OWASP, named with consent): the orchestration carve-out splits into two evidence grades. Forced or required calls certify influence outright (the harness consummates the use); piping and data dependencies certify propagation only, since the receiving tool can ignore the value, leaving behavior to the experiment layer. Motivated by the reviewer's naming: dependency provable by construction is not the same claim as influence.
- **DR-005 addendum — orchestration-determined influence** (2026-09-14, Gunjan Jaswal, named with consent): when the orchestration layer rather than the model consummates the use (forced/required tool calls, programmatic piping, tool-to-tool data dependencies), influence is provable by construction from the dependency graph. Admitted as a second strong evidence class for State 2, not a third state; model-side influence still requires the experiment layer. Mixed case (harness constrains the choice set, model selects) currently splits inside the evidence rank, selection still needs ablation.
- **DR-005 revision — two states + ranked evidence**: adopted the client-side reviewer's reframing (direct reply 2026-08-24; Gunjan Jaswal, @gunjanjaswal, named with consent in #11, 2026-09): `referenced` is not a semantic state but a weak estimator for influence with two named failure modes (cite-without-use, use-without-cite); the spec keeps exactly two states — availability (context fact) and influence (behavioral fact, experiment layer). Added a third reviewer's provider inference boundary (direct reply 2026-08-24, independent convergence; David Turner, @atomicdjt, named with consent, 2026-09): client-side telemetry certifies `serialized-as-sent`, one step short of `reached-inference`. Ladder diagram replaced with the two-state/evidence-rank form. Issue #11 body and normative ontology updated to match; reviewer attribution across DR-005/DR-006 switched to anonymous-pending-consent (first named consent recorded 2026-09-07, DR-005 header updated to match).
- **First public evidence case — [langfuse-demo-traces](conformance/evidence/langfuse-demo-traces/)**: three real framework-instrumented traces published by Langfuse as demo seed data (commit-pinned, source not redistributed), run through the canonical pipeline via a disclosed one-off adapter. Result: 12 attempts, operation grouping evidence 100% ungrouped, **safe operation coverage 0%** in both fail-closed and structural-experimental modes; 4 sibling patterns where retry and loop step are observationally indistinguishable; token usage absent from the export (0/26). Fully reproducible (`fetch_source.py` + `run_case.py`, stdlib only). Claim boundary: External Fixture — numbers describe these three traces only.
- **Website: dual-path trial CTA + recent-evidence card** (deployed): primary CTA is a mailto with prefilled subject and three screening questions (capability / traffic volume & runtimes / decision it informs); the GitHub issue template demoted to the secondary path (issue forms are a dead end for executive buyers — submissions stood at 0). New "Recent evidence" card surfaces the 0%-coverage langfuse case in the trial section. Research note published at `/blog/context-availability.html` (content track #1, linked from the footer).
- **CI**: external-fixture guards + lab suite wired into `conformance.yml` (lands the v0.2.2 pending item); new `status-sync` suite — `scripts/gen_status.py` regenerates `website/status.json` from repo state (spec version from `standard/CORE.md` header + max patch, SDK version, test count, profile count) after a green run on main, commits and mirrors it to `gh-pages` (`website/**` is outside the push path filter, so the sync cannot loop).
- **SDK distribution**: `agentmeasure-mcp-0.1.1.tgz` (21/21 tests) attached to the v0.2.2 GitHub Release; install-via-release-URL verified end-to-end. Registry publish remains blocked on credentials (`npm adduser`).

### Fixed

- **dashboard: legacy packs rendered again.** Dispute packs generated before the three-state vocabulary carry no `three_state_counts`; the dashboard KeyError'd on them. It now computes PASS/FAIL/UNPROVABLE from the directional counts when the field is absent (old packs render; new fields stay additive).
- **crosscheck: duplicate conversation ids in the export are named, never silently swallowed.** The join kept the last row per id with no trace; the result now carries `duplicate_ids_in_export` and the report prints a DUPLICATE line — a repeated id is a metering anomaly in its own right.
- **crosscheck: negative ledger amounts refused** with the reason spelled out — a credit or adjustment belongs to the recovery ledger, not the charges ledger; mixing them would corrupt the charged total.
- **lab: AM-U-007 (#12) — `median_steps_per_operation` mixed assignment grain into an operation-grain metric.** Found by @jaden3824 in the second external conformance vector: per-operation step totals [3, 2] reported 5.0 with denominator 1, because steps were summed per assignment and the median taken across assignments. Steps are now summed per resolved operation, the median runs over reconciled operations only, and the denominator is the number of contributing operations (2.5 over 2 in that vector; unresolved or failed-reconciliation operations contribute nothing, fail-closed). Single-operation behavior (urusilla-001) is unchanged; 84 lab tests and both external guards pass.

## [v0.2.2] - 2026-08-24 — conformance hardening (#8 / #9 + first external vector)

- **Fix #8 — validator: root sibling constraints after oneOf** (found in the first external conformance pass, [langfuse#16383](https://github.com/langfuse/langfuse/discussions/16383)): the bundled validator returned after evaluating `oneOf`, so records matching one branch but missing root-level required fields (siblings of the root `oneOf` in FMT-002) validated successfully. `oneOf`/`anyOf` are now compositional — sibling keywords are always evaluated after a branch matches; zero-match errors carry the first branch error for diagnosis. The registry validator additionally fails closed on unsupported composition keywords instead of silently ignoring them.
- **Fix #9 — aggregator: reconcile declared operation summaries against attempt rows**: the lab aggregator trusted `operation_result` declarations (`attempts`, `outcome`) without checking the underlying `attempt` rows. Every declaration is now reconciled — declared count vs actual rows, declared outcome vs the rows' derived outcome (rule op-success-any/1, last-attempt outcome disclosed) — and mismatches surface as an explicit `operation_reconciliation: failed` block. Counts and outcomes use **measured rows, never declarations**. The canonical collector gained the same discipline: `task_outcome` payloads now persist `task_success`/`attempt_count` and `compute()` emits `operation_summary_reconciliation`.
- **First external conformance vector accepted upstream** — [Urusilla-001](conformance/vectors/external/urusilla-001/) (project-authored synthetic fixture by @jaden3824, per the #8/#9 commitment): schema validity, #8 root-sibling mutation guard, exact metric reproduction, and #9 tamper guards via `conformance/runners/run_external_fixture.py`. Claim boundary preserved: synthetic evidence, not an endorsement or external reproduction. The external-fixture runner and the full lab test suite run in CI (`lab/**` now triggers workflows; guards execute on every conformance run).

## [v0.2.1] - 2026-08-22 — codex adapter live-validated

- **Live validation on a real harness** (codex-cli 0.149.0-alpha, gpt-5.6-sol): first preregistered controlled A/B on a real agent — 4 tasks × 2 variants (description clarity), full funnel (reach/choice/success/consumption), **real token metering** (188K–215K tokens/op measured), steps proxy, honest null with next-round sizing (+25pp observed, n=4/arm, p=0.29 → "plan ≈31/arm"); report verified (`am lab verify` PASS), bilingual one-pager + live disclosure in place.
- Adapter fixes found by the live run (why live validation matters): MCP tools arrive as `mcp_tool_call` items with `server`/`tool` fields (not `function_call`); MCP calls need `--approve-for-me` in exec mode (otherwise "approval policy is never" denies them); stdin must be DEVNULL; **toolserver contract bug** — the runner's spec carried `id` where the server requires `name`, crashing `tools/list` so agents saw "server failed to start" (now covered by a contract regression test); candidate-set steering in the episode prompt (a real harness otherwise reasonably prefers its built-in search).
- codex runner: token usage metered as cost units (1 unit = 1 token, amortized per attempt); `codex_config` / `extra_args` passthrough; `--ephemeral` by default (no session pollution); `claude-code` benefits from the shared fixes (DEVNULL, steering, contract) and remains scripted-transcript-tested pending its own live run. 78 tests (+4).

## [v0.2.0] - 2026-08-22 — Whitepaper v0.3 + AgentMeasure Lab v0.4 (open experiment engine)

- **Lab v0.4 — real harness runners (LAB-004) + experiment history (G6 local)**: `claude-code` adapter — runs the real harness headless (`claude -p --output-format stream-json --mcp-config`) against a **controlled candidate set** injected through a per-episode local MCP tool server ([`toolserver.py`](lab/verovo_lab/toolserver.py), pure stdlib; presentation varies per variant); `codex` adapter (experimental, `codex exec --json`, App Server surface future work); transcript→funnel parsing with honest limits (choice from transcript, success from tool_result, consumption = continuation proxy, latency/cost placeholders — disclosed in every report); integration-tested end-to-end against scripted CLI transcripts (tests/fake_claude.py / fake_codex.py) — **live-CLI validation pending, first live runs are validation runs**; `am lab history` — local experiment/hypothesis library (every run with date, verdicts, prereg hash, fingerprint); `run.json` now records `created_at`; baseline funnel section in [product/local-analytics.py](product/local-analytics.py) (BASE0-002: attempts → resolved operations → success, with explicit "not observable provider-side" boundaries); 74 tests (14 new).
- **Lab v0.3 — business-POC-driven release hardening**: fake growth now blocks the decision exit — a significant selection uplift whose consumption drop is significant or material (>5pp) is verdicted `unverified_growth` with a "do not ship: fix consumption first" recommendation (previously the warning existed but the verdict still said adopt); the check is significance-aware, so within-noise consumption dips no longer false-flag; **dominated candidates are called out** (no more money at higher cost → "pick the other one"); **null results carry next-round sizing guidance** (≈n per arm to resolve the observed effect — a boss must not read "null" as "no effect"); reports open with a **bilingual decision-maker one-pager** (conclusion / uplift / monthly margin / certainty / recommended action, no statistics vocabulary); `preregister` prints a **scale / power / budget preview** (per-arm n vs minimum, n needed for +2/+3/+5/+8pp at an assumed baseline, budget caps + worst-case spend — LAB-002 acceptance and NFR-COST-001 now met); CLI errors print **one clean line** instead of tracebacks (`AM_LAB_DEBUG=1` restores them); task-set paths fall back to the shipped corpus; run output defaults to a predictable cwd-relative `runs/`; mock harness default effect amplitudes cut to **realistic literature scale** (single-digit pp, echoing Hasan et al.) so demos cannot flatter the engine; MCP advice now carries plain-language verdicts, verified monthly margin, dominance and power notes; 60 tests (7 new POC regression tests encoding boss-level acceptance criteria).
- **Lab v0.2 — calibration loop + MCP interface**: production re-test analysis ([`am lab calibrate`](lab/README.md), CAL-002/003) — offline vs production uplift under the same preregistered plan, per-condition (harness × task stratum) transfer effects with intervals (never a single global coefficient), verdicts `production_confirmed` / `direction_mismatch` / `transfer_not_established` / `not_comparable` (gap named, never assumed), matrix-reweighting suggestions (CAL-004 minimal); connector data plane (`am connector`, CAL-001) — local-first aggregation, three-tier per-class authorization (`off`/`local`/`export`), immediate revocation, HMAC-signed aggregate-only exports (schema-level: no per-assignment rows, no content); read-only MCP query interface (`am mcp serve`, LAB-009) — `get_run_summary` / `get_presentation_advice` (evidence grades, guardrails, production-verification status) / `get_funnel_metrics`, no rankings, no competitor data; calibration-report schema; [FMT-004 mapping doc](lab/docs/CORE-MAPPING.md) (Core 0.4.4 ↔ experiment formats + 2 reference scenarios); synthetic production-event generator for demos; 53 tests. Real production ingestion still gated on G0 data rights — validated on synthetic rollouts with planted transfer, disclosed as such.
- **Lab v0.1 — open experiment engine** ([lab/](lab/README.md)): end-to-end preregistered experiments (M1 synthetic-harness scope) — CLI (`am lab init/preregister/run/report/verify/selftest`), enforced preregistration lock (hash), balanced blocked assignment, seed determinism (same seed → same run fingerprint), budget circuit breaker (safe stop, data kept, honest "incomplete" analysis), Reach→Choice→Success→Consumption funnel capture with Operation/Attempt semantics, honest statistics (Wilson/Newcombe intervals, two-proportion tests, honest nulls, "undetermined" with required n), guardrails (`effective_not_qualified` verdict), fake-growth warning, value-formula bridge, offline HTML/JSON reports with Measurement Labels; open formats FMT-001/002/003 ([lab/schemas/](lab/schemas/)); synthetic vertical task corpus v1 (36 tasks, 3 tiers); 30 tests + built-in selftest; synthetic mock harness with planted ground truth (disclosed in every report) — real harness runners (claude-code/codex) are plugin-interface pending
- **Whitepaper v0.3** (EN + zh-CN): experimentation & calibration as first-class measurement semantics (§8 — preregistered loop, guardrails, per-condition effect sizes, offline→production transfer); anti-fake-growth elevated to a core thesis with the x402 wash-trading evidence (§2); data-rights claim tiers in the observation-surfaces chapter (§10); value formula bridging measurement to margin (§7.5); new evidence (Hasan et al. arXiv 2602.14878, BiasBusters ICLR 2026, Arcade ToolBench, AAIF/A2A/AgentCore/AP2 infrastructure); zh-CN now structurally in sync with EN (missing §0.2/§0.5 added); references updated; version pinned to Standard Draft 0.4.4
- **Harness-native direction**: harness profiles for [Codex](profiles/codex.md), [Claude Code](profiles/claude-code.md) and [DeepSeek Harness](profiles/deepseek-harness.md) (what each runtime can/cannot observe, mapped to AgentMeasure semantics); [Experiment D — cross-harness compatibility](experiments/EXPERIMENT-D-cross-harness-compatibility.md) (one fail→retry→fallback→success task across three harnesses); [Proposal: Delegation](proposals/2026-08-21-delegation-graph.md) (fourth semantic object for agent-to-agent delegation); whitepaper §1.5 "Harness-native Software and the Measurement Problem" (EN + zh-CN)
- **Product**: [product/AUDIT.md](product/AUDIT.md) — the 7-day capability performance audit, productized (design-partner offer, founding-audit tier, FAQ); README (EN/zh-CN) now links it as the front-door offer
- **Roadmap**: standard track under a change timebox until 2026-09-04 (non-blocking AUPs queued); product track (Product Gate A) is the critical path
- **Product Gate A in progress**: first external Provider onboarding ([#2](https://github.com/roy-tong/AgentMeasure/issues/2)), first benchmark design discussion ([#3](https://github.com/roy-tong/AgentMeasure/discussions/3))
- README: added status badge row (CI / spec draft / release / license / discussions, EN + zh-CN); status section now shows Draft 0.4.4

## [v0.1.1] - 2026-08-17 — External-Ready convergence

The review round that makes the SDK safe to hand to the first external provider.
The demo is now a reproducible experiment, the SDK is non-blocking with real buffer
accounting, caller identity is per-request, and the benchmark uses the standard's
own Evidence Profile instead of a parallel ladder.

### Fixed

- **Demo reproducibility**: `examples/demo-e2e.sh` now runs in an isolated
  `mktemp -d` workspace and never touches `~/.agentmeasure`; the fixture is labeled
  `usage_context=synthetic` at the source; same fixture + same policy = same result
  (42 calls → 84 observations, every run)
- **Removed invented `result_consumed`** from the provider example: a provider-side
  MCP server cannot observe whether the agent consumed the result —
  UNOBSERVABLE is not fabricated as TRUE (42 calls → 84 observations, not 126)
- **SDK is non-blocking**: `emit()` only enqueues; a background flusher batches
  writes to rotating spool files (default 5 MiB, keep 7); `BUFFER_LIMIT` (10,000)
  is now enforced with explicit loss accounting (`bufferHealth`: queueDepth,
  droppedTotal, droppedSinceLastFlush, flushFailures, spoolBytes, rotatedFiles);
  loss numbers are stamped into each persisted batch's `collection_health`
- **Caller per-request**: `wrapTool(..., { getCaller })` resolves the CallerClaim
  from the request context (v2 `_meta.clientInfo` / v1 `_meta.sessionId` echo);
  the server-level caller is a fallback for fixtures only
- **Lineage snake_case**: `operation_id` / `task_id` / `retry_of` per payload
  schemas; camelCase aliases map (deprecated); previously a runtime-provided
  camelCase field would produce a schema-invalid observation
- **Local analytics**: `--days` is honored (was hardcoded 365); latency histogram
  (p50/p95/buckets from non-sensitive `duration_ms`); caller attribution by runtime
  and identity strength (was "needs histogram" / observer-count placeholder)
- **CI**: `sdk/**` `examples/**` `benchmark/**` `reports/**` added to triggers;
  new `sdk-gate` job (npm ci → build → 21 unit tests → canonical-boundary pipeline
  gate); core tests rewritten to feed **canonical JSONL only** (no more
  `store_observation` + legacy `lifecycle_stage` bypass)
- **MCP SDK v2 support**: `@modelcontextprotocol/server` (stable 2026-07-28 line)
  is the primary adapter path (`mcp-integration-v2.js`); v1 remains as a
  compatibility path; the SDK itself is MCP-version-agnostic (peer dep)
- **Benchmark methodology**: E0–E5 single ladder and A/B/C/D composite scores
  removed — claims are profiled with the multi-axis Evidence Profile
  (`standard/TRUST.md` §3); "Context" renamed to Observation Surface / Provenance
  (CORE `usage_context` keeps its own meaning); output is a Measurement Claim
  Label; machine-readable `benchmark/claims/*.json` with snapshot discipline;
  AgentMeasure's own numbers moved out of the ranking (reference fixture only);
  overclaims in Run #001 corrected ("every number is self-reported" →
  "the dominant pattern is self-reporting"; "Independence beats volume" →
  "Independence strengthens corroboration; it does not substitute for coverage")
- **License unified**: SDK package is MIT (was Apache-2.0) with its own LICENSE,
  matching the repo and the release
- **Release copy correction**: "no self-reported numbers" overstated — callee-side
  measurement means *provider-observed rather than caller-self-reported*; stronger
  claims still require independent corroboration or attestation (TRUST §3)

### Added

- `schemas/validate_jsonl.py` — single-source canonical JSONL validator (SDK tests
  and CI share it)
- `scripts/verify_sdk_pipeline.py` — External-Ready pipeline gate
  (SDK fixture → JSONL → validate → ingest → metrics → expected output)
- `scripts/canonical_core_gate.py` — core invariants through canonical ingestion only
- `sdk/test/` — 21 tests: schema / fail-open / privacy / lineage / caller /
  concurrency / buffer / mcp-integration (v1 + v2)
- `benchmark/claims/*.json` — machine-readable claim records for Run #001

### Changed

- `reports/measurement-report-001.md` → `reports/pipeline-validation-001.md`
  (42/84 honest baseline; **Measurement Report #001 is reserved for the first
  external provider**)
- `@agentmeasure/mcp` v0.1.1 — package.json: MIT, devDeps vs peerDeps, `files`
  includes LICENSE

### v0.1.1 RC → External Alpha（本版追加）

- **Vocabulary single source of truth**: `registry/vocabularies.yaml` →
  `scripts/gen_vocab.py` regenerates the observation schema enums and the
  vocabulary table, and CI-checks TS unions + Python tuples (`--check`).
  `usage_context` now includes `demo`; `validity_source` now includes
  `provider_configuration` — the CORE/schema/TS/Python drift is closed
- **Validity loophole closed**: providers can only claim validity values they
  can know (`health_check` / `load_test` / `suspected_invalid` / `duplicate`);
  `normal` is collector-derived. `validity_source=provider_configuration` is
  never strong qualification (collector core-5 gate)
- **Benchmark Draft 0.3**: Authentication strictly per TRUST (A0/A1/A2 —
  "attributable" is no longer A1); new orthogonal Source Attribution axis;
  Benchmark `Qualification` renamed **Claim Completeness** (Qualification is
  returned to Measurement Core); primary-source rule added and demonstrated —
  claim #003 corrected: primary source is **Ahrefs** (server-log/web-analytics
  telemetry over 137,210 domains, May 2026), not originality.ai / independent
  crawl; claims regenerated with real excerpt+sha256 snapshots
- **Demo fully deterministic**: fixed failure pattern (callIndex%8==0 → 6/42)
  and fixed latency sequence (20+(i*37)%280) via wrapTool `durationMs` override
  (fixture-only; production records measured wall time). Two runs are now
  bitwise-identical in semantic output; pipeline gate asserts success 0.857 and
  the exact duration set
- **Packaging**: `peerDependencies` removed — the SDK is MCP-version-agnostic
  (examples/tests depend on v1/v2 packages themselves); `spoolFileName` option
  with multi-process guidance; spool dir 0700 / files 0600; optional
  `handleSignals` (SIGTERM/SIGINT drain) with documented crash boundary
  ("up to one flush interval may be lost on abrupt termination");
  `npm pack` + clean-install verified in a fresh project
- **Outreach v2**: Pool A/B/C/D segmentation (first 10 = Pool A only);
  email copy rewritten from "we answer both" to
  "we show you what can be known — and what cannot"; Private Alpha default /
  Public Alpha opt-in modes for first-wave providers; no `npm install`
  instruction until the package is actually published

## [v0.1.0] - 2026-08-16

First public release: the measurement layer for the Agent Capability Economy.

### Added

- **Standard (Draft 0.4.3)**: canonical observation schema + 6 payload types, qualification resolution, metrics registry (14 metrics, single source of truth), observe-first policy with `unknown` defaults
- **SDK**: `@agentmeasure/mcp` v0.1.0 — TypeScript wrapper for official MCP SDK servers (fail-open, zero-content, registration-time wrapping)
- **Product**: canonical end-to-end pipeline (adapters → observations → collector → metrics); `local-analytics.py` with six classes of metrics
- **Pipeline Validation #001**: honest baseline (42 synthetic calls → 84 canonical observations, 0 rejected, 0 qualified usage by design; the Measurement Report #001 number is reserved for the first external provider)
- **Docs**: whitepaper (EN/ZH), core spec, quality dimensions, commercial semantics, roadmap, governance
- **Community**: Discussions with 5 categories; issue templates (Metric Semantics / Observation Gap / Discrepancy / Proposal)

### Notes

- `0 stars at launch` is a feature of the honesty-first posture, not a bug.
- Synthetic traffic only so far; every limitation is stated in the report.
- 2026-08-17: v0.1.1 tag pushed; CI workflow fixed (quoted step name)
- 2026-08-17: release automation live — GitHub Release v0.1.1 created and
  v0.1.0 marked superseded by `.github/workflows/release.yml` (GITHUB_TOKEN);
  `agentmeasure-mcp-0.1.1.tgz` attached as a release asset (providers can
  install without npm: `npm install <release-download-url>`); npm publish is
  automated and activates automatically once an `NPM_TOKEN` secret is added
  to the repository (job runs build + tests before publish; `--access public`).
  No npm credentials exist on the dev machine (npm whoami: ENEEDAUTH).
