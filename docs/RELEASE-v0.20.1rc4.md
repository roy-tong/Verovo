# Verovo 0.20.1rc4

Prerelease with matching distribution and runtime versions, plus the RC3 economics, revision and control fixes.

## Included

- Distribution metadata dynamically reads `verovo_healthcheck.__version__`; the installed wheel and `verovo --version` report the same release.
- Monitoring accepts only provably newer revisions; historical replay cannot overwrite current events.
- `tool-value --economics-case` derives costs and buyer acceptance from Canonical Task Economics. Declared CSV/JSONL remains a separately labelled compatibility input.
- Budget readback binds the requested budget, scope, action and observation time.

## Verification

- 785 healthcheck tests and selftest.
- Regression test failed against static package metadata before the dynamic-version fix.
- Rebuilt wheel and sdist identify version 0.20.1rc4; isolated installation verifies both package metadata and CLI output.
- Root tests, Buyer Ledger, registry and protocol vectors remain release gates.

## Boundary

This is a prerelease. Production spend holds, subsequent-spend coverage, a real supplier period, realized savings and external adoption are not claimed. The synthetic economics fixture used for CLI validation is not a real customer experiment.
