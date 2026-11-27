# Verovo 1.0.0

First stable release of Verovo, carrying the complete 0.20.x buyer-first measurement, audit and task-economics line.

## Included

- Buyer-close economics and evidence-aware spend actions, with explicit open items and UNPROVABLE boundaries.
- Persistent spend monitoring, frozen baselines, revision-safe event handling and alert delivery receipts.
- Budget control readback bound to budget, scope, action and observation time; no implied external hold or realized saving.
- Comparable task-economics analysis, human baselines, optimization review and local control/treatment adapters.
- Distribution metadata and runtime both identify version 1.0.0. Stable and Preview installation entries point to the same stable release.

## Verification

- Healthcheck, root, registry and protocol suites are release gates.
- Rebuilt wheel and sdist identify 1.0.0; isolated installation verifies package metadata and `verovo --version`.
- Release CI must pass before GitHub Release and PyPI publication.

## Boundary

Synthetic fixtures do not prove production spend control, supplier reconciliation, realized savings, external adoption or a real billing period. The stable version number does not change those evidence boundaries.
