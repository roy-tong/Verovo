# Verovo local CLI

Verovo is the independent measurement and audit layer for AI agents. Review charges, prepare line-level spend actions, check delivery and measure task economics with explicit evidence gaps. Financial correctness, control (authorization), delivery and economic value remain separate conclusions.

Python 3.9+ standard library only. Local workflows; no runtime dependencies and no automatic upload.

## Install and run the first review

```bash
pipx install verovo
verovo review --sample --html buyer-review.html --json buyer-review.json
```

The offline synthetic example shows charge and delivery reviews with traceable criteria and observations. [Buyer guide](https://github.com/roy-tong/Verovo/blob/main/docs/buyer-review.md) · [Product website](https://roy-tong.github.io/Verovo/).

## Turn an Audit Case into a spend-action list

From a source checkout:

```bash
git clone https://github.com/roy-tong/Verovo.git && cd Verovo
python3 healthcheck/verovo spend-actions \
  --case examples/audit-golden-case/audit-case.json \
  --json spend-actions.json --markdown spend-actions.md
```

The command produces `ready_for_payment`, `evidence_required`, `review_required`, `unreviewed` and `settled` actions with owner roles, reasons, finding links and per-currency amount coverage. Only explicitly confirmed clean lines become ready for payment; an unflagged line is not implicitly cleared. It never executes or authorizes payment.

## Check a Provider Manifest

```bash
python3 healthcheck/verovo provider check \
  --manifest examples/provider-manifest/otel-file.json
```

The manifest records engine identity, integration mode, source trust, explicit readiness checks, resource limits, coverage, cleanup and raw-snapshot digests. Local admission is `admit`, `degrade` or `refuse`; every result states `live_engine_contacted=false`. This is an adapter contract, not live connectivity, production completeness or control-effectiveness proof. [Provider contract](https://github.com/roy-tong/Verovo/blob/main/docs/provider-contract.md).

## Measure task economics

```bash
python3 healthcheck/verovo economics \
  --case examples/task-economics.json \
  --json economics.json --markdown economics.md
```

Connect tasks, agent runs, usage, invoices and verified outcomes. Compare direct and allocated costs, cost per verified success and supplier or workflow scenarios. Incomplete cost or outcome coverage remains explicit. [Task economics](https://github.com/roy-tong/Verovo/blob/main/docs/task-economics.md).

## Export and replay a period proof

```bash
verovo proof export --bundle healthcheck/tests/fixtures/canonical-finance --out /tmp/verovo-period-proof --by reviewer
verovo proof replay /tmp/verovo-period-proof --bundle healthcheck/tests/fixtures/canonical-finance
```

Replay returns `EXPIRED` for changed inputs and `FAIL` for changed financial output. Neither command mutates the period or authorizes payment. Pre-M1 `python3 -m buyer_ledger` workflows remain source-checkout tools documented in [Buyer Ledger](https://github.com/roy-tong/Verovo/blob/main/buyer-ledger/README.md).

## Other supported workflows

- `verovo check`: inspect supported local agent logs for duplicate records, retry amplification and tool-error runs.
- `verovo recount`: recalculate supported vendor exports with explicit rules and evidence gaps.
- `verovo intake`: assemble a task-economics case from a local billing-period bundle.
- `verovo drivers`: break down vendor, task, model, retry and allocation cost drivers.
- `verovo optimize`: produce scenario-based proposals with humans responsible for every change.
- `verovo selftest`: run the packaged adapter and contract checks.

Use `verovo --help` for the full command list and `verovo COMMAND --help` for exact input contracts. Existing conformance, commerce and diagnostic commands remain supported; they do not redefine the buyer product boundary.

## Evidence and privacy

- Raw records stay local. Sharing is an explicit user choice after inspection.
- Unknown amounts, outcomes, readiness and coverage are not coerced into zero, empty or success.
- An adapter manifest, trace or gateway record does not become a final supplier bill or independent proof by relabelling its source.
- Synthetic fixtures demonstrate calculations, not customer results, live-provider coverage, recovery, control effectiveness or procurement validation.

[Current capabilities](https://github.com/roy-tong/Verovo/blob/main/docs/feature-status.md) · [Roadmap](https://github.com/roy-tong/Verovo/blob/main/ROADMAP.md) · [Claims and reproductions](https://roy-tong.github.io/Verovo/claims.html)

## Development

```bash
python3 -m unittest discover -s healthcheck/tests
python3 healthcheck/verovo selftest
bash healthcheck/scripts/smoke_install.sh --wheel
```

The repository CI also runs Buyer Ledger, Lab, SDK, protocol-vector, documentation and website checks. Release artifacts are built and smoke-tested before publication.

- [GitHub](https://github.com/roy-tong/Verovo)
- [Issues](https://github.com/roy-tong/Verovo/issues)
- [Discord](https://discord.gg/UqrM8MCXb)
- [X / Twitter](https://x.com/elliwoodtong)

MIT license.
