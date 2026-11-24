# v0.17.2 — Evidence-bound spend actions

Verovo 0.17.2 hardens the new spend-action semantics found during external review. Clean payment recommendations are now bound to exact invoice bytes and contract versions, settlement is independently verified from credit evidence, and currency precision follows the shared Money Contract.

## What changed

- `ready_for_payment` requires a versioned `charge_reviews` entry containing `invoice_source_id`, `line_id`, `source_digest`, `rule_version`, `reviewed_by` and `reviewed_at`. A matching invoice path and line ID is not enough: changed content or contract versions invalidate the old review. Text digests normalize LF/CRLF so Windows checkouts do not falsely invalidate a review. Legacy `clean_charge_lines` alone no longer clears a line.
- A Finding marked `realized` no longer automatically becomes `settled`. The referenced credit must exist, be applied with a date, and match the finding amount, currency and charge identity. Partial credits produce `partially_settled`; missing, unapplied or mismatched evidence remains `review_required`.
- Missing `affected_lines` and duplicate `finding_id` values now fail closed instead of disappearing from the worklist.
- Invoice-line amounts and case-level amounts are summarized separately to avoid double-counting different risk objects.
- The shared Money Contract now supports BHD, IQD, JOD, KWD, LYD, OMR and TND with three minor units while preserving existing two-decimal behavior.

## Verification

Twelve new adversarial tests cover cross-invoice ID collisions, stale source digests, CRLF normalization, legacy clean declarations, missing affected lines, missing/unapplied/partial credit, full settlement, KWD precision, case-level amount separation and duplicate finding IDs. 659 healthcheck tests and 69 root tests pass locally. GitHub CI and release workflows remain the external release gates.

This patch strengthens local evidence semantics. It does not establish live-provider connectivity, a real customer close, runtime spending control or a procurement benchmark.
