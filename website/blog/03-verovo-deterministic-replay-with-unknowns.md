# Verovo: deterministic replay for AI charges, with the unknowns intact

*Decimal arithmetic, counting grain, content-addressed revisions, OCR integrity, and line-level buyer disposition.*

A public synthetic fixture for claude-hud contained two million one-hour cache-write tokens for a model priced at $3 per million input tokens. The implementation priced them at the five-minute multiplier:

```text
2,000,000 / 1,000,000 × $3 × 1.25 = $7.50
```

The applicable one-hour multiplier was 2×:

```text
2,000,000 / 1,000,000 × $3 × 2.00 = $12.00
```

The difference was $4.50, a 37.5% understatement on that component. The parser already exposed the cache-tier split; the cost path discarded it. The fix was merged as [claude-hud PR #758](https://github.com/jarrodwatts/claude-hud/pull/758).

That fixture was not a customer invoice or recovered money. It demonstrates a more general engineering problem: an AI charge is the result of event identity, counting rules, price versions, time windows, and arithmetic. A total without those inputs is difficult to review. Verovo's implementation is an attempt to make each of them replayable while keeping delivery and buyer acceptance outside the billing verdict.

## Start with a complete, small example

The public replay fixture contains two synthetic events:

```json
{
  "transaction_id": "synthetic-tx-001",
  "metric_code": "api_call",
  "occurred_at": "2026-10-05T10:00:00+00:00",
  "ingested_at": "2026-11-02T00:00:00+00:00",
  "source_ref": "synthetic-events.jsonl",
  "source_hash": "sha256:1111…",
  "properties": {
    "task_id": "synthetic-task-1",
    "attempt_id": "synthetic-attempt-1",
    "vendor": "example-vendor",
    "vendor_record_id": "synthetic-vendor-row-1"
  }
}
```

The second event has a different transaction and vendor-record ID, one minute later. Both name the same task and attempt. The approved rule map says:

```json
{
  "metric_code": "api_call",
  "aggregate": "count",
  "unit": "call",
  "currency": "USD",
  "unit_price": "0.01",
  "price_version": "synthetic-price-1",
  "valid_from": "2026-10-01"
}
```

I built and verified the bundle, then inspected its manifest. The actual result was:

```json
{
  "counts": {
    "received": 2,
    "included": 2,
    "duplicates": 0,
    "conflicts": 0,
    "excluded": 0,
    "late": 0
  },
  "metrics": [{
    "metric_code": "api_call",
    "quantity": "2.0000",
    "unit": "call",
    "currency": "USD",
    "amount": "0.0200",
    "event_count": 2,
    "price_version": "synthetic-price-1"
  }],
  "charge_verdict": "PASS",
  "delivery_state": "UNKNOWN"
}
```

The important detail is the same `attempt_id` on two transactions. The fixture can support attempt-level identity coverage, but it does not infer that the two transactions were two logical operations. Attempt, operation, and delegation are different grains. Operation-level grouping requires additional evidence.

The identity report also showed:

```text
records=2
task_id=2
attempt_id=2
vendor=2
vendor_record_id=2
complete_for_attempt=2
retry_of=0
cache_hit_of=0
```

Zero retry links is a count of observed fields, not a claim that no retries occurred. `external_subject_id` is retained as a billing subject and is never used as a substitute for `task_id`.

## Replay is a sequence of refusals and calculations

The replay engine normalizes every event, applies an identity and rule map, emits line-level exclusions, aggregates only included lines, and computes a content-addressed revision.

### 1. Event identity and deduplication

`dedupe_key` defaults to `transaction_id`. If the same key appears again, the engine compares both `properties_hash` and `source_hash`:

- same key and same hashes: count once and record a duplicate;
- same key with different content: record a conflict and exclude the line with `DEDUPE_CONFLICT`;
- new key: continue through pricing and aggregation.

Conflicting events are not merged into a prettier history. A metric absent from the rule map produces `METRIC_NOT_IN_RULE_MAP`, with null quantity, price, and amount.

### 2. Time and rule eligibility

A period is half-open: `[start, end)`. An event outside that window receives `PERIOD_OUT_OF_WINDOW`. Prices have their own validity interval and `price_version`; an expired or not-yet-valid price produces `PRICE_NOT_VALID_FOR_OCCURRED_AT`. A missing price produces `PRICE_MISSING`, not zero.

Rule-map aggregation requires both `approved_by` and `approved_at`. Without them, the aggregation state is `RULE_MAP_NOT_APPROVED`. Late events are flagged and create a new replay revision; they do not rewrite an earlier signed close.

### 3. Aggregation

The engine supports `count`, `sum`, `max`, `unique_count`, `latest`, and `weighted_sum`. Each has different input requirements. A missing quantity blocks `sum` but not `count` or `unique_count`. A missing weight makes a weighted result null with `WEIGHT_MISSING`. Only `count`, `sum`, `weighted_sum`, and `unique_count` are priced by the current implementation; `max` and `latest` return `AGGREGATE_NOT_PRICED`.

For the two-event example, `count` ignores the absence of a quantity property, calculates two calls, and multiplies by `Decimal("0.01")` to produce `Decimal("0.0200")`.

### 4. Revision identity

The input fingerprint hashes a canonical object containing:

```text
period + closed_at + complete rule map + normalized events
```

Only the parser's positional `order` field is removed from normalized events. The revision ID is `ur-` plus the first twelve hex characters of the fingerprint's SHA-256 digest. This is not just a hash of the final amount: changing a rule, an identity property, a timestamp, or another input field changes the revision.

## Verification checks more than the final number

A reproduction bundle copies source inputs into `inputs/` and records each file's SHA-256 and byte length. Verification then checks:

```text
manifest schema and required fields
→ exact file set
→ every file hash and byte length
→ generator/algorithm version
→ replayed input_fingerprint and revision_id
→ replayed counts and metrics
→ complete semantic replay output
→ independently recomputed identity mapping
→ independently recomputed discrepancy classification
```

Incomplete manifests or missing evidence produce `UNPROVEN`; a complete but failing verification produces `FAIL`. The builder writes a staging directory and verifies it before replacing an existing output. A failed build leaves the previous bundle intact.

Public projection is a separate operation with its own permission rules. Only synthetic or explicitly approved `public-redacted` bundles can be published. The public copy removes private paths and row-level subjects. If private inputs are absent, independent replay is `UNAVAILABLE`, not implicitly successful.

The fixture's proof export produced exactly three checks:

```text
artifact-integrity   P1  PASS
replay-integrity     P1  PASS
external-result      P4  UNCHECKED
```

Usage arithmetic passed. External acceptance was not observed. Delivery remained `UNKNOWN`.

## Canonical proof uses a different replay contract

Usage replay and canonical finance are separate engines. The canonical `verovo proof export` command runs the finance model, records source hashes, and writes a semantic output fingerprint. `verovo proof replay` then applies this decision table:

| Condition | Result |
| --- | --- |
| Proof structure, projections, schema, or expected hash is invalid | `INDETERMINATE` |
| A required input is missing or its hash changed | `EXPIRED` |
| Inputs match but canonical output differs | `FAIL` |
| Inputs and canonical semantic output match | `PASS` |

The semantic fingerprint removes only `intake_report.generated_at`. Every other field in the canonical result participates in canonical JSON hashing. A changed allocation, verdict, coverage field, or buyer-related output therefore cannot hide behind an unchanged top-line amount.

Canonical proof also preserves independent conclusion types: rule repricing, invoice reconciliation, vendor reconciliation, buyer verification, and delivery acceptance. They do not inherit one another's proof levels. Two vendor-controlled exports can support vendor reconciliation; they do not become buyer-controlled evidence merely because the buyer downloaded both files.

## Money has more than one contract

Financial code tends to accumulate small rounding decisions until two displays disagree. Verovo's Money Contract centralizes task-economics display and allocation behavior:

- internal arithmetic uses `Decimal`, with non-finite or invalid strict input rejected;
- external display uses two-decimal strings and `ROUND_HALF_UP`;
- allocation uses the largest-remainder method so allocated cents sum exactly to the original total;
- zero-weight tasks receive no allocation.

For example, running the current implementation gives:

```text
display(0.005)                    → 0.01
allocate(0.05, weights a=b=c=1)  → a=0.02, b=0.02, c=0.01
```

The last example first floors each exact allocation to `0.01`, then distributes the remaining two cents by remainder. The invariant is `sum(allocated) == total`; it does not distribute a rounding error into an imaginary fourth task.

There is also a lenient parser for legacy display paths, which maps invalid input to zero. It is explicitly excluded from accounting calculations. That distinction matters as much as the rounding rule: a display fallback must not become a financial fact. Buyer Ledger's zero-decimal-currency tolerance and booking/allocation rules remain separate contracts rather than being mechanically replaced by the two-decimal display function.

## OCR approval is bound to bytes and revisions

OCR introduces two different risks: extracting a field incorrectly, and approving one document before booking another.

Each bill candidate records the source file's SHA-256. Review and booking reverify:

```text
raw source hash
+ OCR raw_text_sha256
+ OCR payload schema
+ OCR lines
+ reconstructed OCR text
```

A mismatch returns `STALE_SOURCE`, and booking checks the source again immediately before copying it. On platforms without `fcntl`, candidate locking fails closed with `UNSUPPORTED_CAPABILITY` instead of pretending to hold a cross-process lock.

Line arithmetic is recomputed at review and booking. Quantity × unit price must match the printed amount within:

```text
max(one smallest printed unit, 1% of |line amount|)
```

Unparseable quantities, prices, or amounts cannot be waived into a booked invoice with a reviewer note. This avoids the older zero-decimal-currency failure in which a scale-based tolerance could accept an invalid JPY or KRW line.

The period projection adds a byte-level consistency check. `CaseRepository` snapshots ten named state artifacts, including the job, scan, mapping, calculation, report, review, signoff, and revision. `PeriodCase.project()` checks recovery state, snapshots those bytes, builds the projection, then verifies that every byte is unchanged. Mid-projection writes return `STALE_SOURCE`.

Signoff binding is equally explicit. The current report and signoff must agree on:

- `revision_id`;
- report output fingerprint;
- calculation fingerprint;
- canonical snapshot fingerprint;
- revision metadata and signoff record contents.

Adding evidence after signoff creates a new revision. It does not mutate the signed report or let an old signature endorse new calculations.

## The spend-action projection has a conservative order

The recent `spend-actions` command turns an Audit Case into buyer work rather than another aggregate score. Its line-level decision order is:

| Condition in priority order | Action | Default owner role |
| --- | --- | --- |
| Open finding is unprovable, or classified as missing evidence, currency mismatch, or unverified outcome | `evidence_required` | Buyer operations, or finance for currency mismatch |
| Any other open finding | `review_required` | Procurement, or finance for shared-cost allocation |
| Realized finding, with no open finding on the line | `settled` | Buyer finance |
| Line explicitly listed in manifest `clean_charge_lines` | `ready_for_payment` | Accounts payable |
| No finding and no explicit clean confirmation | `unreviewed` | Buyer finance |

That last row is important: absence of a finding is not payment clearance.

Amount summaries remain grouped by action and currency, with `known_count`, `unknown_count`, and `known_value`. Unknown amounts are counted, not zeroed. The command never executes or authorizes payment; the buyer still records the actual payment, hold, evidence request, or vendor review elsewhere.

A companion Provider Manifest workflow returns local `admit`, `degrade`, or `refuse` from declared readiness checks, engine identity, resource limits, coverage, and cleanup state. It does not contact the engine. Admission says something about the declared engineering contract, not that production integration or purchasing is safe.

## The next period tests reuse without inheriting old passes

Cross-period reuse has a precise eligibility rule in `PeriodCase`. A pricing rule can be proposed only if its `valid_from` is on or before the period start and its `valid_to` is on or after the period's inclusive last day. Every reusable rule is emitted with:

```json
{
  "requires_re_review_next_period": true
}
```

A previous month's `PASS` does not cover this month's price. The useful assets are valid field mappings, rule references, acceptance criteria, and comparison definitions—not immutable approval.

A meaningful second period should therefore record which mappings were reused, which rules expired, how many new open items appeared, how many minutes humans spent on review and missing evidence, and what the buyer actually did. Replaying the same two events establishes determinism. Processing a new period under those constraints tests continued value.

## What the current implementation establishes

Verovo's strongest mechanism is not a universal “bill is correct” badge. It is a collection of narrower, inspectable invariants:

- conflicting events are not silently merged;
- missing price, quantity, identity, and evidence remain null or unknown;
- input and output revisions are content-addressed;
- money uses explicit arithmetic and allocation contracts;
- OCR review and signoff are bound to specific bytes;
- clean lines, unreviewed lines, and lines needing evidence remain different;
- replay success never becomes buyer acceptance.

Those invariants make the system useful before a universal benchmark exists. They also give a third party something concrete to attack: mutate one event, change a rule, edit an OCR artifact, alter a signed revision, or remove an identity field. The resulting `FAIL`, `EXPIRED`, `UNPROVEN`, or `INDETERMINATE` is part of the product, not an embarrassment to be hidden by a green dashboard.

### Source and verification links

- [Replayable-period fixture and commands](https://roy-tong.github.io/Verovo/examples/replayable-period.html)
- [`usage_replay.py`: event normalization, pricing, aggregation, and revisions](https://github.com/roy-tong/Verovo/blob/main/buyer-ledger/buyer_ledger/usage_replay.py)
- [`reproduce.py`: bundle verification and public projection](https://github.com/roy-tong/Verovo/blob/main/buyer-ledger/buyer_ledger/reproduce.py)
- [`proof_export.py`: canonical semantic fingerprint and replay verdicts](https://github.com/roy-tong/Verovo/blob/main/healthcheck/verovo_healthcheck/proof_export.py)
- [`money.py`: Decimal display and largest-remainder allocation](https://github.com/roy-tong/Verovo/blob/main/healthcheck/verovo_healthcheck/money.py)
- [`period_case.py`: snapshot projection, signoff binding, and cross-period rules](https://github.com/roy-tong/Verovo/blob/main/buyer-ledger/buyer_ledger/period_case.py)
- [`spend_action.py`: line-level buyer worklist](https://github.com/roy-tong/Verovo/blob/main/healthcheck/verovo_healthcheck/spend_action.py)
- [claude-hud PR #758](https://github.com/jarrodwatts/claude-hud/pull/758)
