# Verovo

<img src="assets/verovo-logo-card.png" alt="Verovo" width="300">

**The independent measurement and audit layer for AI agents.**

**Make every dollar spent on AI services make sense.** Verovo helps teams audit agent and tool charges, connect spending to verified task outcomes, and decide what to pay, fix or compare next.

[Website](https://roy-tong.github.io/Verovo/) · [中文](README.zh-CN.md) · [Buyer guide](docs/buyer-review.md) · [Replay example](https://roy-tong.github.io/Verovo/examples/replayable-period.html) · [Discord](https://discord.gg/UqrM8MCXb)

[![CI](https://github.com/roy-tong/Verovo/actions/workflows/conformance.yml/badge.svg)](https://github.com/roy-tong/Verovo/actions/workflows/conformance.yml)
[![PyPI](https://img.shields.io/pypi/v/verovo)](https://pypi.org/project/verovo/)
[![License](https://img.shields.io/badge/License-MIT-blue)](LICENSE)

## Audit every charge. Measure every outcome.

A total invoice cannot tell you which line needs evidence, whether a retry was worth it, or which tool should handle the next task. Verovo connects supplier rules, invoices, usage, delivery and buyer acceptance so finance, operations and engineering can answer three questions:

- **What should happen to this charge?** Recalculate supported bills and turn the findings into a line-level worklist: ready for payment, evidence required, review required, unreviewed, partially settled or settled. Clean recommendations are bound to normalized source content and contract versions; settlement is verified from applied credit evidence.
- **Was the work delivered?** Check observed delivery against the buyer’s own acceptance criteria without treating a failed outcome as automatic proof of a billing error.
- **Which option is worth choosing?** Connect direct and allocated task costs with verified outcomes, cost per verified success and explicit scenario comparisons.

Financial correctness, control (authorization), delivery and economic value remain separate conclusions. Missing evidence stays visible instead of becoming a zero or a pass.

## From one billing period to a clear next move

1. **Bring the period.** Gather the rules, invoice, usage and delivery records for one supplier.
2. **Review charges and outcomes.** See financial, delivery and evidence coverage as separate answers.
3. **Assign the next action.** Give each open line an owner and a reason; record the buyer’s actual payment, hold or vendor review separately.
4. **Replay and compare.** Reproduce the evidence and use task economics for the next acceptance, renewal or procurement decision.

[Open a buyer review](https://roy-tong.github.io/Verovo/examples/buyer-review.html) · [Replay a billing period](https://roy-tong.github.io/Verovo/examples/replayable-period.html)

## Run your first review

```bash
pipx install verovo
verovo review --sample --html buyer-review.html --json buyer-review.json
```

The offline synthetic example presents charge and delivery reviews with traceable criteria and observations. Prepare your own task with the [buyer guide](docs/buyer-review.md).

## Turn an Audit Case into a spend-action list

From a source checkout:

```bash
python3 healthcheck/verovo spend-actions \
  --case examples/audit-golden-case/audit-case.json \
  --json spend-actions.json --markdown spend-actions.md
```

The synthetic case produces three evidence actions, four review actions, one unreviewed line, two lines ready for payment and one settled line. Only versioned reviews bound to normalized source content and contract versions can become ready for payment; `realized` becomes settled only after the applied credit is independently verified. The report does not execute or authorize payment.

## See the economics behind each task

```bash
git clone https://github.com/roy-tong/Verovo.git && cd Verovo
python3 healthcheck/verovo economics \
  --case examples/task-economics.json \
  --json economics.json --markdown economics.md
```

Verovo connects tasks, agent runs, usage, invoices and verified outcomes. Compare direct and allocated costs, cost per verified success and supplier or workflow scenarios. Incomplete cost or outcome coverage remains explicit.

[Task economics](docs/task-economics.md) · [Period intake](docs/period-intake.md) · [Cost drivers](docs/cost-optimization.md)

## Keep external components behind an evidence contract

```bash
python3 healthcheck/verovo provider check \
  --manifest examples/provider-manifest/otel-file.json
```

The Provider Manifest records engine version, integration mode, source trust, readiness checks, resource limits, coverage, cleanup and raw-snapshot digests. Local admission can be `admit`, `degrade` or `refuse`; it never claims live engine contact or complete production coverage. [Provider contract](docs/provider-contract.md)

## Export and replay a period proof

```bash
verovo proof export --bundle healthcheck/tests/fixtures/canonical-finance --out /tmp/verovo-period-proof --by reviewer
verovo proof replay /tmp/verovo-period-proof --bundle healthcheck/tests/fixtures/canonical-finance
```

Replay returns `EXPIRED` for changed inputs and `FAIL` for changed financial output. Neither command mutates the period or authorizes payment. The installed wheel covers these canonical proof commands; pre-M1 `python3 -m buyer_ledger` workflows remain source-checkout tools documented in [Buyer Ledger](buyer-ledger/README.md).

## Where Verovo fits

- **OTel, OpenLIT and Langfuse** provide execution observations. Verovo uses eligible records for independent financial and outcome review rather than treating a trace as a final bill.
- **LiteLLM and other gateways** execute routing and controls. Verovo is designed to check their records, coverage and subsequent charges rather than replacing them.
- **FOCUS and FinOps tools** exchange and aggregate cost. Verovo links those amounts to agent tasks, acceptance and procurement choices.
- **Inspect and other runners** execute evaluations. Verovo Lab owns preregistration, comparison limits and economic interpretation.

These are integration boundaries, not a claim that every connector is already shipped. [Current capabilities](docs/feature-status.md) and [the validation roadmap](ROADMAP.md) separate local functionality from live-provider and customer validation.

## Built from public measurement work

Verovo grew out of an audit of roughly 110 AI usage and cost tools. The project published more than 65 evidence-backed findings across five recurring accounting problems, along with reproductions and upstream corrections.

- [The Token-Accounting Bug Report](campaigns/audit-report-2026-09.md)
- [Dated case cards](docs/launch/README.md)
- [Claims and reproductions](https://roy-tong.github.io/Verovo/claims.html)

That work continues in the open-source rules, SDK, conformance vectors and buyer tools. Use the CLI, connect through MCP, join [Discord](https://discord.gg/UqrM8MCXb), follow [X](https://x.com/elliwoodtong), or bring a concrete supplier period to the [buyer task page](https://roy-tong.github.io/Verovo/first-look.html#contact).

## Continue

- [Buyer review guide](docs/buyer-review.md)
- [Supported recounts](examples/showcase/README.md)
- [Buyer Ledger](buyer-ledger/README.md)
- [Provider SDK](sdk/README.md)
- [Roadmap](ROADMAP.md) · [Contributing](CONTRIBUTING.md) · [AGENTS.md](AGENTS.md)

Verovo is MIT open source. Start with one supplier, one period and one decision.
