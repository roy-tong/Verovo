# v0.13.0 — independent completion axes and evidence provenance

CLI release 0.13.0. Provider SDK stays 0.3.0 (`@verovo/mcp`).

This release separates financial completion from Task Economics, distinguishes
rule repricing from cross-source reconciliation, and hardens buyer evidence
against relabelled vendor records.

## What is in it

- **Four completion axes**:
  - `finance_status`: known financial records, cost allocation and
    reconciliation are closed without an open financial gap;
  - `verification_status`: applicable outcomes have decisive buyer-procedural
    evidence;
  - `delivery_status`: every task has decisive Delivery Accepted PASS/FAIL
    evidence;
  - `economics_status`: applicable outcome cost, verified success and value
    evidence are complete.
  A finance-only period can complete independently of Task Economics. The legacy
  top-level `status` is a compatibility summary, not a business PASS verdict.
- **`Rule Repriced` vs `Invoice Reconciled`**: approved-rule recalculation is not
  ledger reconciliation. `Invoice Reconciled` needs a distinct second source
  record; a bill-only repricing match is only `Rule Repriced`. `Invoice Matched`
  remains a labelled compatibility alias.
- **Buyer provenance**: source affiliation, control domain, collector party,
  confirmation process and physical source path are preserved. A
  `buyer_independent` label is an assertion; missing or conflicting provenance
  fails closed. Vendor paths and vendor-controlled confirmation cannot become
  buyer evidence.
- **No PASS masking**: a buyer-usage PASS does not erase a different vendor
  invoice amount; an Invoice Reconciled PASS does not erase a rule failure. Both
  directions have regression tests.

## Verification

- Healthcheck 592 tests, root 59, buyer-ledger 110 (761 total).
- Healthcheck selftest, registry and vocabulary checks, and the complete
  conformance runner suite pass.
- Python 3.9 and 3.11 healthcheck suites pass.
- Complex fixture reports finance complete, verification complete, delivery
  partial and economics partial, with ROI null where evidence is incomplete.

## Boundary

Provenance checks are deterministic metadata consistency checks, not
cryptographic identity or proof of actual independence. A disguised or
undisclosed vendor control path can still evade explicit string checks. Real
supplier data, OCR accuracy, FX, tax, credits, refunds, general contract parsing
and external signature verification remain unverified. The complex fixture is
synthetic, not a production customer result.
