"""verovo-mcp — minimal buyer-side MCP server (stdio JSON-RPC 2.0, zero deps).

Exposes the buyer review and economics entry points as agent tools. Local reads
and computation are allowed, plus explicitly named local state/output files:

  verovo_economics_ops  monitor local usage state, return advisory budget
                         signals, review observed optimization changes, or
                         export a shared tool-value experiment contract
  verovo_review          run a buyer review from a task file (or the synthetic
                         sample); returns lanes, per-criterion verdicts,
                         conclusion levels (rule consistency / evidence
                         credibility / buyer decision) and the boundary text
  verovo_review_template the deliberately incomplete task template (an agent
                         fills scope/criteria; it must not invent rules)
  verovo_recount         Tier 1 recount: apply a vendor's own published rules
                         to a counts-only export (or the bundled sample)

Deliberately NOT exposed: network access, dispute submission, supplier contact,
external runtime control, or automatic rollback. The buyer decision (Level 3)
stays with the human; `dispute`/`recovery` remain CLI-only human-driven flows.

Run: `verovo-mcp` (console script) or `python3 -m verovo_healthcheck.mcp_server`.
"""
import json
import os
import sys
from pathlib import Path

from . import __version__
from . import review
from . import invoice_intake
from . import period_intake
from . import cost_drivers
from . import optimization
from . import budget_control
from . import budget_recovery
from . import control_receipt
from . import experiment_export
from . import experiment_runner
from verovo_local_runner import run_process
from . import monitor_state
from . import monitor_series
from . import monitor_alerts
from . import alert_resolution
from . import spend_forecast
from . import optimization_review
from . import optimization_loop
from . import human_baseline
from . import tool_value
from . import vendors as vendors_mod

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "verovo_invoice_intake",
        "description": "Read a local supplier invoice through optional invoice2data. "
                       "Returns an unreviewed source-hashed draft, NEVER books a cost "
                       "or claims invoice authenticity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "local invoice path"},
            },
            "required": ["file"],
        },
    },
    {
        "name": "verovo_period_intake",
        "description": "Assemble a task-economics case from a local billing-period bundle "
                       "(manifest + traces/bills/outcomes). Returns the case and an intake "
                       "report that keeps unallocated, mismatched and unverified rows as open "
                       "items; no network, no guessed tax/FX, no booking.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bundle": {"type": "string", "description": "local directory with manifest.json"},
            },
            "required": ["bundle"],
        },
    },
    {
        "name": "verovo_optimize",
        "description": "Cost drivers plus ROI-driven proposals for a local case, with optional "
                       "explicit scenario simulation. Unknown buckets stay unknown; every proposal "
                       "requires a human and is never auto-applied.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case": {"type": "string", "description": "task-economics case JSON"},
                "scenario": {"type": "string", "description": "optional scenario JSON path"},
            },
            "required": ["case"],
        },
    },
    {
        "name": "verovo_economics_ops",
        "description": "Run local spend monitoring, advisory budget checks, optimization "
                       "review, or shared tool-value experiment export. Only explicit local "
                       "state/output paths are written. No network, external enforcement, "
                       "automatic rollback, supplier contact, causal claim, or realized-saving claim.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": [
                    "monitor_track", "monitor_freeze_baseline", "monitor_series", "monitor_notify", "monitor_forecast", "alert_resolution", "budget_check", "budget_verify", "budget_resume",
                    "optimize_verify", "optimization_loop_create", "optimization_loop_tick", "optimization_loop_status",
                    "optimization_loop_pause", "optimization_loop_resume", "human_baseline", "experiment_run",
                    "tool_value_analyze", "tool_value_export",
                ]},
                "file": {"type": "string", "description": "local usage/task JSONL or CSV path"},
                "economics_case": {"type": "string", "description": "canonical task-economics case JSON for derived tool-value input"},
                "state": {"type": "string", "description": "local monitoring state JSON path"},
                "dir": {"type": "string", "description": "existing local optimization-loop directory"},
                "at": {"type": "string", "description": "optimization-loop current timezone-aware ISO timestamp"},
                "budget": {"type": "string", "description": "local spend-budget JSON path"},
                "decision": {"type": "string", "description": "local spend-control-decision JSON path"},
                "receipt": {"type": "string", "description": "local spend-control-receipt JSON path"},
                "recovery": {"type": "string", "description": "local spend-budget-recovery JSON path"},
                "baseline": {"type": "string", "description": "local baseline task JSONL/CSV path"},
                "final": {"type": "string", "description": "local final task JSONL/CSV path"},
                "human": {"type": "string", "description": "human-baseline human task JSONL/CSV path"},
                "agent": {"type": "string", "description": "human-baseline agent task JSONL/CSV path"},
                "contract": {"type": "string", "description": "local optimization-review JSON path"},
                "design": {"type": "string", "description": "local experiment-design JSON path"},
                "out": {"type": "string", "description": "new local experiment-contract JSON or alert-outbox JSONL path"},
                "alert_id": {"type": "string", "description": "alert_resolution: alert id in file"},
                "granularity": {"type": "string", "enum": ["minute", "hour", "day"],
                                "description": "monitor_series UTC bucket size"},
                "scope": {"type": "object", "description": "monitor_series optional task_id/tool/model scope"},
                "start": {"type": "string", "description": "monitor_series inclusive ISO start"},
                "end": {"type": "string", "description": "monitor_series exclusive ISO end"},
                "severity": {"type": "string", "enum": ["low", "medium", "high"],
                             "description": "monitor_notify: minimum anomaly severity"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "verovo_review",
        "description": "Run a scoped buyer review (charges / delivery as separate "
                       "conclusions) from a local task file, or the synthetic demo "
                       "sample. Read-only: local JSON in, verdicts out. Verdicts are "
                       "PASS/FAIL/UNPROVABLE per criterion; conclusion levels state "
                       "what the run establishes (rule consistency) and what it does "
                       "not (evidence credibility, buyer decision).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_path": {"type": "string",
                              "description": "path to a buyer-review task JSON "
                                             "(see verovo_review_template)"},
                "sample": {"type": "boolean", "default": False,
                           "description": "run the bundled synthetic demo task"},
            },
        },
    },
    {
        "name": "verovo_review_template",
        "description": "The deliberately incomplete buyer-review task template. "
                       "An agent fills scope and criteria from the buyer's contract "
                       "and authorized observations; it must not invent rules, "
                       "versions or missing facts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "verovo_recount",
        "description": "Tier 1 recount: apply a vendor's own published billing "
                       "rules to a counts-only export CSV (local read + arithmetic). "
                       "Use --sample via sample=true for the bundled demo. Vendor ids "
                       "are listed by the CLI (`verovo recount --list-vendors`).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sample": {"type": "boolean", "default": False},
                "export_path": {"type": "string",
                                "description": "path to the counts-only export CSV"},
                "vendor": {"type": "string", "default": "zendesk"},
            },
        },
    },
]


