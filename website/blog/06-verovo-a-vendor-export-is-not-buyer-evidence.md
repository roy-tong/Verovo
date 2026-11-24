# Verovo: a vendor export is not buyer evidence

*Control domains, procedural provenance checks, outcome observer grades, canonical completion axes, and evidence-only handoffs.*

Two CSV files can contain identical rows and still fail to reconcile anything meaningful. If the same vendor produced both exports, the files establish consistency inside one control domain. They do not establish independent confirmation by the buyer.

This is the control-domain problem, and it sits underneath AI billing, delivery acceptance, and task economics. A buyer-side verifier needs to know more than whether `amount_a == amount_b`. It needs to know who controlled each source, who collected it, how it was confirmed, and whether the claimed acceptance identity has any authority.

Verovo makes those relationships explicit. Its buyer-evidence checks are procedural metadata checks, not cryptographic identity proof. That is a narrower and more useful claim: inconsistent provenance can be rejected deterministically instead of being upgraded by a plausible filename or a `verified=true` field.

## Buyer provenance is a conjunction, not a label

The provenance vocabulary contains eight normalized fields:

```text
source_party
source_affiliation
control_domain
collector_party
collection_method
independence
confirmation_process
source_path
```

`source_association` and `collected_by` are accepted as aliases for affiliation and collector, then projected into the canonical names. Missing values become `unknown`.

The current buyer-evidence review requires all of the following:

- `source_party`, `source_affiliation`, `control_domain`, and `collector_party` are exactly `buyer`;
- `independence=buyer_independent`;
- a non-empty `source_ref` exists;
- collection is not `ocr`, `invoice2data`, `self_declared`, `self_attested`, or unknown;
- confirmation is not self-declared, self-attested, or unknown;
- none of the source, path, collection, confirmation, affiliation, domain, or collector fields carries an explicit vendor-control marker.

In simplified form:

```python
qualified = (
    all(field == "buyer" for field in PARTY_FIELDS)
    and independence == "buyer_independent"
    and source_ref
    and collection_method not in UNQUALIFIED_METHODS
    and confirmation_process not in UNQUALIFIED_CONFIRMATION
    and not any_vendor_control_markers
)
```

A `buyer` row under a manifest path containing an explicit vendor marker fails closed. A vendor path cannot be relabeled as buyer independence. Two vendor-controlled exports may support `Vendor Reconciled`; they cannot support `Buyer Verified`.

The output also distinguishes `independence_assertion` from `independence_basis`. A clean result is only `procedural_metadata_only`. This is not identity cryptography, and the implementation says so rather than treating metadata as a signature.

## Outcome class and observer grade are separate axes

Outcome verification has the same coercion problem as source independence. “Resolved” is not one homogeneous unit.

A public synthetic fixture models a per-resolution dispute with ten records:

```text
outcome_class:
  resolved            4
  assumed_resolved    3
  escalated           2
  abandoned           1

observer_grade:
  affected_party      5
  self_attested       5
```

I copied the fixture to a temporary directory and ran its shipped reproduction. The actual output was:

```text
Provider headline: resolved + assumed_resolved
  7/10 = 70%

Audited: resolved + affected_party attested only
  4/10 = 40%

Gap: 30 percentage points
UNPROVABLE: 10/10 records carry no incrementality evidence
REPRODUCED
```

The four audited records require both `outcome_class=resolved` and `observer_grade=affected_party`. The three assumed resolutions do not enter that denominator. Escalations do not become resolutions regardless of observer grade.

The final line is equally important: none of the ten records contains incrementality evidence, so the fixture cannot prove that the AI caused the resolution. That does not make the count zero. It limits the claim to the specified outcome and observer definitions.

