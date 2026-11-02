#!/usr/bin/env python3
"""Validate a Verovo Audit Case and every embedded Audit Finding.

The validator is deterministic and uses the Python standard library only. It
checks the contract the two schemas describe, plus the referential rules that a
JSON Schema cannot express by itself:

- a finding's case_id must equal the case's case_id;
- every evidence_ref must resolve to a real record inside the case's evidence
  root (absolute paths and traversal outside the root are refused);
- a non-provable finding must carry a reason and amount.value=null, never zero;
- a realized finding must carry a settlement_ref, because acceptance is not
  settlement.

Usage:
    python3 scripts/validate_audit_case.py examples/audit-golden-case/audit-case.json

Exit codes: 0 = valid, 1 = contract violations, 2 = unreadable input.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

CLASSIFICATIONS = (
    "duplicate_charge",
    "rate_mismatch",
    "missing_evidence",
    "currency_mismatch",
    "discount_not_applied",
    "refund_missing",
    "shared_cost_allocation",
    "unverified_outcome",
)
SEVERITIES = ("info", "low", "medium", "high", "critical")
FINDING_STATUSES = ("identified", "validated", "accepted", "realized", "dismissed")
CASE_STATUSES = ("open", "in_review", "closed", "dismissed")

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$"
)

_CASE_REQUIRED = (
    "case_id",
    "buyer",
    "vendor",
    "period",
    "contract_version",
    "scope",
    "coverage",
    "findings",
    "status",
)
_FINDING_REQUIRED = (
    "finding_id",
    "case_id",
    "classification",
    "severity",
    "amount",
    "status",
    "evidence_refs",
    "rule_ref",
    "provable",
)
_AMOUNT_REQUIRED = ("currency", "value", "basis")
_COVERAGE_REQUIRED = ("verified_amount", "unverified_amount", "currency")
_RULE_REQUIRED = ("rule_id", "version", "confirmed_by", "confirmed_at")
_EVIDENCE_REQUIRED = ("path", "record_id")

_CASE_FIELDS = set(_CASE_REQUIRED)
_FINDING_FIELDS = set(_FINDING_REQUIRED) | {"reason", "settlement_ref"}
_AMOUNT_FIELDS = set(_AMOUNT_REQUIRED)
_COVERAGE_FIELDS = set(_COVERAGE_REQUIRED)
_PERIOD_FIELDS = {"start", "end"}
_RULE_FIELDS = set(_RULE_REQUIRED)
_EVIDENCE_FIELDS = set(_EVIDENCE_REQUIRED) | {"id_field", "field", "note"}


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _show(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return repr(value)


def _check_required(obj: Dict[str, Any], required: Tuple[str, ...], label: str,
                    errors: List[str]) -> None:
    for name in required:
        if name not in obj:
            errors.append("%s.%s: missing required field" % (label, name))


def _check_unknown(obj: Dict[str, Any], allowed: set, label: str,
                   errors: List[str]) -> None:
    for name in sorted(set(obj) - allowed):
        errors.append("%s.%s: unknown field" % (label, name))


def _string_field(obj: Dict[str, Any], name: str, label: str,
                  errors: List[str]) -> Optional[str]:
    if name not in obj:
        return None
    value = obj[name]
    if not _is_nonempty_string(value):
        errors.append("%s.%s: required non-empty string" % (label, name))
        return None
    return value


def _is_date(value: Any) -> bool:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if _DATE_RE.fullmatch(value):
        return _is_date(value)
    if not _DATETIME_RE.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _walk_json_records(value: Any) -> Iterator[Dict[str, Any]]:
    """Yield every object in a JSON document, including objects in arrays."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                for record in _walk_json_records(child):
                    yield record
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                for record in _walk_json_records(item):
                    yield record


def _iter_records(path: Path) -> Iterator[Tuple[Dict[str, Any], str]]:
    """Yield (record, location) for a JSON, JSONL or CSV evidence file."""
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError("line %d is not JSON: %s" % (line_number, exc))
                if not isinstance(record, dict):
                    raise ValueError("line %d must contain an object" % line_number)
                yield record, "line %d" % line_number
        return

    if suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("not valid JSON: %s" % exc)
        for record in _walk_json_records(data):
            yield record, "JSON"
        return

    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ValueError("CSV has no header")
            for line_number, row in enumerate(reader, 2):
                cleaned: Dict[str, Any] = {}
                for key, value in row.items():
                    if key is None:
                        continue
                    cleaned[str(key).strip()] = value
                yield cleaned, "line %d" % line_number
        return

    raise ValueError(
        "unsupported evidence format %r; use .json, .jsonl or .csv" % suffix
    )


