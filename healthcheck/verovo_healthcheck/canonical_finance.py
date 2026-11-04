"""Narrow canonical finance chain built from the existing Verovo components.

The chain is intentionally an orchestrator, not another financial engine::

    approved Rule Map -> Period Intake -> approved recalculation
                      -> Task Economics allocation and outcome arithmetic

Only known CSV/JSONL period sources, one vendor/account/currency and explicitly
approved rules are accepted. OCR/invoice2data documents remain unreviewed and
unbooked candidates through Period Intake. All monetary arithmetic stays in
:mod:`task_economics`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import period_intake, task_economics

SCHEMA_VERSION = "1.0"
SOURCE_NAMES = {"tasks", "runs", "usage", "bills", "outcomes", "invoices"}
INVOICE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".txt"}
CONCLUSION_NAMES = (
    "Rule Repriced", "Invoice Reconciled", "Invoice Matched",
    "Vendor Reconciled", "Buyer Verified", "Delivery Accepted",
)


class CanonicalFinanceError(ValueError):
    """The requested input is outside the supported canonical slice."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _inside(bundle: Path, relative: Any) -> Path:
    if not _text(relative) or Path(str(relative)).is_absolute():
        raise CanonicalFinanceError("rule_map must be a relative local path")
    path = (bundle / str(relative)).resolve()
    try:
        path.relative_to(bundle.resolve())
    except ValueError as exc:
        raise CanonicalFinanceError("rule_map escapes the period bundle") from exc
    if not path.is_file() or path.suffix.lower() != ".json":
        raise CanonicalFinanceError("rule_map must be an existing JSON file")
    return path


def _approved(rule: Dict[str, Any], label: str) -> None:
    if "approved" in rule and rule["approved"] is not True:
        raise CanonicalFinanceError("%s is not approved" % label)
    for field in ("rule_id", "version", "kind", "confirmed_by", "confirmed_at", "source_ref"):
        if not _text(rule.get(field)):
            raise CanonicalFinanceError("%s needs approved %s" % (label, field))


def load_rule_map(bundle: Path, manifest: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], ...]:
    path = _inside(bundle, manifest.get("rule_map"))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CanonicalFinanceError("cannot read approved rule map: %s" % exc) from exc
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise CanonicalFinanceError("rule map schema_version must be 1.0")
    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise CanonicalFinanceError("rule map needs approved rules")
    intake: Dict[str, Dict[str, Any]] = {}
    pricing: Dict[str, Dict[str, Any]] = {}
    allocation: Dict[str, Dict[str, Any]] = {}
    seen = set()
    for index, rule in enumerate(raw_rules):
        if not isinstance(rule, dict):
            raise CanonicalFinanceError("rule map rules[%d] must be an object" % index)
        _approved(rule, "rule map rules[%d]" % index)
        rule_id = _text(rule["rule_id"])
        if rule_id in seen:
            raise CanonicalFinanceError("duplicate rule map rule_id: %s" % rule_id)
        seen.add(rule_id)
        kind = rule["kind"]
        if kind == "intake":
            if rule.get("format") not in period_intake.FORMATS:
                raise CanonicalFinanceError("intake rule %s has unsupported format" % rule_id)
            intake[rule_id] = rule
        elif kind == "pricing":
            if rule.get("method") != "per_unit":
                raise CanonicalFinanceError(
                    "pricing rule %s is outside the per_unit canonical slice" % rule_id)
            if rule.get("unit_price") in (None, "") or not _text(rule.get("currency")):
                raise CanonicalFinanceError("pricing rule %s needs unit_price and currency" % rule_id)
            pricing[rule_id] = rule
        elif kind == "allocation":
            if task_economics._driver_error(rule):
                raise CanonicalFinanceError("allocation rule %s: %s" % (
                    rule_id, task_economics._driver_error(rule)))
            allocation[rule_id] = rule
        else:
            raise CanonicalFinanceError("unsupported rule kind: %r" % kind)
    return intake, pricing, allocation