The [case runner](https://github.com/roy-tong/Verovo/blob/main/conformance/evidence/assumed-resolution/run_case.py) computes both rates and generates the settlement disclosure through the shipped code. It models a dispute shape; it is not Intercom customer data or an accusation about a supplier.

## Period intake is a join with explicit loss reporting

A canonical period joins five kinds of input:

```text
tasks.csv      task identity and type
runs.jsonl     execution identity and task linkage
usage.jsonl    technical usage and optional financial amount
bills.csv      supplier charge representation
outcomes.csv   delivery and business outcome evidence
```

The intake layer preserves gaps rather than hiding them in totals:

| Input condition | Intake behavior |
| --- | --- |
| Repeated `run_id` or `usage_id` | Later row ignored and duplicate opened |
| Repeated `charge_id` | First booked representation kept, duplicate reported, completeness damaged |
| Cost without currency | `financial_status=unknown`; case currency is not assumed |
| Financialized usage without cost | `unpriced` plus `missing_amount`, never zero |
| Pricing rule without quantity | `missing_quantity`, never zero |
| Currency different from case | Row excluded and named; canonical run rejects the mixed-currency case |
| Bill or usage without task linkage | Cost remains unallocated rather than equally distributed |
| Shared run linked to multiple task candidates | No single task is chosen silently |

One vendor, one account, and one currency are hard canonical scope constraints. A matching bill cannot bypass the single-vendor check. An approved pricing rule can recalculate a charge, but rule repricing and cross-source reconciliation remain different axes.

The implementation also indexes task, run, and outcome coverage sets once rather than rescanning every source row for every task. The 4,000-row synthetic benchmark in the release notes fell from about 0.66 seconds to 0.071 seconds with identical coverage output. That optimization is useful because it did not change the evidence semantics.

## Completion has four axes, and FAIL can be complete

Canonical finance does not return one universal health score. It returns four completion statuses:

- `finance_status`: are financial records, reconciliation gaps, and cost allocation closed?
- `verification_status`: do applicable outcomes have decisive buyer-procedural verification?
- `delivery_status`: does every task have decisive Delivery Accepted PASS or FAIL evidence?
- `economics_status`: are applicable outcome economics, including cost and success/value evidence, complete?

An omitted outcomes source makes verification, delivery, and economics `not_applicable`; it does not make a finance-only period partial. A present but empty outcomes source cannot hide the economics gap.

A decisive delivery `FAIL` is complete evidence. The buyer has enough information to reject delivery; uncertainty is not the same as a negative result. This rule prevents the reporting layer from treating only favorable outcomes as finished work.

ROI is also separate from Cost per Verified Success. Missing verified value can leave ROI null while cost and verified-success cost remain computable. An unverified vendor outcome is unknown, not a success and not a failure.

## Acceptance identity comes from a controlled organization

Delivery acceptance needs more than an `accepted_by` string. The manifest must declare a controlled buyer organization with `org_id` and a non-empty `acceptance_signers` list.

For every acceptance row, canonical finance checks:

1. the controlled organization exists;
2. the signer is not the vendor;
3. the signer belongs to the authorized signer list;
4. any declared `accepted_by_org` matches that organization's ID or name.

A forged buyer label, unknown organization, vendor self-acceptance, or missing authority produces `UNPROVABLE`. A buyer-verified success also needs `accepted_by` and `accepted_at`; missing timestamps produce `UNKNOWN`.

There is an additional cross-check: if a task has an unqualified acceptance row, a charge-level Buyer Verified `PASS` for that task is downgraded to `UNPROVABLE`. A clean arithmetic result cannot compensate for unauthorized acceptance on the same task.

This is procedural authority rather than self-description. The manifest's controlled organization is the credential boundary recognized by the implementation.

## A hash proves handoff integrity, not external truth

Growovo can hand execution and outcome records to Verovo. The importer verifies the handoff schema, recomputes SHA-256 over the body excluding `integrity_sha256` and `generated_at`, rejects credential-like fields recursively, and requires:

```text
booking_status = evidence_only_not_canonical
external_action_executed_by_export = false
```

The imported evidence record then states:

```text
source_integrity_verified = true
integrity_meaning = unchanged_json_body_not_external_truth
receipt_verified_by_verovo = false
business_outcome_observed_by_verovo = false
payment_authorization = false
terminal_state = DELIVERED_WITH_GAP
premises_do_not_upgrade_grade = true
```

This is a precise integrity boundary. The hash shows that the JSON body has not changed since Growovo computed it. It does not prove that an external action happened, a supplier accepted delivery, or revenue exists. Booking requires separate source mapping and review.

The same distinction appears in Provider Manifest admission. A local document can declare engine identity, process executable hash, resource limits, readiness checks, coverage, cleanup, and raw snapshot digests. Required failed or unknown checks refuse admission; optional ones degrade it; unreleased cleanup refuses it. The result still has `live_engine_contacted=false`. A well-formed adapter contract is not a live integration test.

## Reuse means preserving definitions across periods

A second accounting period should reuse provenance requirements, source mappings, outcome classes, observer grades, and completion semantics. It should not reuse the previous period's acceptance or independence claims automatically.

The useful comparison is therefore more than amount deltas:

- which sources changed control domain or collector;
- which links became missing or ambiguous;
- which outcome denominators changed definition;
- which acceptance signers lost or gained authority;
- which completion axes changed;
- how much human work was needed to resolve new gaps.

I ran the task-economics, period-intake, coverage, and canonical-finance suites for this article: 54 tests passed. They cover duplicate identities, cross-currency and cross-account scope, missing amounts, vendor-controlled relabeling, forged buyer strings, vendor self-acceptance, empty outcomes, decisive delivery failure, unpriced usage, and unallocated shared cost.

The implementation's central constraint is conservative but enabling: evidence from different parties can answer different questions, and a missing answer can remain visible without blocking every calculation. A buyer can reconcile one charge, leave delivery unresolved, and still know exactly which evidence must arrive next.

### Source and verification links

- [`task_economics.py`: provenance and buyer-evidence qualification](https://github.com/roy-tong/Verovo/blob/main/healthcheck/verovo_healthcheck/task_economics.py)
- [`period_intake.py`: source joins, exclusions, and coverage](https://github.com/roy-tong/Verovo/blob/main/healthcheck/verovo_healthcheck/period_intake.py)
- [`canonical_finance.py`: completion axes and acceptance authority](https://github.com/roy-tong/Verovo/blob/main/healthcheck/verovo_healthcheck/canonical_finance.py)
- [`growovo_handoff.py`: evidence-only integrity import](https://github.com/roy-tong/Verovo/blob/main/buyer-ledger/buyer_ledger/growovo_handoff.py)
- [`provider_contract.py`: local adapter admission](https://github.com/roy-tong/Verovo/blob/main/healthcheck/verovo_healthcheck/provider_contract.py)
- [Assumed-resolution evidence case](https://github.com/roy-tong/Verovo/blob/main/conformance/evidence/assumed-resolution/run_case.py)
- [Canonical-finance tests](https://github.com/roy-tong/Verovo/blob/main/healthcheck/tests/test_canonical_finance.py)
