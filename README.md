# Verovo

<img src="assets/verovo-logo-card.png" alt="Verovo" width="300">

**Find the economic problem you did not know you had.**

Verovo is independent economic intelligence for AI agents. It helps developers, AI teams, finance and procurement discover abnormal spend, audit supplier charges and compare the real cost of accepted outcomes across tools, models and workflows.

[OVO unified website](https://roy-tong.github.io/ovo/en.html) · [中文](README.zh-CN.md) · [Buyer guide](docs/buyer-review.md) · [Replay example](https://roy-tong.github.io/Verovo/examples/replayable-period.html)

[![CI](https://github.com/roy-tong/Verovo/actions/workflows/conformance.yml/badge.svg)](https://github.com/roy-tong/Verovo/actions/workflows/conformance.yml)
[![PyPI](https://img.shields.io/pypi/v/verovo)](https://pypi.org/project/verovo/)
[![License](https://img.shields.io/badge/License-MIT-blue)](LICENSE)

## A platform can tell you what it charged. Verovo checks what the work was worth.

Agent platforms, gateways and observability tools already show usage and prices. The buyer still needs an independent answer: which retry created no value, which invoice line needs evidence, and which tool delivers an accepted task at a defensible cost.

Verovo connects agent logs, supplier records and independently accepted business outcomes without turning financial correctness, control, delivery and value into one opaque score.

### Three economic services

| Service | Start with | Business delivery |
| --- | --- | --- |
| **Agent Spend Healthcheck** | Accessible agent logs and usage records | Cost distribution, retries, anomalies and the tasks behind inefficient spend |
| **Independent Bill Audit** | A supplier bill and buyer-side records | Recalculated charges, supported differences, unresolved amounts and dispute-ready evidence |
| **Agent Economics Optimization** | Real tasks, costs and acceptance results | Tool/model comparisons, controlled experiments and an independent review of the optimized result |

A useful first result is direct: **“I found an economic problem I could not see before.”** When no anomaly exists, a reliable bounded review is also a valid delivery.

## Go from a charge to an economic decision

1. **Discover the actual environment.** Identify supported local agent runtimes and the records they can prove; subscription usage is not automatically a marginal cash bill.
2. **Calculate the economics.** Connect task, operation and attempt identities without double counting revisions or collapsing separate currencies.
3. **Investigate the exception.** Find abnormal cost, retry loops, orphan calls and incomplete evidence.
4. **Audit independently.** Apply explicit supplier rules and keep confirmed, estimated and unresolved amounts separate.
5. **Compare and verify.** Run comparable tasks, measure cost per accepted outcome and review quality before calling an option better.

Verovo does not need to become another model gateway. Existing gateways and runtimes execute controls; Verovo checks their scope, readback and subsequent evidence.

## Proof comes from measurement, not a larger dashboard

Verovo grew out of public measurement work across **110+ repositories**, producing **65+ evidence-backed findings across 5 recurring accounting problem classes**. Those reproductions demonstrate technical measurement, not customer savings or paid adoption.

A separate **USD 4.50 synthetic billing difference** shows why historical pricing rules must enter the calculation. It is a fixture, not a customer loss or a recovered amount.

[Read the Token-Accounting Bug Report](campaigns/audit-report-2026-09.md) · [Inspect claims and reproductions](https://roy-tong.github.io/Verovo/claims.html)

## Run your first healthcheck

```bash
# Stable
<!--OVO:verovo-stable-->pipx install verovo==0.20.0
verovo review --sample<!--/OVO:verovo-stable-->
# Preview
<!--OVO:verovo-preview-->pipx install verovo==0.20.1rc3
verovo review --sample<!--/OVO:verovo-preview-->
verovo review --sample --html buyer-review.html --json buyer-review.json
```

The offline synthetic review compares explicit charge and delivery criteria with local observations. For task economics:

```bash
git clone https://github.com/roy-tong/Verovo.git && cd Verovo
python3 healthcheck/verovo economics \
  --case examples/task-economics.json \
  --json economics.json --markdown economics.md
```

The result keeps attributed subtotals when full economics are incomplete. Unknown costs, outcomes and ROI remain unknown instead of becoming zero.

## A measurable path from healthcheck to optimization

```text
Agent logs / supplier bill
    ↓
Canonical usage, cost and outcome records
    ↓
Anomaly or independently verified difference
    ↓
Comparable tool, model or workflow experiment
    ↓
Independent quality and cost review
    ↓
Keep / rollback / inconclusive
```

Potential savings and realized savings are separate results. A successful experiment with no observed saving is also an honest outcome.

## Current capabilities and next proof

- **Available now:** local runtime discovery, persistent spend monitoring, anomaly records, advisory budgets and control receipts, independent buyer review, line-level spend actions, task economics, controlled local experiments and proof replay.
- **Next to validate:** authorized production control, complete subsequent-spend reconciliation, a real supplier period and equal-or-better-quality optimization.
- **Adoption goal:** repeated independent use by developers, technical owners and professional reviewers, rather than more cost charts.

See [feature status](docs/feature-status.md) and [the validation roadmap](ROADMAP.md) for live-provider and customer-validation boundaries. OTel, Langfuse, LiteLLM and evaluation runners remain integration counterparts, not proof that every connector is shipped.

## Continue

- [Buyer review guide](docs/buyer-review.md)
- [Task economics](docs/task-economics.md)
- [Supported recounts](examples/showcase/README.md)
- [Buyer Ledger](buyer-ledger/README.md)
- [Provider SDK](sdk/README.md)
- [Roadmap](ROADMAP.md) · [Contributing](CONTRIBUTING.md) · [AGENTS.md](AGENTS.md)

Verovo is MIT open source. Bring one agent environment, one supplier period and one decision to the [buyer task page](https://roy-tong.github.io/Verovo/first-look.html#contact), or join [Discord](https://discord.gg/UqrM8MCXb).