def _text(payload):
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, ensure_ascii=False, indent=1)}]}


def _err(message):
    return {"content": [{"type": "text", "text": str(message)}], "isError": True}


def _run_review(args):
    sample = bool(args.get("sample"))
    task_path = args.get("task_path")
    if sample and task_path:
        return _err("sample and task_path are mutually exclusive")
    if not sample and not task_path:
        return _err("provide task_path or sample=true")
    if sample:
        demo = os.path.join(os.path.dirname(os.path.abspath(review.__file__)),
                            "demo", "buyer-review.json")
        task_path = demo
    result = review.evaluate(task_path, synthetic=sample)
    return _text({
        "result": result,
        "report_text": review.report_text(result),
    })


def _run_recount(args):
    export_path = args.get("export_path")
    if args.get("sample"):
        if export_path:
            return _err("sample and export_path are mutually exclusive")
        export_path = os.path.join(
            os.path.dirname(os.path.abspath(vendors_mod.__file__)),
            "demo", "sample-recount-export.csv")
    if not export_path:
        return _err("provide export_path or sample=true")
    vendor = args.get("vendor") or "zendesk"
    export = vendors_mod.load_export(export_path)
    result = vendors_mod.recount(export, vendor)
    return _text({
        "vendor": vendor,
        "export": export_path,
        "result": result,
        "report_text": vendors_mod.recount_report(result),
    })


