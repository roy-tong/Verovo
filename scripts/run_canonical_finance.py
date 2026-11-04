#!/usr/bin/env python3
"""Run the narrow canonical finance chain without creating another engine."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "healthcheck"))

from verovo_healthcheck import canonical_finance, period_intake, task_economics  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run Verovo canonical Period Intake + Rule Map + Task Economics")
    parser.add_argument("bundle", help="period bundle with manifest.json and an approved rule map")
    parser.add_argument("--out", required=True, help="JSON output path; source inputs are never overwritten")
    args = parser.parse_args(argv)
    bundle = Path(args.bundle).resolve()
    target = Path(args.out).resolve()
    try:
        manifest = period_intake.load_manifest(bundle)
        inputs = {bundle / "manifest.json"}
        if manifest.get("rule_map"):
            inputs.add((bundle / str(manifest["rule_map"])).resolve())
        for entries in manifest["sources"].values():
            for entry in entries:
                inputs.add((bundle / str(entry["path"])).resolve())
        resolved_inputs = {path.resolve() for path in inputs}
        if target in resolved_inputs or any(
                path.is_dir() and (target == path or path in target.parents)
                for path in resolved_inputs):
            raise canonical_finance.CanonicalFinanceError("output would overwrite a source input")
        if target.exists() and any(
                path.is_file() and target.samefile(path) for path in resolved_inputs):
            raise canonical_finance.CanonicalFinanceError("output is a hard link to a source input")
        if not target.parent.is_dir():
            raise canonical_finance.CanonicalFinanceError("output parent does not exist: %s" % target.parent)
        result = canonical_finance.run(bundle)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (canonical_finance.CanonicalFinanceError, period_intake.IntakeError,
            task_economics.EconomicsError, OSError, json.JSONDecodeError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    totals = (result.get("economics") or {}).get("totals") or {}
    print("wrote %s canonical case (%s) -> %s" % (
        result["case_id"], result["status"], target))
    print("finance=%s verification=%s delivery=%s economics=%s" % (
        result["finance_status"], result["verification_status"],
        result["delivery_status"], result["economics_status"]))
    print("fully_loaded_cost=%r cost_per_verified_success=%r roi=%r" % (
        totals.get("fully_loaded_cost"), totals.get("cost_per_verified_success"), totals.get("roi")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
