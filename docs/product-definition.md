# Verovo product definition

Baseline: 2026-10-30. Implementation alignment: 2026-11-23.

**Verovo is the independent measurement and audit layer for AI agents: a buyer-side financial audit and task economics engine.**
It connects technical usage, financial records and business outcomes to compute
direct/shared task costs and cost per verified success. Verified monetary value
supports ROI calculation; missing evidence remains explicit.
It connects applicable charging rules, spending and usage records, delivery
evidence and buyer requirements to support payment, reconciliation, acceptance,
remediation and renewal decisions. Provider-side work extends from concrete
transactions rather than defining a second equal-priority market.

## Users and responsibilities

Business, engineering and team owners are accountable for AI spending or business
results. Finance and procurement help with payment, contracts and approval.
Identify the person who has the records, the person who uses the result and the
person who can commission work; these may be different people.

## Independent review workflows and task economics

| Workflow | Basis | Result |
|---|---|---|
| Charges | Applicable charging rules, account, period and records | Matching items, supported differences and unresolved items |
| Delivery | Explicit business acceptance criteria and observable results | Met, unmet and unprovable requirements, with remediation needs |

A correct charge can accompany inadequate delivery. A failed buyer requirement
does not automatically establish a billing violation. Delivery review can be
commissioned independently of a billing discrepancy. Every result is limited to
the criteria, observations and coverage actually reviewed.

## Objects and current focus

AI applications, coding agents, automation platforms, tools/APIs and model
services are the long-term object map. AI support is an initial application
scenario. Tool-period reconciliation is another concrete buyer task. Broader
coverage requires its own adapters, usable records and customer validation.

The first implementation combines existing local tools and scoped human review.
Review reports carry three separate conclusion levels: rule consistency (what a run
establishes), evidence credibility (explicitly not established by the tool: rule
applicability confirmation, third-party corroboration, coverage), and the buyer
business decision (outside the tool). The levels are never collapsed into a score
and Level 3 never becomes a tool output.
The [buyer review command](buyer-review.md) compares explicit criteria with local
JSON observations and produces separate charge/delivery reports. Supported
recounts and Buyer Ledger remain specialized input and calculation capabilities.
It is not a universal contract parser, autonomous quality judge or refund system.

## Keep these layers separate

- Buyer-first, charges and delivery: product boundary.
- Support, coding, automation, tools and models: objects and scenarios.
- Ledger, rules, SDK, browser access and evidence hashes: capabilities.
- A/B POCs, sample sizes and schedules: adjustable validation plans.
- Open source, scoped projects and possible repeat service: operating models.

Provider upstream spend is a buyer task. Provider downstream billing work needs
a specific dispute, revenue discrepancy or procurement requirement. Shared code
does not establish demand on both sides or a network effect.

## Scope changes

State the changed layer, previous scope, proposed scope, reason and review
condition. New engineering capabilities or a narrow experiment do not silently
replace this definition. Customer value, actual payment and repeat purchase are
separate from engineering tests and open-source use.