def _run_economics_ops(args):
    action = args.get("action")
    required = {
        "monitor_track": ("file", "state"),
        "monitor_freeze_baseline": ("file", "state"),
        "monitor_series": ("state",),
        "monitor_notify": ("state", "out"),
        "monitor_forecast": ("state", "contract"),
        "alert_resolution": ("file", "alert_id"),
        "budget_check": ("state", "budget"),
        "budget_verify": ("state", "decision", "receipt"),
        "budget_resume": ("state", "budget", "recovery", "out"),
        "optimize_verify": ("baseline", "final", "contract"),
        "optimization_loop_create": ("contract", "out"),
        "optimization_loop_tick": ("dir",),
        "optimization_loop_status": ("dir",),
        "optimization_loop_pause": ("dir",),
        "optimization_loop_resume": ("dir",),
        "human_baseline": ("human", "agent", "contract"),
        "experiment_run": ("contract", "out"),
        "tool_value_analyze": (),
        "tool_value_export": ("file", "design", "out"),
    }
    if action not in required:
        return _err("unknown economics action: %s" % action)
    missing = [key for key in required[action] if not args.get(key)]
    if missing:
        return _err("%s requires: %s" % (action, ", ".join(missing)))
    if action == "tool_value_analyze" and bool(args.get("file")) == bool(args.get("economics_case")):
        return _err("tool_value_analyze requires exactly one of file or economics_case")
    if action in ("monitor_track", "monitor_freeze_baseline"):
        return _text(monitor_state.track_file(
            args["file"], args["state"],
            freeze=action == "monitor_freeze_baseline"))
    if action == "budget_check":
        budget = json.loads(Path(args["budget"]).read_text(encoding="utf-8"))
        return _text(budget_control.evaluate(args["state"], budget))
    if action == "budget_verify":
        decision = json.loads(Path(args["decision"]).read_text(encoding="utf-8"))
        receipt = json.loads(Path(args["receipt"]).read_text(encoding="utf-8"))
        return _text(control_receipt.evaluate(args["state"], decision, receipt))
    if action == "budget_resume":
        budget = json.loads(Path(args["budget"]).read_text(encoding="utf-8"))
        recovery = json.loads(Path(args["recovery"]).read_text(encoding="utf-8"))
        return _text(budget_recovery.evaluate(args["state"], budget, recovery, args["out"]))
    if action == "monitor_series":
        return _text(monitor_series.build(
            args["state"], granularity=args.get("granularity") or "day",
            scope=args.get("scope") or {}, start=args.get("start") or None,
            end=args.get("end") or None))
    if action == "monitor_notify":
        return _text(monitor_alerts.emit(
            args["state"], args["out"], min_severity=args.get("severity") or "low"))
    if action == "monitor_forecast":
        spec = json.loads(Path(args["contract"]).read_text(encoding="utf-8"))
        return _text(spend_forecast.evaluate(args["state"], spec))
    if action == "alert_resolution":
        alert = alert_resolution.load_alert(args["file"], args["alert_id"])
        return _text(alert_resolution.from_alert(alert))
    if action == "experiment_run":
        spec = json.loads(Path(args["contract"]).read_text(encoding="utf-8"))
        return _text(experiment_runner.run(spec, args["out"], process_runner=run_process))
    if action == "human_baseline":
        contract = json.loads(Path(args["contract"]).read_text(encoding="utf-8"))
        return _text(human_baseline.evaluate(args["human"], args["agent"], contract))
    if action == "optimize_verify":
        baseline = tool_value.load_tasks(args["baseline"])
        final = tool_value.load_tasks(args["final"])
        contract = json.loads(Path(args["contract"]).read_text(encoding="utf-8"))
        return _text(optimization_review.evaluate(baseline, final, contract))
    if action.startswith("optimization_loop_"):
        loop_action = action.replace("optimization_loop_", "")
        if loop_action == "create":
            spec = json.loads(Path(args["contract"]).read_text(encoding="utf-8"))
            return _text(optimization_loop.create(spec, args["out"]))
        if loop_action == "tick":
            return _text(optimization_loop.tick(args["dir"], now=args.get("at") or ""))
        if loop_action == "status":
            return _text(optimization_loop.status(args["dir"], now=args.get("at") or ""))
        return _text(optimization_loop.set_paused(
            args["dir"], loop_action == "pause", now=args.get("at") or ""))
    tasks = (tool_value.load_economics(args["economics_case"]) if args.get("economics_case")
             else tool_value.load_tasks(args["file"]))
    if action == "tool_value_analyze":
        return _text(tool_value.analyze(tasks))
    analysis = tool_value.analyze(tasks)
    design = json.loads(Path(args["design"]).read_text(encoding="utf-8"))
    document = experiment_export.from_tool_value(
        tasks, analysis, args["file"], design, __version__)
    out = experiment_export.write_new(document, args["out"])
    return _text({
        "status": "exported",
        "schema": document["schema"],
        "experiment_id": document["experiment_id"],
        "observations": len(document["observations"]),
        "outcome": document["outcome"]["status"],
        "path": str(out),
    })


def call_tool(name, args):
    if name == "verovo_economics_ops":
        return _run_economics_ops(args)
    if name == "verovo_invoice_intake":
        if not args.get("file"):
            return _err("provide a local invoice file")
        return _text(invoice_intake.extract_invoice(args["file"]))
    if name == "verovo_period_intake":
        if not args.get("bundle"):
            return _err("provide a bundle directory with manifest.json")
        return _text(period_intake.intake(args["bundle"]))
    if name == "verovo_optimize":
        if not args.get("case"):
            return _err("provide a case JSON path")
        case = json.loads(Path(args["case"]).read_text(encoding="utf-8"))
        scenarios = None
        if args.get("scenario"):
            scenarios = json.loads(Path(args["scenario"]).read_text(encoding="utf-8"))
        return _text(optimization.propose(case, scenarios=scenarios))
    if name == "verovo_review":
        return _run_review(args)
    if name == "verovo_review_template":
        return _text(review.template())
    if name == "verovo_recount":
        return _run_recount(args)
    return _err("unknown tool: %s" % name)


def handle(message):
    method = message.get("method", "")
    mid = message.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "verovo-mcp", "version": "0.1.0"},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        try:
            result = call_tool(message["params"]["name"],
                               message["params"].get("arguments", {}))
        except Exception as exc:  # surface as tool error, not a crash
            result = _err("%s: %s" % (type(exc).__name__, exc))
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": "method not found: %s" % method}}
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