def _materialize_driver_csv(bundle: Path, allocation_rules: Dict[str, Dict[str, Any]]) -> None:
    """Bind approved weights to a local, versioned driver CSV when referenced."""
    for rule_id, rule in allocation_rules.items():
        method = rule.get("method")
        if method not in ("headcount", "revenue"):
            continue
        driver = rule["driver"]
        reference = _text(driver.get("source_ref"))
        if ".csv#" not in reference:
            continue
        relative, anchor = reference.split("#", 1)
        if anchor != _text(driver.get("id")):
            raise CanonicalFinanceError("allocation rule %s driver anchor does not match driver id" % rule_id)
        path = (bundle / relative).resolve()
        try:
            path.relative_to(bundle.resolve())
        except ValueError as exc:
            raise CanonicalFinanceError("allocation driver escapes the bundle") from exc
        if not path.is_file():
            raise CanonicalFinanceError("allocation driver CSV does not exist: %s" % relative)
        field = "weight" if method == "headcount" else "value"
        basis_key = "weights" if method == "headcount" else "values"
        observed: Dict[str, str] = {}
        try:
            rows = period_intake._rows(path)
        except period_intake.IntakeError as exc:
            raise CanonicalFinanceError(str(exc)) from exc
        for row in rows:
            if _text(row.get("driver_id")) != _text(driver.get("id")):
                continue
            if _text(row.get("version")) != _text(driver.get("version")):
                continue
            task_id = _text(row.get("task_id"))
            if not task_id or task_id in observed:
                raise CanonicalFinanceError("allocation driver %s has duplicate/empty task_id" % rule_id)
            try:
                value = task_economics._dec(row.get(field), "driver %s %s" % (rule_id, task_id))
            except task_economics.EconomicsError as exc:
                raise CanonicalFinanceError(str(exc)) from exc
            observed[task_id] = str(value)
        if not observed:
            raise CanonicalFinanceError("allocation driver %s has no rows at its approved version" % rule_id)
        basis = rule.get("basis")
        if basis is None:
            rule["basis"] = {basis_key: observed}
            continue
        supplied = basis.get(basis_key) if isinstance(basis, dict) else None
        if not isinstance(supplied, dict) or set(supplied) != set(observed):
            raise CanonicalFinanceError("allocation rule %s task weights do not match its driver CSV" % rule_id)
        for task_id, raw in supplied.items():
            try:
                matches = task_economics._dec(raw, "approved driver weight") == \
                    task_economics._dec(observed[task_id], "CSV driver weight")
            except task_economics.EconomicsError as exc:
                raise CanonicalFinanceError(str(exc)) from exc
            if not matches:
                raise CanonicalFinanceError(
                    "allocation rule %s weight for %s does not match driver CSV" % (rule_id, task_id))


def _validate_scope(manifest: Dict[str, Any], intake_rules: Dict[str, Dict[str, Any]]) -> None:
    for field in ("vendor", "account_ref", "currency"):
        if not _text(manifest.get(field)):
            raise CanonicalFinanceError("canonical case needs one explicit %s" % field)
    currency = _text(manifest["currency"]).upper()
    if len(currency) != 3 or not currency.isalpha():
        raise CanonicalFinanceError("canonical currency must be a three-letter code")
    sources = manifest["sources"]
    if set(sources) - SOURCE_NAMES:
        raise CanonicalFinanceError("unsupported canonical sources: %s" % sorted(set(sources) - SOURCE_NAMES))
    for name, entries in sources.items():
        for entry in entries:
            for field in ("vendor", "account_ref"):
                if _text(entry.get(field)) and _text(entry[field]) != _text(manifest[field]):
                    raise CanonicalFinanceError("source %s crosses the single-%s scope" % (name, field))
            if _text(entry.get("currency")) and _text(entry["currency"]).upper() != currency:
                raise CanonicalFinanceError("source %s crosses the single-currency scope" % name)
            if name == "invoices":
                target = Path(str(entry["path"]))
                if target.suffix and target.suffix.lower() not in INVOICE_SUFFIXES:
                    raise CanonicalFinanceError("unsupported invoice candidate format: %s" % target.suffix)
                continue
            rule_id = _text(entry.get("rule_id"))
            rule = intake_rules.get(rule_id)
            if rule is None:
                raise CanonicalFinanceError("source %s has no approved intake rule" % name)
            if rule.get("format") != entry.get("format"):
                raise CanonicalFinanceError("source %s format does not match its approved rule" % name)
            if _text(entry.get("rule_version")) and _text(entry["rule_version"]) != _text(rule["version"]):
                raise CanonicalFinanceError("source %s uses an unapproved rule version" % name)


