# Verovo: implementation status

Reviewed 2026-11-04 against the **v0.13.0 release**. Available means local implementation, not production validation on every platform.

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

## Other implemented feature status

| Feature | Scope | Status |
|---|---|---|
| V01–V02, V05 | Usage adapters, supported bill recounts and charge review | Available for documented formats; no universal billing connector. `verovo intake` assembles a case from a manifest plus generic usage rows, bill CSV and outcome CSV, and records unmatched/duplicate charges instead of summing them |
| V03–V04 | Invoice extraction and normalization | Optional `invoice-intake` via invoice2data produces source-linked, unreviewed drafts (documents only, never booked). Intake reconciles bill and usage charges by `charge_id` and excludes currency mismatches; tax/credit/refund/FX normalization remains planned |
| V06–V07 | Task/run linkage and direct/shared allocation | Available with explicit IDs and versioned rules |
| V08–V10 | Business outcomes, verification flags and task costs | Outcome CSV ingestion requires a literal `verified` value; unverified or procedurally unqualified outcomes are kept but cannot count as success or ROI. Local case ingestion available; source truth requires review |
| ROI | Verified value / cost and cost per verified success | Available; incomplete input remains a coverage limitation. `verovo drivers` separates recorded from attributable cost and keeps unattributed cost visible |
| Optimization / assurance | Cost-driven workflow changes and risk pricing | `verovo optimize` proposes vendor/task/retry/allocation tests with explicit scenario arithmetic and a verification plan; every item needs a human and is never auto-applied. No causal savings or insurance claims |

Build: reconciliation semantics, task identity, allocation, outcome rules and economics. Reuse: existing telemetry/billing adapters; optional Docling/invoice2data and DuckDB remain integration candidates, not shipped dependencies. Optional invoice2data performs extraction into a non-booked draft, and period intake reuses that draft without booking it; no vendored fork or mandatory core dependency.

The read-only/local `verovo_invoice_intake` MCP tool returns the same unreviewed, unbooked draft as the CLI; it never updates the economics case. `verovo_period_intake` returns the assembled case plus the intake report (open items, reconciliation, currency mismatches) and writes nothing.

Reliability behavior: largest-remainder allocation preserves cents and zero weights; explicit `charge_id` deduplicates usage/bill representations; duplicate outcome references do not multiply value; conflicting verified task states abstain; ROI requires a source and literal boolean verification. Zero verified value produces -100% ROI. Confirmed loss totals exclude estimates and duplicates.

## Verification in this working tree and remaining boundaries

- PASS: 592 healthcheck unit tests via repository-root discovery, 59 root tests, 110 buyer-ledger tests, and `python3 healthcheck/verovo selftest`.
- PASS: metric vectors 22/22, outcome-audit vectors 15/15, delegation vectors 23/23, SSE vectors 2/2; external fixture runners, receipt/correlation vectors, aggregate-join, replay-digest, commerce-retry and conformance-pack selftest. Metric registry and generated vocabulary checks also pass.
- The canonical fixture and new adversarial cases are synthetic. No real customer close, supplier dataset, production identity flow, external signature, cryptographic attestation or OCR accuracy was validated.
- Supported canonical scope remains known CSV/JSONL, one vendor/account/currency, approved per-unit pricing and approved allocation drivers. Unsupported formats and cross-scope input fail closed. General contract parsing, arbitrary invoice layouts, tax/credit/refund/FX handling, external business-system confirmation and real-world independence remain outside the verified boundary.