The current [free measurement runs](https://roy-tong.github.io/Verovo/services.html)
remain free and limited to measurement samples. Enterprise review scope and any
fee are agreed for a concrete task; there is no fixed enterprise rate or recovery
guarantee. See [commercial scope](commercial-narrative.md).

## Spend actions and Provider admission (implemented 2026-11-23)

The two product goals share an evidence base but not a single score. AI Spend Assurance asks whether charges occurred, were authorized, were calculated correctly and received a disposition. Agent Value Measurement asks which agent, tool or workflow delivers accepted outcomes at a defensible cost. Financial correctness, control effectiveness, delivery and economic value remain independent conclusions.

`spend-actions` projects an existing Audit Case into ready/evidence/review/unreviewed/partially_settled/settled work items with owner roles and per-currency amount coverage. Only versioned `charge_reviews` bound to normalized source content and contract versions can make a line ready for payment; legacy `clean_charge_lines` alone does not clear it. A Finding marked `realized` becomes settled only when an applied credit matches the finding amount, currency and charge identity; partial credit produces `partially_settled`. Actual payment or vendor review remains outside the tool. `provider check` validates adapter identity, version, readiness, source trust, resource limits, coverage, cleanup and raw snapshot digests. Local `admit` does not claim live connectivity, complete coverage or control effectiveness. See [feature status](feature-status.md) and [Provider contract](provider-contract.md).

The next business validations are an actual buyer disposition on one period, a real authorized stop-receipt reconciliation using `budget verify` against subsequent charges, and one private two-tool procurement comparison. The local receipt/monitor consistency verifier is implemented, but runtime enforcement, authenticated external evidence, public benchmarks and universal connectors are not current capabilities.

## Task economics

`economics` consumes explicit local case files linking tasks, runs, financialized usage, costs and outcomes. See [the contract](task-economics.md). This extends charge and delivery review without treating one as proof of the other. General-purpose automated invoice extraction, causal optimization and insurance are not shipped capabilities. The narrow Buyer Ledger OCR candidate intake below creates review drafts only; it is not a universal invoice parser.

## Canonical narrow finance chain (implemented 2026-11-04)

`canonical_finance.py` and `scripts/run_canonical_finance.py` orchestrate the existing Period Intake, approved Rule Map and Task Economics components rather than adding another financial engine. The supported slice is known CSV/JSONL inputs, one vendor/account/currency, approved per-unit pricing and approved allocation drivers. Unsupported formats and unapproved rules fail closed. Raw OCR/invoice2data documents attached directly to Period Intake remain unreviewed/unbooked; the separately reviewed candidate export below is the only OCR bridge into the existing close input path.

- Missing amount, currency or quantity is `unknown/open`, never a default currency or zero. Related totals stay unknown; attributed subtotals remain separate.
- Allocation coverage and reconciliation are orthogonal: per-currency `allocatable = allocated + unallocated + non_allocatable`, with separate matched/difference/unknown states. Shared costs require versioned drivers and approval; missing weights are not averaged.
- Evidence conclusions are `Invoice Matched`, `Vendor Reconciled`, `Buyer Verified` and `Delivery Accepted`, with source party, collection method, independence and recalculation status. Two vendor exports can at most support Vendor Reconciled; they do not independently prove Buyer Verified.
- Delivery and task-economics evidence is joined through Agent traces, tasks and BuyerOutcome. Cost per Verified Success is not ROI; unverified business value never fills the ROI gap.

### OCR candidate intake (Buyer Ledger, synthetic validation only)

`buyer-ledger bill-candidate add/review/book` is the narrow front door for visible bill text and PNG/JPG/JPEG screenshots. It does not require `invoice2data`; the older healthcheck `invoice-intake` command remains a separate optional invoice2data draft adapter and never books its output. Screenshot OCR uses Verovo's own local macOS Vision runner. Non-darwin local Vision is explicitly unavailable; pasted text and caller-supplied offline OCR lines remain portable alternatives.

The candidate preserves the source bytes and SHA-256, the exact OCR text, line confidence and field-level text spans or image bounding boxes. Missing `vendor`, `invoice_no`, `period`, `currency` and `amount` values remain `null`. Every key field requires named human confirmation; low-confidence or flagged fields also remain queued. Only an explicit `bill-candidate book --by ...` exports reviewed values as an ordinary bills CSV plus `verovo.period-job/1` for the existing close chain. Bad OCR, unresolved fields, line-total mismatches and duplicate vendor+invoice numbers cannot book; mixed currencies are rejected by close import rather than merged or converted.

This bridge neither verifies OCR accuracy nor upgrades vendor evidence into Buyer Verified. The generic counts-only `recount` adapter is not a universal invoice target, and `review` still needs its own explicit criteria and local observations. Booking, close review and signoff do not execute or authorize payment or refunds. See [the OCR command contract](../buyer-ledger/docs/ocr-golden-chain.md).

The complex fixture covers shared API keys, seat fees, buyer-accepted and rejected delivery, and vendor-claimed value. It verifies fully loaded cost and Cost per Verified Success while leaving ROI unknown. This fixture is synthetic, not a production customer result. Real supplier data, FX, general contract parsing, arbitrary invoice formats, external identity/signature verification and OCR accuracy remain unverified.
