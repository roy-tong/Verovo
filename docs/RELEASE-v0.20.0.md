# v0.20.0 — Persistent spend control evidence and optimization review

Verovo 0.20.0 connects local Agent usage, control evidence and task-economics experiments without collapsing them into a payment, causality or savings claim.

- Persistent monitoring adds frozen baselines, deduplicated incremental records, UTC minute/hour/day series, forecasts and local alert outboxes. External delivery and runtime control remain separate.
- `budget check`, `budget verify` and `budget resume` distinguish advisory decisions, stopped/running readbacks, subsequent-spend coverage and named recovery history. No hold suggestion is presented as an applied stop.
- Controlled local process experiments use an explicit adapter and retain output/artifact/source hashes. Tool-value comparisons include Wilson/Newcombe intervals, and human baselines require equal task and quality contracts.
- `optimize-verify` and persistent `optimize-loop` review cost and quality before/after an authorized change. Source changes, registration drift, pause/resume and rollback-required states remain visible; Verovo never applies external configuration or rollback itself.
- Runtime discovery no longer lets an empty Codex directory hide existing Claude sessions.

Verification: 763 healthcheck and 69 root tests pass, together with selftest, the metric registry and all protocol vectors. Isolated wheels pass monitoring, control, experiment, optimization and discovery chains. Synthetic evidence does not prove production control, supplier reconciliation, causal value or realized savings.