def _buyer_evidence(item: Dict[str, Any]) -> bool:
    return task_economics.buyer_evidence(item)


def _source_view(source: Dict[str, Any]) -> Dict[str, Any]:
    view: Dict[str, Any] = {"source_ref": source.get("source_ref")}
    view.update(task_economics.evidence_provenance(source))
    return view


def _conclusion(name: str, status: str, row_id: str, source: Dict[str, Any],
                recalculation_status: str, reason: str, basis: str,
                compared_sources: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    review = task_economics.buyer_evidence_review(source)
    row = {
        "name": name, "id": row_id, "status": status,
        "recalculation_status": recalculation_status,
        "source_ref": source.get("source_ref"),
        "independence_assertion": review["independence_assertion"],
        "independence_basis": review["independence_basis"],
        "basis": basis,
        "reason": reason,
    }
    row.update(review["provenance"])
    if compared_sources is not None:
        row["compared_sources"] = [_source_view(item) for item in compared_sources]
    return row


def _bill_item(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "cost_id": row["bill_id"], "charge_id": row["charge_id"],
        "amount": row.get("bill_amount"), "currency": row.get("bill_currency"),
        "quantity": row.get("quantity"), "rate_rule": row.get("rate_rule"),
    }


def _usage_item(row: Dict[str, Any], usage: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    # Return the live usage record, not a copy: repricing verdicts must
    # propagate into the canonical case (financial_status / recalculation_status).
    return usage.get(_text(row.get("usage_id")))


def _recalculate(case: Dict[str, Any], report: Dict[str, Any],
                 pricing_rules: Dict[str, Dict[str, Any]]) -> None:
    currency = case["currency"]
    usage = {item["usage_id"]: item for item in case["usage"]}
    costs = {item["cost_id"]: item for item in case["cost_items"]}
    open_items = case["open_items"]
    for row in report["reconciliation_rows"]:
        bill = _bill_item(row)
        bill_recalc = task_economics.recalculate_charge(bill, pricing_rules, currency)
        usage_item = _usage_item(row, usage)
        usage_recalc = None
        if usage_item is not None and usage_item.get("cost") not in (None, ""):
            usage_recalc = task_economics.recalculate_charge(usage_item, pricing_rules, currency)
        statuses = [item["status"] for item in (bill_recalc, usage_recalc) if item is not None]
        # Keep source-record reconciliation separate from approved-rule repricing.
        # A bill-only rule match is not a second ledger record and must not enter
        # reconciled_charges; one repricing difference must not erase a source mismatch.
        row["recalculation_status"] = (
            "difference" if "difference" in statuses else
            "matched" if statuses and all(status == "matched" for status in statuses) else
            "unknown")
        row["recalculation"] = {"bill": bill_recalc, "usage": usage_recalc}
        cost = costs.get(row["charge_id"])
        usage_difference = usage_recalc is not None and usage_recalc["status"] == "difference"
        if cost is not None:
            decisive = bill_recalc["status"]
            if decisive == "unknown" and usage_recalc is not None:
                decisive = usage_recalc["status"]
            cost["recalculation_status"] = decisive
            if bill_recalc.get("rule_ref"):
                cost["rule_ref"] = bill_recalc["rule_ref"]
        # A known repricing difference on any lane must reach the cost item and
        # the finance open items; a lane that cannot reprice must not hide the
        # other lane's difference out of the complete-state totals.
        if bill_recalc["status"] == "difference" or usage_difference:
            lanes = ("bill+usage" if usage_difference and bill_recalc["status"] == "difference"
                     else "bill" if bill_recalc["status"] == "difference" else "usage")
            if cost is not None:
                cost["financial_status"] = "difference"
            if not any(item.get("kind") == "charge_amount_mismatch"
                       and item.get("record_id") == row["charge_id"]
                       and item.get("status") != "resolved" for item in open_items):
                period_intake._open_item(
                    open_items, "charge_amount_mismatch",
                    "charge %s differs from its approved rule recalculation (%s lane)"
                    % (row["charge_id"], lanes),
                    _text(row.get("source_ref")), row["charge_id"])
        if usage_item is not None and usage_recalc is not None:
            usage_item["recalculation_status"] = usage_recalc["status"]
            if usage_recalc["status"] == "difference":
                usage_item["financial_status"] = "difference"
    matched_ids = sorted(row["charge_id"] for row in report["reconciliation_rows"]
                         if row["status"] == "matched")
    report["reconciled_charges"] = matched_ids
    known_mismatches = {row["charge_id"] for row in report["charge_amount_mismatches"]}
    for row in report["reconciliation_rows"]:
        difference_lanes = [lane for lane in ("bill", "usage")
                            if row["recalculation"][lane] is not None
                            and row["recalculation"][lane]["status"] == "difference"]
        if difference_lanes and row["charge_id"] not in known_mismatches:
            lane = difference_lanes[0]
            recalculation = row["recalculation"][lane]
            report["charge_amount_mismatches"].append({
                "charge_id": row["charge_id"], "bill": recalculation["actual_amount"],
                "expected": recalculation["expected_amount"], "source": row.get("source_ref"),
                "kind": "approved_rule_recalculation", "lane": lane,
            })
    counts = {status: sum(row["status"] == status for row in report["reconciliation_rows"])
              for status in ("matched", "difference", "unknown")}
    usage_only = set(report["reconciliation"]["usage_charges"]) - set(report["reconciliation"]["bill_charges"])
    counts["unknown"] += len(usage_only)
    report["reconciliation"]["counts"] = counts
    report["reconciliation"]["reconciled_charges"] = matched_ids
    report["reconciliation"]["status"] = (
        "difference" if counts["difference"] else
        "unknown" if counts["unknown"] or report["unmatched_charges"] else
        "matched" if counts["matched"] else "unknown")
    if any(item.get("kind") in period_intake.COST_OPEN_KINDS
           and item.get("domain", "finance") == "finance"
           and item.get("status") != "resolved" for item in open_items):
        case["financial_status"] = "open"


def _charge_conclusions(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    bill_source = {key: row.get(key) for key in
                   task_economics.EVIDENCE_FIELDS + ("source_ref",)}
    usage_source = {"source_ref": row.get("usage_source_ref")}
    for field in task_economics.EVIDENCE_FIELDS:
        usage_source[field] = row.get("usage_" + field)
    bill_recalc = row["recalculation"]["bill"]
    usage_recalc = row["recalculation"]["usage"]
    recalcs = [item for item in (bill_recalc, usage_recalc) if item is not None]
    differences = [item for item in recalcs if item["status"] == "difference"]
    unknowns = [item for item in recalcs if item["status"] == "unknown"]
    if differences:
        rule_status = "FAIL"
        rule_reason = "; ".join("%s: %s" % (item["id"], item["reason"]) for item in differences)
    elif recalcs and not unknowns:
        rule_status = "PASS"
        rule_reason = "Every available bill/usage amount agrees with its approved rule recalculation."
    elif any(item.get("actual_amount") is None or not item.get("currency")
             or "unknown" in _text(item.get("reason")).lower() for item in unknowns):
        rule_status = "UNKNOWN"
        rule_reason = "; ".join(item["reason"] for item in unknowns) or "Rule inputs are missing."
    else:
        rule_status = "UNPROVABLE"
        rule_reason = "; ".join(item["reason"] for item in unknowns) or "No approved rule can decide this charge."
    conclusions = [_conclusion(
        "Rule Repriced", rule_status, row["charge_id"], bill_source,
        row["recalculation_status"], rule_reason, "approved_rule_recalculation")]

    distinct_refs = bool(_text(row.get("source_ref")) and _text(row.get("usage_source_ref"))
                         and row.get("source_ref") != row.get("usage_source_ref"))
    if not row.get("usage_id"):
        invoice_status = "UNPROVABLE"
        invoice_reason = "Invoice Reconciled needs a second, distinct ledger record; rule repricing is not reconciliation."
    elif not distinct_refs:
        invoice_status = "UNPROVABLE"
        invoice_reason = "The bill and usage records do not have two distinct source references."
    elif row.get("status") == "difference":
        invoice_status = "FAIL"
        invoice_reason = "The distinct bill and usage ledger records carry different amounts."
    elif row.get("status") == "matched":
        invoice_status = "PASS"
        invoice_reason = "Two distinct bill and usage ledger records carry the same amount and currency."
    else:
        invoice_status = "UNKNOWN"
        invoice_reason = "A bill/usage amount or currency is missing, so ledger reconciliation is open."
    conclusions.append(_conclusion(
        "Invoice Reconciled", invoice_status, row["charge_id"], bill_source,
        "not_applicable", invoice_reason, "distinct_source_ledger_comparison",
        [bill_source, usage_source]))
    legacy = dict(conclusions[0])
    legacy.update({
        "name": "Invoice Matched", "alias_of": "Rule Repriced",
        "boundary": "Compatibility alias for approved-rule repricing only; not cross-source reconciliation.",
    })
    conclusions.append(legacy)

    both_vendor = all(_text(source.get("source_party")).lower() == "vendor"
                      for source in (bill_source, usage_source))
    vendor_source = dict(bill_source)
    vendor_source["independence"] = "same_vendor_control_domain"
    if not both_vendor:
        vendor_source["independence"] = "not_established"
        vendor_status = "UNPROVABLE"
        vendor_reason = "Two explicitly vendor-controlled exports are required for Vendor Reconciled."
    elif not distinct_refs:
        vendor_status = "UNPROVABLE"
        vendor_reason = "Two distinct vendor source references are required; duplicated rows are not reconciliation."
    elif row.get("usage_amount") in (None, "") or row.get("bill_amount") in (None, ""):
        vendor_status = "UNKNOWN"
        vendor_reason = "A vendor-side amount is missing."
    elif row.get("status") == "difference":
        vendor_status = "FAIL"
        vendor_reason = "The two vendor-controlled representations differ."
    elif row.get("status") == "matched":
        vendor_status = "PASS"
        vendor_reason = "Vendor exports agree; this is not independent buyer verification."
    else:
        vendor_status = "UNKNOWN"
        vendor_reason = "Vendor-controlled amount or currency evidence is incomplete."
    conclusions.append(_conclusion(
        "Vendor Reconciled", vendor_status, row["charge_id"], vendor_source,
        "not_applicable", vendor_reason, "same_vendor_record_comparison",
        [bill_source, usage_source]))

    buyer_rows = [(bill_source, bill_recalc), (usage_source, usage_recalc)]
    buyer_matched = [source for source, recalc in buyer_rows
                     if recalc is not None and recalc["status"] == "matched" and _buyer_evidence(source)]
    buyer_difference = [source for source, recalc in buyer_rows
                        if recalc is not None and recalc["status"] == "difference" and _buyer_evidence(source)]
    if buyer_difference:
        buyer_status = "FAIL"
        buyer_reason = "Qualified buyer-provenance observation differs from the approved rule."
        buyer_source = buyer_difference[0]
    elif buyer_matched:
        buyer_status = "PASS"
        buyer_reason = "Qualified buyer-provenance observation matches the approved rule; this is procedural, not cryptographic proof."
        buyer_source = buyer_matched[0]
    else:
        buyer_status = "UNPROVABLE"
        buyer_reason = ("No complete, consistent buyer-provenance observation; repeated vendor exports do not qualify; "
                        "vendor paths and self-declared independence are also rejected.")
        buyer_source = next((source for source, recalc in buyer_rows
                             if recalc is not None
                             and _text(source.get("independence")).lower() == "buyer_independent"),
                            bill_source)
    conclusions.append(_conclusion(
        "Buyer Verified", buyer_status, row["charge_id"], buyer_source,
        "difference" if buyer_difference else "matched" if buyer_matched else "unknown",
        buyer_reason, "qualified_buyer_provenance_and_rule_recalculation"))
    return conclusions


def _delivery_conclusions(outcomes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for index, outcome in enumerate(outcomes):
        outcome_id = _text(outcome.get("outcome_id")) or "outcome:%d" % (index + 1)
        buyer = _buyer_evidence(outcome) and outcome.get("verified") is True
        status_text = _text(outcome.get("status")).lower()
        if buyer and status_text == "success" and _text(outcome.get("accepted_by")) \
                and _text(outcome.get("accepted_at")):
            status = "PASS"
            reason = "Buyer-owned acceptance evidence names the affected-party reviewer and time."
        elif buyer and status_text in ("failure", "failed", "rejected", "cancelled"):
            status = "FAIL"
            reason = "Buyer-owned evidence records that delivery was not accepted."
        elif buyer and status_text == "success":
            status = "UNKNOWN"
            reason = "Success is buyer-verified, but accepted_by/accepted_at evidence is missing."
        else:
            status = "UNPROVABLE"
            reason = "Delivery acceptance lacks buyer-independent verification."
        rows.append(_conclusion(
            "Delivery Accepted", status, outcome_id, outcome, "not_applicable", reason,
            "qualified_buyer_acceptance_evidence"))
    return rows


def _completion_statuses(manifest: Dict[str, Any], case: Dict[str, Any],
                         case_written: bool, economics: Optional[Dict[str, Any]],
                         conclusions: Dict[str, List[Dict[str, Any]]]) -> Dict[str, str]:
    """Return completion states, not PASS/FAIL evidence verdicts.

    An omitted outcomes source means outcome verification, delivery and business
    economics were not requested.  It does not make an otherwise closed finance
    period partial.  An outcomes source with missing/unverified rows remains
    applicable and partial, so callers cannot silence gaps by adding an empty file.
    """
    if not case_written or economics is None:
        return {field: "blocked" for field in
                ("finance_status", "verification_status", "delivery_status", "economics_status")}
    active_open = [item for item in case["open_items"] if item.get("status") != "resolved"]
    finance_open = any(item.get("domain", "finance") == "finance"
                       and item.get("kind") in period_intake.COST_OPEN_KINDS
                       for item in active_open)
    totals = economics["totals"]
    finance_complete = (case.get("financial_status") == "complete"
                        and totals["cost_status"] == "complete" and not finance_open)
    outcomes_applicable = "outcomes" in (manifest.get("sources") or {}) or bool(case["outcomes"])
    if not outcomes_applicable:
        verification = delivery = economics_status = "not_applicable"
    else:
        verification = "complete" if totals["success_denominator_status"] == "complete" else "partial"
        delivery_rows = conclusions["Delivery Accepted"]
        decisive_tasks = set()
        outcome_tasks = {_text(item.get("outcome_id")): _text(item.get("task_id"))
                         for item in case["outcomes"]}
        for row in delivery_rows:
            if row["status"] in ("PASS", "FAIL"):
                decisive_tasks.add(outcome_tasks.get(row["id"], ""))
        all_tasks_decided = all(task["task_id"] in decisive_tasks for task in case["tasks"])
        delivery = ("complete" if delivery_rows and all_tasks_decided
                    and all(row["status"] in ("PASS", "FAIL") for row in delivery_rows) else "partial")
        outcome_open = any(item.get("domain") == "outcome" for item in active_open)
        value_open = any(item.get("code") == "unverified_value" for item in economics["findings"])
        economics_status = ("complete" if finance_complete and verification == "complete"
                            and not outcome_open and not value_open else "partial")
    return {
        "finance_status": "complete" if finance_complete else "partial",
        "verification_status": verification,
        "delivery_status": delivery,
        "economics_status": economics_status,
    }


def run(bundle: Path) -> Dict[str, Any]:
    """Run the supported canonical slice and return all evidence gaps explicitly."""
    bundle = Path(bundle).resolve()
    manifest = period_intake.load_manifest(bundle)
    intake_rules, pricing_rules, allocation_rules = load_rule_map(bundle, manifest)
    _materialize_driver_csv(bundle, allocation_rules)
    _validate_scope(manifest, intake_rules)
    ingested = period_intake.intake(bundle)
    case, report = ingested["case"], ingested["report"]
    if report["currency_mismatches"]:
        raise CanonicalFinanceError("canonical case cannot cross currencies; split the bundle first")
    scope_pairs = {
        (_text(item.get("vendor")), _text(item.get("account_ref")))
        for item in case["cost_items"] + case["usage"] + report["reconciliation_rows"]
        if _text(item.get("vendor")) or _text(item.get("account_ref"))
    }
    expected_pair = (_text(manifest["vendor"]), _text(manifest["account_ref"]))
    for pair in scope_pairs:
        if pair[0] and pair[0] != expected_pair[0] or pair[1] and pair[1] != expected_pair[1]:
            raise CanonicalFinanceError("row crosses the single vendor/account scope")
    case["allocation_rules"] = [dict(rule, id=rule["rule_id"])
                                for rule in allocation_rules.values()]
    _recalculate(case, report, pricing_rules)
    report["financial_status"] = case["financial_status"]
    economics = task_economics.evaluate(case) if ingested["case_written"] else None
    conclusions = {name: [] for name in CONCLUSION_NAMES}
    for row in report["reconciliation_rows"]:
        for conclusion in _charge_conclusions(row):
            conclusions[conclusion["name"]].append(conclusion)
    conclusions["Delivery Accepted"] = _delivery_conclusions(case["outcomes"])
    statuses = _completion_statuses(
        manifest, case, ingested["case_written"], economics, conclusions)
    # ``status`` is a compatibility summary of completion, never an evidence
    # verdict.  FAIL can be a completely verified outcome; not_applicable axes
    # do not make a finance-only period partial.
    status = ("blocked" if statuses["finance_status"] == "blocked" else
              "complete" if all(value in ("complete", "not_applicable")
                                for value in statuses.values()) else "partial")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "verovo.canonical-finance",
        "case_id": case["case_id"], "status": status,
        **statuses,
        "status_semantics": {
            "finance_status": "Financial records, reconciliation gaps and cost allocation are closed.",
            "verification_status": "Applicable task outcomes have decisive buyer-procedural verification.",
            "delivery_status": "Every task has decisive Delivery Accepted PASS/FAIL evidence; FAIL is not incomplete.",
            "economics_status": "Applicable outcome economics, including cost and success/value evidence, are complete.",
            "status": "Backward-compatible completion summary across the four axes; not a PASS/FAIL verdict.",
        },
        "synthetic": manifest.get("synthetic") is True,
        "scope": case["scope"], "case": case, "intake_report": report,
        "economics": economics, "evidence_conclusions": conclusions,
        "rule_map": {
            "intake": sorted(intake_rules), "pricing": sorted(pricing_rules),
            "allocation": sorted(allocation_rules),
        },
        "boundary": (
            "Known CSV/JSONL, one vendor/account/currency and approved rules only. "
            "OCR/invoice2data remains unreviewed/unbooked. Rule repricing is not "
            "cross-source reconciliation; reconciliation and allocation are orthogonal. "
            "Buyer provenance checks are procedural metadata checks, not cryptographic "
            "identity proof; vendor agreement is not buyer verification. ROI is separate "
            "from Cost per Verified Success."
        ),
    }
