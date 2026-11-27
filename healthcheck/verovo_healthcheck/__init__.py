"""Verovo Healthcheck — a local check-up report for your coding agent runs.

Reads local agent runtime logs (Codex rollout JSONL first), computes a small
set of deterministic checks (duplicate records, retry amplification, tool-error
runs), and renders a terminal summary plus a local HTML report.

Honesty rules inherited from the conformance pack:
- Healthcheck run verdicts are PASS / FINDING / UNPROVABLE; UNPROVABLE is first-class.
  The `review` command uses PASS / FAIL / UNPROVABLE for criterion verdicts; FINDING
  describes a healthcheck observation, not a third review verdict.
- Raw data never leaves the machine; there is no network code in this package.
- Sub-token fields (cached, reasoning) are subsets, never added into totals.

Python 3.9+ standard library only.
"""

__version__ = "1.0.0"

RUNTIME_NAME = "codex-rollout"
