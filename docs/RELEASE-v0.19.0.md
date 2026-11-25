# v0.19.0 — Business-first close report and spend monitoring

Verovo 0.19.0 makes the buyer-close report answer the buyer's first question before showing audit detail: should this be paid or renewed, what is the unexplained difference, and what must be fixed first.

- The main section now carries the payment/renewal recommendation, invoice and open difference, accepted task count, cost per verified success and whether value can be judged. Root-cause groups replace raw item IDs; verdict axes, line-level reconciliation and replay metadata remain in the appendix.
- Unresolved items are ordered by amount (same currency), due date, evidence confidence, persistent loss and systemic-error signal. Findings on one invoice line collapse into one exposure, and the report shows both gross and de-duplicated amounts without summing different currencies.
- `verovo monitor` ingests local usage, builds a per-tool/model/currency median/MAD baseline and reports spend spikes, unit-price drift, retry storms and orphan calls. It is advisory only and never blocks a payment.
- `verovo tool-value` compares comparable task groups on success rate, cost per task and cost per verified success. Without a control group it does not claim incremental value.

Verification: 445 Buyer Ledger, 673 healthcheck and 69 root tests pass, together with `verovo selftest`. Open items never become payment authorization; unknown economics never become zero or ROI.