def _scalar_id(value: Any) -> Optional[str]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def resolve_evidence_ref(root: Path, ref: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Resolve one evidence ref to a record under *root*.

    The root is the audit case's evidence root. Paths are resolved, so a symlink
    that escapes the root is rejected just like a literal ``..`` path.
    """
    if not isinstance(ref, dict):
        return None, "evidence ref must be an object"
    root = Path(root).resolve()
    relative = ref.get("path")
    record_id = ref.get("record_id")
    if not _is_nonempty_string(relative):
        return None, "path must be a non-empty string"
    if not _is_nonempty_string(record_id):
        return None, "record_id must be a non-empty string"
    relative_path = Path(relative)
    if relative_path.is_absolute():
        return None, "path must be relative to the evidence root"

    try:
        target = (root / relative_path).resolve()
    except OSError as exc:
        return None, "cannot resolve path: %s" % exc
    try:
        target.relative_to(root)
    except ValueError:
        return None, "path escapes the evidence root"
    if not target.is_file():
        return None, "file does not exist under the evidence root: %s" % relative

    try:
        records = list(_iter_records(target))
    except (OSError, ValueError, csv.Error) as exc:
        return None, "cannot read evidence file: %s" % exc

    id_field = ref.get("id_field")
    if id_field is not None and not _is_nonempty_string(id_field):
        return None, "id_field must be a non-empty string when present"

    matches: List[Tuple[Dict[str, Any], str]] = []
    if id_field:
        for record, location in records:
            if id_field in record and _scalar_id(record.get(id_field)) == record_id:
                matches.append((record, location))
    else:
        # Default matching only considers identifier-like fields (id / *_id).
        # Matching every scalar value would make a reference in another field
        # (for example duplicate_of=line-002 on line-003) ambiguous.
        for record, location in records:
            for key, value in record.items():
                key_text = str(key)
                if (key_text == "id" or key_text.endswith("_id")) \
                        and _scalar_id(value) == record_id:
                    matches.append((record, location))
                    break

    if not matches:
        detail = (
            "record id %r not found in %s using identifier fields; "
            "set id_field when the identifier uses another field"
            % (record_id, relative)
        )
        if id_field:
            detail = "record id %r not found in %s using id_field %r" % (
                record_id, relative, id_field
            )
        return None, detail
    if len(matches) > 1:
        locations = ", ".join(sorted({location for _, location in matches}))
        return None, (
            "record id %r is ambiguous in %s (%d matches: %s)"
            % (record_id, relative, len(matches), locations)
        )

    record, _location = matches[0]
    field = ref.get("field")
    if field is not None:
        if not _is_nonempty_string(field):
            return None, "field must be a non-empty string when present"
        if field not in record:
            return None, "resolved record %r has no field %r" % (record_id, field)
    return record, None


def _validate_coverage(coverage: Any, errors: List[str]) -> None:
    label = "audit_case.coverage"
    if not isinstance(coverage, dict):
        errors.append("%s: expected object" % label)
        return
    _check_unknown(coverage, _COVERAGE_FIELDS, label, errors)
    _check_required(coverage, _COVERAGE_REQUIRED, label, errors)

    for name in ("verified_amount", "unverified_amount"):
        if name not in coverage:
            continue
        value = coverage[name]
        if value is None:
            continue
        if not _is_number(value):
            errors.append("%s.%s: expected a finite non-negative number or null"
                          % (label, name))
        elif value < 0:
            errors.append("%s.%s: must not be negative" % (label, name))

    if "currency" not in coverage:
        return
    currency = coverage["currency"]
    if not isinstance(currency, str) or not _CURRENCY_RE.fullmatch(currency):
        errors.append("%s.currency: expected an uppercase ISO-4217 code" % label)


def _validate_rule_ref(rule_ref: Any, label: str, errors: List[str]) -> None:
    if not isinstance(rule_ref, dict):
        errors.append("%s: expected object" % label)
        return
    _check_unknown(rule_ref, _RULE_FIELDS, label, errors)
    _check_required(rule_ref, _RULE_REQUIRED, label, errors)
    for name in ("rule_id", "version", "confirmed_by"):
        _string_field(rule_ref, name, label, errors)
    confirmed_at = _string_field(rule_ref, "confirmed_at", label, errors)
    if confirmed_at is not None and not _is_timestamp(confirmed_at):
        errors.append(
            "%s.confirmed_at: expected an ISO 8601 date or date-time" % label
        )


def _validate_evidence_ref(ref: Any, label: str, root: Path,
                           errors: List[str]) -> None:
    if not isinstance(ref, dict):
        errors.append("%s: expected object" % label)
        return
    _check_unknown(ref, _EVIDENCE_FIELDS, label, errors)
    _check_required(ref, _EVIDENCE_REQUIRED, label, errors)
    _string_field(ref, "path", label, errors)
    _string_field(ref, "record_id", label, errors)
    for name in ("id_field", "field", "note"):
        if name in ref and not isinstance(ref[name], str):
            errors.append("%s.%s: expected string" % (label, name))

    _record, error = resolve_evidence_ref(root, ref)
    if error:
        errors.append("%s: %s" % (label, error))


def _validate_amount(amount: Any, provable: Optional[bool], label: str,
                     errors: List[str]) -> None:
    if not isinstance(amount, dict):
        errors.append("%s: expected object" % label)
        return
    _check_unknown(amount, _AMOUNT_FIELDS, label, errors)
    _check_required(amount, _AMOUNT_REQUIRED, label, errors)

    currency = _string_field(amount, "currency", label, errors)
    if currency is not None and not _CURRENCY_RE.fullmatch(currency):
        errors.append("%s.currency: expected an uppercase ISO-4217 code" % label)
    _string_field(amount, "basis", label, errors)

    value = amount.get("value")
    if "value" in amount and value is not None:
        if not _is_number(value):
            errors.append("%s.value: expected a finite non-negative number or null"
                          % label)
        elif value < 0:
            errors.append("%s.value: must not be negative" % label)

    if provable is False:
        if "value" in amount and value is not None:
            errors.append(
                "%s.value: an UNPROVABLE finding must use null, never %s "
                "(a missing amount is not zero)" % (label, _show(value))
            )
    elif provable is True:
        if "value" in amount and value is None:
            errors.append(
                "%s.value: a provable finding must carry a numeric amount "
                "(use provable=false with a reason when it is unknown)" % label
            )


def _validate_finding(finding: Any, index: int, case_id: Optional[str],
                      root: Path, seen_ids: set, errors: List[str]) -> None:
    label = "audit_case.findings[%d]" % index
    if not isinstance(finding, dict):
        errors.append("%s: expected object" % label)
        return

    _check_unknown(finding, _FINDING_FIELDS, label, errors)
    _check_required(finding, _FINDING_REQUIRED, label, errors)

    finding_id = _string_field(finding, "finding_id", label, errors)
    if finding_id is not None:
        if not _ID_RE.fullmatch(finding_id):
            errors.append("%s.finding_id: invalid id format" % label)
        if finding_id in seen_ids:
            errors.append("%s.finding_id: duplicate finding_id %r"
                          % (label, finding_id))
        seen_ids.add(finding_id)

    finding_case_id = _string_field(finding, "case_id", label, errors)
    if finding_case_id is not None and case_id is not None \
            and finding_case_id != case_id:
        errors.append(
            "%s.case_id: %r does not match audit_case.case_id %r"
            % (label, finding_case_id, case_id)
        )

    classification = _string_field(finding, "classification", label, errors)
    if classification is not None and classification not in CLASSIFICATIONS:
        errors.append("%s.classification: unknown value %r; expected one of %s"
                      % (label, classification, ", ".join(CLASSIFICATIONS)))

    severity = _string_field(finding, "severity", label, errors)
    if severity is not None and severity not in SEVERITIES:
        errors.append("%s.severity: unknown value %r; expected one of %s"
                      % (label, severity, ", ".join(SEVERITIES)))

    status = _string_field(finding, "status", label, errors)
    if status is not None and status not in FINDING_STATUSES:
        errors.append("%s.status: unknown value %r; expected one of %s"
                      % (label, status, ", ".join(FINDING_STATUSES)))

    provable = finding.get("provable")
    if "provable" in finding and not isinstance(provable, bool):
        errors.append("%s.provable: expected boolean" % label)
        provable = None
    elif "provable" not in finding:
        provable = None

    if "amount" in finding:
        _validate_amount(finding["amount"], provable, label + ".amount", errors)

    reason_present = False
    if "reason" in finding:
        reason = _string_field(finding, "reason", label, errors)
        reason_present = reason is not None
    if provable is False and not reason_present:
        errors.append(
            "%s.reason: an UNPROVABLE finding requires a non-empty reason" % label
        )

    if "evidence_refs" in finding:
        evidence_refs = finding["evidence_refs"]
        if not isinstance(evidence_refs, list):
            errors.append("%s.evidence_refs: expected array" % label)
        elif not evidence_refs:
            errors.append("%s.evidence_refs: at least one evidence reference is required"
                          % label)
        else:
            for ref_index, ref in enumerate(evidence_refs):
                _validate_evidence_ref(
                    ref, "%s.evidence_refs[%d]" % (label, ref_index), root, errors
                )

    if "rule_ref" in finding:
        _validate_rule_ref(finding["rule_ref"], label + ".rule_ref", errors)

    settlement_present = False
    if "settlement_ref" in finding:
        settlement_ref = _string_field(finding, "settlement_ref", label, errors)
        settlement_present = settlement_ref is not None
    if status == "realized" and not settlement_present:
        errors.append(
            "%s.settlement_ref: a realized finding requires a settlement reference"
            % label
        )
    if status == "realized" and provable is not True:
        errors.append(
            "%s.provable: a realized finding must be provable" % label
        )


def validate_audit_case(case: Any, evidence_root: Optional[Any] = None) -> List[str]:
    """Return a deterministic list of contract errors (empty means valid).

    ``evidence_root`` defaults to the current working directory. The file helper
    below defaults to the audit case file's directory instead.
    """
    if not isinstance(case, dict):
        return ["audit_case: expected object"]

    errors: List[str] = []
    label = "audit_case"
    _check_unknown(case, _CASE_FIELDS, label, errors)
    _check_required(case, _CASE_REQUIRED, label, errors)

    case_id = _string_field(case, "case_id", label, errors)
    if case_id is not None and not _ID_RE.fullmatch(case_id):
        errors.append("%s.case_id: invalid id format" % label)

    for name in ("buyer", "vendor", "contract_version", "scope"):
        _string_field(case, name, label, errors)

    case_status = _string_field(case, "status", label, errors)
    if case_status is not None and case_status not in CASE_STATUSES:
        errors.append("%s.status: unknown value %r; expected one of %s"
                      % (label, case_status, ", ".join(CASE_STATUSES)))

    if "period" in case:
        period = case["period"]
        period_label = label + ".period"
        if not isinstance(period, dict):
            errors.append("%s: expected object" % period_label)
        else:
            _check_unknown(period, _PERIOD_FIELDS, period_label, errors)
            _check_required(period, ("start", "end"), period_label, errors)
            period_start = _string_field(period, "start", period_label, errors)
            period_end = _string_field(period, "end", period_label, errors)
            start_valid = period_start is not None and _is_date(period_start)
            end_valid = period_end is not None and _is_date(period_end)
            if period_start is not None and not start_valid:
                errors.append("%s.start: expected YYYY-MM-DD" % period_label)
            if period_end is not None and not end_valid:
                errors.append("%s.end: expected YYYY-MM-DD" % period_label)
            if start_valid and end_valid and period_start > period_end:
                errors.append("%s: start must not be after end" % period_label)

    if "coverage" in case:
        _validate_coverage(case["coverage"], errors)

    root = Path(evidence_root).expanduser().resolve() if evidence_root is not None \
        else Path.cwd().resolve()
    if not root.is_dir():
        errors.append("audit_case.evidence_root: not a directory: %s" % root)

    if "findings" in case:
        findings = case["findings"]
        if not isinstance(findings, list):
            errors.append("%s.findings: expected array" % label)
        else:
            seen_ids: set = set()
            for index, finding in enumerate(findings):
                _validate_finding(finding, index, case_id, root, seen_ids, errors)

    return errors


def validate_audit_case_file(path: Any,
                             evidence_root: Optional[Any] = None) -> List[str]:
    """Load an audit case file and validate it.

    Evidence paths resolve against ``evidence_root`` when supplied, otherwise
    against the directory containing the case file.
    """
    case_path = Path(path)
    try:
        case = json.loads(case_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return ["cannot read %s: %s" % (case_path, exc)]
    except UnicodeError as exc:
        return ["cannot decode %s as UTF-8: %s" % (case_path, exc)]
    except json.JSONDecodeError as exc:
        return ["%s is not valid JSON: %s" % (case_path, exc)]
    root = Path(evidence_root) if evidence_root is not None else case_path.parent
    return validate_audit_case(case, root)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate Verovo audit cases and their evidence references."
    )
    parser.add_argument(
        "case",
        nargs="+",
        help="audit case JSON file(s) to validate",
    )
    parser.add_argument(
        "--evidence-root",
        default=None,
        help="directory evidence paths resolve against (default: each case file's directory)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    exit_code = 0
    for raw_path in args.case:
        path = Path(raw_path)
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            print("ERROR %s: cannot read: %s" % (path, exc))
            exit_code = 2
            continue
        except UnicodeError as exc:
            print("ERROR %s: cannot decode as UTF-8: %s" % (path, exc))
            exit_code = 2
            continue
        except json.JSONDecodeError as exc:
            print("ERROR %s: not valid JSON: %s" % (path, exc))
            exit_code = 2
            continue

        root = Path(args.evidence_root) if args.evidence_root else path.parent
        errors = validate_audit_case(case, root)
        if errors:
            print("INVALID %s: %d error(s)" % (path, len(errors)))
            for error in errors:
                print("  - " + error)
            if exit_code == 0:
                exit_code = 1
        else:
            print("VALID %s" % path)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
