# v0.11.0 — the audit case contract, hardened

CLI release 0.11.0. Provider SDK stays 0.3.0 (`@verovo/mcp`).

This release adds the buyer-side Audit Case: a period, its confirmed contract
rules, and findings that separate what is proven, what is unprovable and what
has actually been settled. It then hardens exactly the arithmetic a buyer
cannot afford to get wrong.

## What is in it

- **Audit case contract** (`schemas/audit-finding.schema.json`,
  `schemas/audit-case.schema.json`): findings carry classification, severity,
  a versioned rule reference with its confirmer, evidence references that must
  resolve inside the case root, and business status that only a reviewer
  advances. A finding that cannot be decided is `provable: false` with a reason
  and `amount.value: null` — never zero. A `realized` finding requires a
  settlement reference, because acceptance is not settlement.
- **Builder and validator** (`scripts/build_audit_case.py`,
  `scripts/validate_audit_case.py`): deterministic rule handlers keyed to
  confirmed `rule_id`s; a standard-library validator for the full contract.
- **Financial correctness hardening**, each pinned by mutation tests against a
  previously failing sample (`tests/test_audit_mutation.py`):
  - all money is `Decimal` end to end and serializes as two-decimal strings
    (`"44.00"`); the schemas and validator refuse JSON numbers in amount
    fields, so binary float rounding cannot enter an audit case;
  - contract rule parameters (unit prices, discount rate/threshold,
    allocation) are data on each confirmed rule; a missing parameter fails
    closed, never a built-in default;
  - settlement is evidenced by application (credit `applied: true` plus
    `applied_at`), not by the existence of a credit note;
  - coverage counts each affected invoice line once (its worst open finding),
    never a sum over findings sharing a line, and no amount is ever parsed out
    of human-readable text; `verified + unverified + settled` must reconcile
    to the invoice total or the build fails.
- **Audit golden case** (`examples/audit-golden-case/`): synthetic bundle with
  eight independently expected findings across every classification and the
  USD 1,686 / 394 coverage split.

## Install

```bash
pipx install "git+https://github.com/roy-tong/verovo@v0.11.0#subdirectory=healthcheck"
verovo demo
```

PyPI publication of the `verovo` project is pending a one-time trusted
publisher setup on the PyPI side; until it completes, the pinned Git install
above is the supported path. The previous `agentmeasure` project stays at
0.8.0.

## Verification

- 727 tests pass locally (Python 3.9), including 54 audit tests and 11
  mutation tests that change contract parameters, double-flag a line, issue
  credits without applying them, and check Decimal/split identities.
- CI: healthcheck matrix (3.9/3.11, Windows), buyer-ledger, spec conformance,
  SDK gate and release smoke.

## Boundary

An audit case organizes what the evidence supports; it does not decide the
dispute. Issued is not applied, acceptance is not settlement, and a computed
coverage number is not a recovered amount.
