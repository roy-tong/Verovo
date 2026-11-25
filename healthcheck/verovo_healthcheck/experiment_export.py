"""Shared experiment projection for comparable Verovo tool-value tasks."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

SCHEMA = "ovo.experiment-contract/1"
GRADES = {"observed", "reported", "derived", "independently_verified"}


def design_hash(document):
    projection = {key: document.get(key) for key in
                  ("hypothesis", "design", "window", "stop_conditions")}
    canonical = json.dumps(projection, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def from_tool_value(tasks, analysis, source_path, spec, origin_version):
    if not isinstance(spec, dict):
        raise ValueError("experiment design must be an object")
    required = ("experiment_id", "task_id", "title", "hypothesis", "design", "window",
                "stop_conditions", "registered_by", "registered_at", "evidence_grade")
    for key in required:
        if not spec.get(key):
            raise ValueError("experiment export requires %s" % key)
    for key in ("external_actions", "eligible"):
        if key not in spec:
            raise ValueError("experiment export requires %s" % key)
    if spec["external_actions"] is True and not spec.get("authorization_refs"):
        raise ValueError("external_actions requires authorization_refs")
    if spec["evidence_grade"] not in GRADES:
        raise ValueError("unknown evidence_grade")
    source = Path(source_path)
    source_hash = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    design = spec["design"]
    comparability = design.get("comparability") if isinstance(design, dict) else {}
    if not isinstance(comparability, dict):
        comparability = {}
    comparable_groups = {str(row.get("comparable_group") or "") for row in tasks}
    if comparability.get("same_task_set") is True and (
            not comparable_groups or comparable_groups == {""} or len(comparable_groups) != 1):
        raise ValueError("same_task_set requires one non-empty comparable_group")

    observations = []
    for task in tasks:
        observed_at = str(task.get("observed_at") or "").strip()
        if not observed_at:
            raise ValueError("task %s needs observed_at" % task["task_id"])
        base = {
            "arm_id": task["group"],
            "subject_ref": task["task_id"],
            "observed_at": observed_at,
            "source_hash": source_hash,
            "evidence_grade": spec["evidence_grade"],
            "eligible": spec["eligible"] is True,
            "exclusion_reason": "" if spec["eligible"] is True else str(
                spec.get("exclusion_reason") or "Task is not qualified as experiment evidence."),
        }
        observations.append(dict(base, **{
            "observation_id": "%s:success" % task["task_id"],
            "metric_id": "task_success",
            "source_ref": "%s#%s/success" % (source.resolve(), task["task_id"]),
            "value_number": 1 if task["success"] else 0,
        }))
        observations.append(dict(base, **{
            "observation_id": "%s:cost" % task["task_id"],
            "metric_id": "task_cost",
            "source_ref": "%s#%s/cost" % (source.resolve(), task["task_id"]),
            "value_text": str(task["cost"]),
        }))
    has_two_arms = bool((analysis.get("treatment") or {}).get("n")
                        and (analysis.get("control") or {}).get("n"))
    outcome = copy.deepcopy(spec.get("outcome") or {
        "status": "inconclusive" if observations else "not_evaluated",
        "causality": "descriptive_association" if has_two_arms else "not_claimed",
        "evidence_refs": [row["observation_id"] for row in observations],
        "evaluated_by": "",
        "evaluated_at": "",
        "notes": "Tool-value analysis is descriptive; incremental contribution needs a qualified design and review.",
    })
    document = {
        "schema": SCHEMA,
        "experiment_id": str(spec["experiment_id"]),
        "origin_product": "verovo",
        "origin_version": str(origin_version),
        "task_id": str(spec["task_id"]),
        "title": str(spec["title"]),
        "state": str(spec.get("state") or ("completed" if observations else "registered")),
        "hypothesis": copy.deepcopy(spec["hypothesis"]),
        "design": copy.deepcopy(design),
        "window": copy.deepcopy(spec["window"]),
        "stop_conditions": list(spec["stop_conditions"]),
        "external_actions": spec["external_actions"],
        "authorization_refs": list(spec.get("authorization_refs") or []),
        "registered_by": str(spec["registered_by"]),
        "registered_at": str(spec["registered_at"]),
        "observations": observations,
        "outcome": outcome,
    }
    if spec.get("decision"):
        document["decision"] = copy.deepcopy(spec["decision"])
    document["design_sha256"] = design_hash(document)
    return document


def write_new(document, path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return target
