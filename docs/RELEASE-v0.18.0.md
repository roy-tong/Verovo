# v0.18.0 — Reviewable buyer-close revisions

Verovo 0.18.0 completes the buyer-close revision path and hardens its signing boundary. Named sources can be added without silently ignoring bundle materials, a selected revision can be reviewed under the same lock as signoff, and final reports expose task economics and unresolved amounts without turning them into payment authorization.

## What changed

- `close revise --source FILE --role ROLE` adds or replaces a declared source. Bundle revisions reject undeclared tables and invoice documents.
- `close review --revision rN` records a named review for that revision and synchronizes the report fingerprint used by signoff. Review and signoff share the root-case lock.
- Signoff requires an exact, case-insensitive reviewer match in `canonical manifest.buyer.acceptance_signers`; embedding an authorized name in a longer string no longer passes.
- OCR `to` period boundaries stay exclusive as stated, while date-only values keep calendar-date semantics. Booking provenance binds reviewer/time, original document hash, bill-export hash and the declared bills source.
- Reports include task-level direct/allocated/full costs, verified outcomes and values, unresolved priority amounts, root-cause groups and the actual signoff state. `signoff_revision` and `signoff_state` stay separate.
- `verovo case` uses the shared real-task runtime, and the repository/package conformance entries use one implementation.

## Verification

440 Buyer Ledger, 663 healthcheck, 69 root and 91 Lab tests pass. Selftest, protocol vectors, registry/vocabulary/rule-card checks, package builds and SEO checks are release gates. New regressions cover root locking, exact signer matching, named source addition, period boundaries and report projections.

This release strengthens local evidence and revision semantics. It does not establish a real customer close, buyer payment authorization, external identity proof or live-provider connectivity.
