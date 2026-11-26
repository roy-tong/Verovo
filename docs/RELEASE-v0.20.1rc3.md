# Verovo 0.20.1rc3

Prerelease for independently verified spend control, revision history and Canonical Task Economics comparison.

## Included

- Monitoring accepts only provably newer integer-sequence or timezone-aware timestamp revisions; historical replay and unordered revisions cannot overwrite current events.
- `tool-value --economics-case` and MCP `economics_case` derive costs and buyer acceptance from Canonical Task Economics. Incomplete coverage is refused and declared CSV/JSONL inputs carry a separate trust label.
- Budget `APPLIED` now binds runtime readback to the requested budget, scope and hold action, and requires an observation no earlier than the control decision. Wrong-scope and stale readbacks remain `UNPROVABLE`.
- Stable and Preview installation commands are synchronized from the unified release manifest; the legacy homepage redirects to the Verovo product entry.

## Verification

- 784 healthcheck tests and selftest.
- 445 Buyer Ledger tests and 70 repository tests.
- Registry validation and all 62 protocol vectors.
- 156 shared acceptance tests and 115 passed / 0 failed / 5 blocked real-task verification results; five historical cases remain blocked because their original requirement sessions are not relinked.
- SEO validation across 23 retained pages and initial adversarial control/revision reproductions.

## Boundary

This is a prerelease. Production spend holds, subsequent-spend coverage, a real supplier period, realized savings and external adoption are not claimed. The new `--economics-case` capability becomes available only with this release; public pages for older RC versions retain their existing commands.
