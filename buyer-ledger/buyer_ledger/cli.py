"""buyer-ledger CLI：全部本地运行。

  python3 -m buyer_ledger init --db ledger.db
  python3 -m buyer_ledger import bill.csv --provider example --tier bill_fact --period 2026-10
  python3 -m buyer_ledger parse --document <id> --agent
  python3 -m buyer_ledger policy policy.json
  python3 -m buyer_ledger price --provider example --period 2026-10
  python3 -m buyer_ledger recipe validate recipe.json / dryrun recipe.json
  python3 -m buyer_ledger session login --recipe recipe.json
  python3 -m buyer_ledger fetch --recipe recipe.json --period 2026-10
  python3 -m buyer_ledger catalog summary
  python3 -m buyer_ledger catalog list --region cn --role buyer
  python3 -m buyer_ledger session cdp --endpoint http://127.0.0.1:9222
  python3 -m buyer_ledger fetch --recipe recipe.json --period 2026-10 --cdp ...
  python3 -m buyer_ledger reconcile --period 2026-10
  python3 -m buyer_ledger report --period 2026-10 --perspective seller --out report.md
  python3 -m buyer_ledger show <row-id> / status / audit verify <trail.jsonl>
"""

import argparse
import json
import sys
from pathlib import Path

from . import bill_candidate as bc, browser, catalog as catalog_mod, close as close_mod, ingest as ingest_mod, reconcile as recon_mod, report as report_mod, reproduce as reproduce_mod, reproduce_proof
from .llm import LLMClient, LLMError
from .model import LedgerError, PricingPolicy
from .store import LedgerStore

DEFAULT_DB = "buyer-ledger.db"


def _store(args):
    return LedgerStore(args.db or DEFAULT_DB)


def cmd_init(args):
    store = _store(args)
    print("ledger ready: %s" % (args.db or DEFAULT_DB))
    store.close()


def cmd_import(args):
    store = _store(args)
    llm = LLMClient() if args.agent else None
    result = ingest_mod.import_file(
        store, args.file, provider=args.provider, tier=args.tier,
        period=args.period, doc_type=args.doc_type, account_ref=args.account,
        use_agent_if_new=args.agent, llm_client=llm)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    store.close()


def cmd_parse(args):
    store = _store(args)
    doc = store.get_document(args.document)
    if not doc:
        print("document not found: %s" % args.document)
        sys.exit(1)
    if doc["tier"] not in ("bill_fact", "platform_usage"):
        print("reference-tier document; nothing to parse")
        store.close()
        return
    text, current_hash = ingest_mod.read_and_hash(doc["file_path"])
    if current_hash != doc["content_hash"]:
        store.close()
        raise ValueError("source file changed after registration; import it as a new document")
    fingerprint = ingest_mod.parsing.format_fingerprint(doc["file_name"], text)
    scope = {"provider": doc["provider"], "doc_type": doc["doc_type"], "tier": doc["tier"]}
    rule = store.get_parse_rule(fingerprint, **scope)
    if rule is None:
        if not args.agent:
            store.close()
            print("no approved rule for fingerprint %s; rerun with --agent" % fingerprint)
            sys.exit(1)
        spec, golden = ingest_mod.parsing.agent_generate_spec(
            doc["file_name"], text, doc["provider"], doc["doc_type"], LLMClient())
        store.put_parse_rule(fingerprint, spec, created_via="agent",
                             golden=golden, approved=False, **scope)
        print(json.dumps({"fingerprint": fingerprint, "spec_source": "agent_draft",
                          "review_required": True, "spec": spec,
                          "next": "rule approve %s --provider %s --doc-type %s --tier %s" %
                                  (fingerprint, doc["provider"], doc["doc_type"], doc["tier"])},
                         ensure_ascii=False, indent=2))
        store.close()
        return
    if not rule["approved"]:
        print(json.dumps({"review_required": True, "fingerprint": fingerprint,
                          "spec": rule["spec"],
                          "next": "run rule approve, then rerun parse"},
                         ensure_ascii=False, indent=2))
        store.close()
        return
    from .model import SourceDocument
    doc_obj = SourceDocument(
        content_hash=doc["content_hash"], provider=doc["provider"], tier=doc["tier"],
        account_ref=doc["account_ref"], doc_type=doc["doc_type"], period=doc["period"],
        file_path=doc["file_path"], file_name=doc["file_name"])
    result = ingest_mod.parsing.apply_spec(
        doc["file_name"], text, rule["spec"], doc_obj, doc["provider"],
        period=doc["period"], review_status="verified")
    n_lines = n_events = 0
    try:
        for cl in result["charge_lines"]:
            _, inserted = store.put_charge_line(cl, commit=False)
            n_lines += inserted
        for ue in result["usage_events"]:
            _, inserted = store.put_usage_event(ue, commit=False)
            n_events += inserted
        store.conn.commit()
    except Exception:
        store.conn.rollback()
        raise
    print(json.dumps({"fingerprint": fingerprint, "spec_via": rule.get("created_via"),
                      "charge_lines": n_lines, "usage_events": n_events,
                      "notes": result["notes"], "constraint": result["constraint"]},
                     ensure_ascii=False, indent=2))
    store.close()


def cmd_rule(args):
    store = _store(args)
    if args.action == "list":
        rows = [{k: r[k] for k in ("fingerprint", "provider", "doc_type", "tier",
                                   "created_via", "approved")}
                for r in store.list_parse_rules()]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        scope = {"provider": args.provider, "doc_type": args.doc_type, "tier": args.tier}
        if args.action == "show":
            rule = store.get_parse_rule(args.fingerprint, **scope)
            if not rule:
                store.close()
                raise ValueError("parse rule not found")
            print(json.dumps(rule, ensure_ascii=False, indent=2))
        else:
            rule = store.get_parse_rule(args.fingerprint, **scope)
            if not rule:
                store.close()
                raise ValueError("parse rule not found")
            errors = ingest_mod.parsing.validate_spec(rule["spec"])
            if errors:
                store.close()
                raise ValueError("refusing invalid rule: %s" % "; ".join(errors))
            store.approve_parse_rule(args.fingerprint, **scope)
            print("rule approved; rerun `parse --document <doc-id>` to apply it")
    store.close()


def cmd_preset(args):
    from . import presets as presets_mod
    if args.action == "list":
        for name, p in presets_mod.PRESETS.items():
            print("%-22s %-12s %-8s %s" % (name, p["doc_type"], p["tier"], p["status"]))
        return
    if args.action == "show":
        if args.name not in presets_mod.PRESETS:
            raise ValueError("preset not found: %s" % args.name)
        p = presets_mod.PRESETS[args.name]
        print(json.dumps({"name": args.name, "provider": p["provider"],
                          "doc_type": p["doc_type"], "tier": p["tier"],
                          "status": p["status"],
                          "expected_header": p["header"], "spec": p["spec"]},
                         ensure_ascii=False, indent=2))
        return
    # apply：登记为已批准规则；真实文件表头一致时 import 直接重放
    store = _store(args)
    try:
        fingerprint = presets_mod.apply_preset(store, args.name)
    except KeyError:
        store.close()
        raise ValueError("preset not found: %s" % args.name)
    p = presets_mod.PRESETS[args.name]
    print(json.dumps({"applied": args.name, "provider": p["provider"],
                      "doc_type": p["doc_type"], "tier": p["tier"],
                      "fingerprint": fingerprint, "created_via": "preset"},
                     ensure_ascii=False, indent=2))
    store.close()


def cmd_catalog(args):
    catalog = catalog_mod.load_catalog()
    if args.action == "summary":
        print(json.dumps(catalog_mod.support_summary(catalog), ensure_ascii=False, indent=2))
        return
    if args.action == "show":
        try:
            provider = catalog_mod.get_provider(args.provider, catalog)
        except KeyError as exc:
            raise ValueError(str(exc))
        print(json.dumps(provider, ensure_ascii=False, indent=2))
        return
    if args.action == "coverage":
        store = _store(args)
        period = args.period or None
        providers = {r["provider"] for r in store.effective_charge_lines(period=period)} | {
            r["provider"] for r in store.effective_usage_events(period=period)}
        store.close()
        print(json.dumps(catalog_mod.coverage(providers, catalog),
                         ensure_ascii=False, indent=2))
        return
    providers = catalog_mod.list_providers(
        region=args.region, role=args.role, category=args.category,
        query=args.query, catalog=catalog)
    if args.json:
        print(json.dumps(providers, ensure_ascii=False, indent=2))
        return
    print("%-28s %-6s %-18s %-11s %-16s %s" %
          ("ID", "REGION", "CATEGORY", "ROLES", "READER", "NAME"))
    for p in providers:
        print("%-28s %-6s %-18s %-11s %-16s %s" % (
            p["id"], p["region"], p["category"], ",".join(p["roles"]),
            p["reader_status"], p["name"]))
    print("\n%d tools; cataloged != live-validated" % len(providers))


def cmd_policy(args):
    store = _store(args)
    with open(args.file, "r", encoding="utf-8") as f:
        data = json.load(f)
    policies = data if isinstance(data, list) else [data]
    for p in policies:
        store.put_policy(PricingPolicy(**p))
        print("policy saved: %s@%s (%s/%s)" % (p["policy_id"], p["version"],
                                               p["provider"], p.get("capability", "")))
    store.close()


def cmd_price(args):
    store = _store(args)
    print(json.dumps(recon_mod.apply_pricing(store, args.provider, args.period),
                     ensure_ascii=False))
    store.close()


def cmd_reconcile(args):
    store = _store(args)
    results = recon_mod.reconcile(store, args.period)
    for r in results:
        def show(value):
            return "—" if value in (None, "") else value
        print("%-16s account=%-12s currency=%-4s bill=%-12s recount=%-12s "
              "variance=%-12s %s" % (
                  r["provider"], r["account_ref"] or "—", r["currency"] or "—",
                  show(r["bill_total"]), show(r["recount_total"]),
                  show(r["variance"]), r["variance_class"]))
    store.close()


def cmd_report(args):
    store = _store(args)
    results = store.list_reconciliations(args.period)
    if not results:
        print("no reconciliation for %s; run reconcile first" % args.period)
        sys.exit(1)
    docs = store.list_documents(period=args.period)
    rendered = []
    for row in results:
        detail = json.loads(row["detail_json"])
        rendered.append({"period": row["period"], "provider": row["provider"],
                         "account_ref": row["account_ref"], "currency": row["currency"],
                         "bill_total": row["bill_total"],
                         "recount_total": row["recount_total"],
                         "recount_complete": detail.get("recount_complete", True),
                         "variance": row["variance"],
                         "variance_class": row["variance_class"],
                         "detail": detail})
    catalog_coverage = catalog_mod.coverage(
        [row["provider"] for row in results])
    md = report_mod.render_report(
        args.period, rendered, documents_count=len(docs),
        ledger_path=args.db or DEFAULT_DB,
        catalog_coverage=catalog_coverage,
        perspective=getattr(args, "perspective", "buyer"))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md)
        print("report written: %s" % args.out)
    else:
        print(md)
    store.close()


def cmd_run(args):
    """日常轻量入口：为账期内全部 provider 复算，然后对账并生成报告。"""
    store = _store(args)
    providers = sorted({e["provider"] for e in
                        store.effective_usage_events(period=args.period)})
    pricing = {provider: recon_mod.apply_pricing(store, provider, args.period)
               for provider in providers}
    store.close()
    print(json.dumps({"pricing": pricing}, ensure_ascii=False))
    cmd_reconcile(args)
    cmd_report(args)


def cmd_recipe(args):
    recipe = browser.load_recipe(args.file)
    if args.cmd == "validate":
        errors = browser.validate_recipe(recipe)
        if errors:
            print("INVALID:\n" + "\n".join("- " + e for e in errors))
            sys.exit(1)
        print("recipe OK: %s (%d steps, readonly)" % (recipe["recipe_id"],
                                                      len(recipe["steps"])))
    else:
        print(json.dumps(browser.dry_run(recipe), ensure_ascii=False, indent=2))


def cmd_session(args):
    if args.action == "cdp":
        if not args.endpoint:
            raise ValueError("--endpoint is required for session cdp")
        print(json.dumps(browser.cdp_health(args.endpoint), ensure_ascii=False, indent=2))
        return
    if not args.recipe:
        raise ValueError("--recipe is required for session login/status")
    recipe = browser.load_recipe(args.recipe)
    if args.action == "status":
        status = browser.session_health(recipe, account_ref=args.account)
        print(json.dumps({"status": status, "account": args.account}, ensure_ascii=False))
        sys.exit(0 if status == "ok" else 2)
    ok = browser.open_login_session(recipe, account_ref=args.account)
    sys.exit(0 if ok else 1)


def cmd_fetch(args):
    recipe = browser.load_recipe(args.recipe)
    store = _store(args)
    doc_ids = []

    def on_document(path, name, provenance):
        result = ingest_mod.import_file(
            store, path, provider=recipe["provider"],
            tier=recipe.get("tier", "bill_fact"), period=args.period,
            doc_type=recipe.get("doc_type", "bill"), account_ref=args.account,
            fetched_via="browser_recipe:" + recipe["recipe_id"],
            fetched_at=provenance["fetched_at"], extra=provenance)
        doc_ids.append(result)
        print("ingested: %s" % json.dumps(result, ensure_ascii=False))
        return result

    try:
        summary = browser.run_recipe(recipe, period=args.period,
                                     headless=not args.headed,
                                     on_document=on_document, account_ref=args.account,
                                     cdp_endpoint=args.cdp)
        summary["ingestions"] = doc_ids
        summary["needs_parse"] = any(
            not d.get("duplicate") and not (d.get("charge_lines") or d.get("usage_events"))
            for d in doc_ids)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if summary["needs_parse"]:
            print("采集完成但尚未形成账本行：请完成解析规则审核后运行 parse。")
            sys.exit(2)
    except browser.Handoff as h:
        print("NEEDS MANUAL HANDOFF: %s" % h)
        print("audit: %s" % getattr(h, "audit_path", "—"))
        print("请手动导出后运行：python3 -m buyer_ledger --db %s import <file> "
              "--provider %s --tier %s --period %s --account %s" %
              (args.db or DEFAULT_DB, recipe["provider"], recipe.get("tier", "bill_fact"),
               args.period, args.account))
        sys.exit(2)
    finally:
        store.close()
    if args.analyze:
        cmd_run(args)


def cmd_show(args):
    store = _store(args)
    for table in ("charge_line", "usage_event"):
        row = store.conn.execute(
            "SELECT * FROM %s WHERE id=?" % table, (args.id,)).fetchone()
        if row:
            d = dict(row)
            d["evidence"] = json.loads(d.pop("evidence_json") or "[]")
            print(json.dumps(d, ensure_ascii=False, indent=2))
            store.close()
            return
    print("not found: %s" % args.id)
    store.close()


def cmd_status(args):
    store = _store(args)
    docs = store.list_documents()
    print("documents: %d" % len(docs))
    for tier in ("bill_fact", "platform_usage", "reference"):
        n = len([d for d in docs if d["tier"] == tier])
        print("  %-16s %d" % (tier, n))
    n_charge = store.conn.execute("SELECT COUNT(*) c FROM charge_line").fetchone()["c"]
    n_usage = store.conn.execute("SELECT COUNT(*) c FROM usage_event").fetchone()["c"]
    print("charge lines: %d (effective %d)" % (
        n_charge, len(store.effective_charge_lines())))
    print("usage events: %d (effective %d)" % (
        n_usage, len(store.effective_usage_events())))
    store.close()


def cmd_audit(args):
    entries = []
    with open(args.file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    ok, reason = browser.verify_chain(entries)
    print("chain %s (%d entries) %s" % ("VERIFIED" if ok else "BROKEN",
                                        len(entries), reason))
    sys.exit(0 if ok else 1)


def cmd_close_init(args):
    result = close_mod.init_case(
        args.case, job_path=args.from_job or None, job_id=args.job_id,
        buyer=args.buyer, vendor=args.vendor, account=args.account,
        currency=args.currency, period=args.period, timezone_=args.timezone,
        task_economics_requested=args.task_economics, rule_map=args.rule_map or None)
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# bill-candidate：OCR/截图/粘贴账单候选（V-08 黄金链入口）
# ---------------------------------------------------------------------------

DEFAULT_OCR_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ocr_image.swift"


def _bc_print_candidate(candidate):
    payload = bc.projection(candidate)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("candidate: %s（%s）" % (payload["candidate_id"], payload["status"]))
    terminal = payload["terminal"]
    print("terminal_state: %s（%s）" % (terminal["terminal_state"], terminal["reason"]))
    if terminal.get("user_action"):
        print("next action: %s" % terminal["user_action"])
    if payload["status"] == "unreviewed":
        print("OCR 草稿不会自动入账：先复核 %d 个字段，再显式 book"
              % len(payload["review_queue"]))
    return payload


def cmd_bc_add(args):
    ocr_lines = None
    if args.ocr_json:
        try:
            doc = json.loads(Path(args.ocr_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("--ocr-json must be a readable JSON file: %s" % exc)
        ocr_lines = doc.get("lines") if isinstance(doc, dict) else doc
    pasted = None
    text_path = None
    if args.paste:
        pasted = sys.stdin.read()
    elif args.text:
        text_path = Path(args.text)
    engine = args.engine
    if engine is None:
        engine = "vision" if args.image else "text"
    result = bc.add_candidate(
        args.store, engine=engine, pasted_text=pasted, text_path=text_path,
        image_path=Path(args.image) if args.image else None,
        ocr_lines=ocr_lines,
        ocr_script=Path(args.ocr_script) if args.ocr_script else DEFAULT_OCR_SCRIPT)
    if result["candidate"] is None:
        print(json.dumps(result["procurement"], ensure_ascii=False, indent=2))
        print("requires_procurement：未执行任何采购或网络调用；改用免费路径："
              "--text/--paste 或 --image + 本地 Vision/--ocr-json")
        sys.exit(2)
    payload = _bc_print_candidate(result["candidate"])
    if payload["status"] == "blocked_user_input":
        sys.exit(2)


def cmd_bc_engines(args):
    print(json.dumps(bc.engine_catalog(), ensure_ascii=False, indent=2))


def cmd_bc_list(args):
    rows = bc.list_candidates(args.store)
    if not rows:
        print("no candidates in %s；先运行 bill-candidate add" % args.store)
        return
    print("%-12s %-18s %-12s %-10s %s" % ("ID", "STATUS", "ENGINE", "QUEUE", "INVOICE"))
    for row in rows:
        payload = bc.projection(row)
        invoice = next((f["value"] for f in row.get("fields", [])
                        if f["name"] == "invoice_no"), "") or "—"
        print("%-12s %-18s %-12s %-10d %s" % (
            payload["candidate_id"], payload["status"], payload.get("engine") or "—",
            len(payload["review_queue"]), invoice))
    print("%d candidate(s)；候选只是草稿，入账必须人工 book" % len(rows))


def cmd_bc_show(args):
    candidate = bc.load_candidate(args.store, args.id)
    # show stays machine-parseable: the human summary lives in add/list/review.
    print(json.dumps(bc.projection(candidate), ensure_ascii=False, indent=2))


def cmd_bc_review(args):
    candidate = bc.review_field(
        args.store, args.id, field=args.field, value=args.value,
        reviewer=args.by, note=args.note)
    payload = bc.projection(candidate)
    field = next(f for f in payload["fields"] if f["name"] == args.field)
    review = field["review"]
    print("reviewed %s.%s by %s（%s）" % (payload["candidate_id"], args.field,
                                          review["reviewer"], review["attestation"]))
    if review["original_value"] != review["value"]:
        print("value: %s -> %s（OCR 原值保留在 candidate.json）"
              % (json.dumps(review["original_value"], ensure_ascii=False),
                 json.dumps(review["value"], ensure_ascii=False)))
    print("queue left: %d" % len(payload["review_queue"]))
    if payload["review_queue"]:
        print("pending: %s" % ", ".join(i["field"] for i in payload["review_queue"]))


def cmd_bc_book(args):
    result = bc.book(
        args.store, args.id, reviewer=args.by, into=args.into,
        account=args.account, job_id=args.job_id, buyer=args.buyer,
        timezone_=args.timezone, usage=args.usage, rule_map=args.rule_map)
    print("booked by %s（%s；不等于付款授权）" % (result["booked_by"],
                                                result["attestation"]))
    print("bills export: %s (sha256 %s)" % (result["export_path"],
                                            result["export_sha256"][:12]))
    print("invoice document: %s" % result["document_path"])
    print("period-job: %s" % result["job_path"])
    print("next: close init CASE --from-job %s && close ingest CASE --bundle %s"
          % (result["job_path"], args.into))


def cmd_contract(args):
    path = Path(__file__).resolve().parents[1] / "docs" / "delivery-contract.json"
    if not path.is_file():
        raise ValueError("delivery contract missing: %s" % path)
    print(path.read_text(encoding="utf-8").rstrip("\n"))


def cmd_close_ingest(args):
    result = close_mod.ingest(args.case, bundle=args.bundle or None)
    draft = result["mapping_draft"]
    intake = args.case.rstrip("/") + "/intake"
    print("scan: %s/scan.json" % intake)
    print("mapping-draft.json: %s/mapping-draft.json" % intake)
    print("state: %s%s" % (draft["state"],
                           " (idempotent re-ingest)" if result["idempotent"] else ""))
    print("sources: %d; docs: %d; open items: %d; blocked: %s"
          % (sum(len(v) if isinstance(v, list) else 1
                 for v in draft["sources"].values()),
             len(result["documents"]), len(draft["open_items"]),
             draft["validation"]["blocked_code"] or "no"))
    if draft["validation"]["blocked"]:
        print("blocked: %s" % draft["validation"]["blocked_reason"])


def _parse_control_domains(values):
    domains = {}
    for raw in values or []:
        role, sep, domain = raw.partition("=")
        if not sep or not role.strip() or not domain.strip():
            raise close_mod.CloseError(
                "INPUT_REQUIRED",
                "--control-domain must be role=vendor|buyer (got %r)" % raw)
        domains[role.strip()] = domain.strip()
    return domains


def _draft_text(value):
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def _pending_control_domains(draft):
    """Roles whose control domain is still the unresolved sentinel.

    The draft already records each source's control domain, so the preview can
    name the ``--control-domain`` flags the confirm step needs instead of
    printing a command the tool will refuse.
    """
    pending = set()
    for role, entry in (draft.get("sources") or {}).items():
        entries = entry if isinstance(entry, list) else [entry]
        for item in entries:
            if isinstance(item, dict) and (
                    item.get("control_domain")
                    not in close_mod.RESOLVED_CONTROL_DOMAINS):
                pending.add(role)
    return sorted(pending)


def _confirm_command(case, pending):
    command = "close mapping %s --confirm --review-by <reviewer>" % case
    for role in pending:
        command += " --control-domain %s=<vendor|buyer>" % role
    return command


def cmd_close_mapping(args):
    if not args.confirm:
        path = args.case.rstrip("/") + "/intake/mapping-draft.json"
        try:
            with open(path, "r", encoding="utf-8") as handle:
                draft = json.load(handle)
        except OSError as exc:
            raise close_mod.CloseError(
                "INPUT_REQUIRED", "no mapping draft found; run close ingest first: %s" % exc)
        print("mapping draft: %s" % path)
        print("state: %s; review required before canonical generation" % draft.get("state"))
        for role, entry in (draft.get("sources") or {}).items():
            entries = entry if isinstance(entry, list) else [entry]
            for item in entries:
                print("  %s: %s -> %s" % (role, item.get("path"),
                                          item.get("columns") or {}))
        open_items = draft.get("open_items") or []
        print("open items: %d" % len(open_items))
        for item in open_items:
            location = _draft_text(item.get("source"))
            if _draft_text(item.get("field")):
                location = "%s / %s" % (location, item.get("field"))
            print("  - %s: %s" % (location or "？", item.get("detail")
                                  or item.get("kind") or ""))
        pending = _pending_control_domains(draft)
        if pending:
            print("control domain pending for %s; confirm needs an explicit "
                  "--control-domain role=vendor|buyer for each" % ", ".join(pending))
        print("confirm with: %s" % _confirm_command(args.case, pending))
        return
    confirmation = close_mod.confirm_mapping(
        args.case, reviewer=args.review_by,
        control_domains=_parse_control_domains(args.control_domain),
        note=args.note)
    print("mapping confirmed by %s (%s)"
          % (confirmation["confirmed_by"], confirmation["attestation"]))
    print("canonical: %s/canonical" % args.case)
    gaps = ["%s.%s" % (role, field)
            for role, entry in (confirmation.get("sources") or {}).items()
            for fields in entry.get("pending_fields_approved_as_gaps") or []
            for field in fields or []]
    if gaps:
        print("approved unmapped fields as gaps: %s" % ", ".join(gaps))
        print("they stay open items in the report; calculations that need them "
              "remain blocked until a later revision resolves them")


def _close_status_markdown(payload):
    identity = payload.get("identity") or {}
    account_ref = identity.get("buyer_account_ref") or {}
    period = identity.get("period_range") or {}
    c01 = payload.get("c01_status") or {}
    facts = payload.get("computed_facts") or {}
    review = payload.get("review_flow") or {}
    lines = [
        "# 月结状态 · %s" % (payload.get("close_case_id") or "—"),
        "",
        "- 买方：%s" % (account_ref.get("buyer") or "—"),
        "- 供应商：%s" % (identity.get("vendor_ref") or "—"),
        "- 账户：%s" % (account_ref.get("account") or "—"),
        "- 账期：%s → %s（%s）" % (
            period.get("from") or "—", period.get("to") or "—",
            period.get("timezone") or "—"),
        "- 币种：%s" % (identity.get("currency") or "—"),
        "",
        "## 状态",
        "",
        "- 任务状态：%s｜next_actor：%s" % (
            c01.get("task_state") or "—", c01.get("next_actor") or "—"),
        "- 阻断码：%s" % (c01.get("blocked_reason_code") or "无"),
        "- 完成轴：%s" % json.dumps(facts.get("completion_axes") or {},
                                    ensure_ascii=False),
        "- 复核：%s｜签核版本：%s" % (
            review.get("assigned_reviewer") or "未指派",
            review.get("signoff_revision") or "未签核"),
        "- 未闭合项：%d" % len(review.get("review_queue") or []),
        "",
        "## 缺失输入",
        "",
    ]
    missing = payload.get("missing_inputs") or []
    if missing:
        for item in missing:
            lines.append("- %s：%s" % (item.get("item") or "—",
                                        item.get("why") or ""))
    else:
        lines.append("- 无")
    lines += ["", "## 边界", "", "- %s" % (payload.get("boundary") or "—"), ""]
    return "\n".join(lines)


def cmd_close_status(args):
    from . import close_revision
    recovery = close_revision.ensure_recovered(args.case)
    target = close_revision.resolve_case_dir(args.case, args.revision)
    payload = close_mod.build_case(target, allow_pending=True)
    payload["recovery"] = recovery
    fmt = getattr(args, "format", "auto")
    if fmt == "auto":
        fmt = "md" if sys.stdout.isatty() else "json"
    if fmt == "md":
        print(_close_status_markdown(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_close_bundle(args):
    from . import close_bundle
    payload = close_bundle.build_bundle(args.case, args.revision)
    html_path = ""
    if args.html:
        path = Path(args.html)
        if path.exists():
            print(json.dumps({"status": "refused",
                              "reason_code": "INPUT_REQUIRED",
                              "detail": "OUTPUT_EXISTS", "path": str(path)},
                             ensure_ascii=False))
            sys.exit(2)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(close_bundle.render_html(payload), encoding="utf-8")
        html_path = str(path)
    if args.json:
        output = dict(payload)
        output["html_path"] = html_path
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(close_bundle.render_summary(payload))
        if html_path:
            print("html: %s" % html_path)
    if (payload.get("faults")
            or payload.get("state") in ("empty", "stale", "partial",
                                        "failed")):
        sys.exit(1)


def cmd_usage_replay(args):
    from . import usage_replay
    events = []
    inputs = [Path(item) for item in args.input]
    for path in inputs:
        events.extend(usage_replay.load_events(path))
    rule_map = usage_replay.load_rule_map(Path(args.rule_map))
    previous = None
    if args.previous:
        previous = json.loads(Path(args.previous).read_text(encoding="utf-8"))
    payload = usage_replay.replay(
        events, period=args.period, rule_map=rule_map,
        closed_at=args.closed_at, previous_revision=args.previous_revision,
        source_files=inputs)
    diff = usage_replay.diff_replays(payload, previous) if previous else None
    paths = {}
    if args.out:
        paths = usage_replay.write_replay(payload, Path(args.out), diff=diff)
    if args.json:
        output = dict(payload)
        output["paths"] = paths
        if diff is not None:
            output["diff"] = diff
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return
    print(_usage_summary(payload, paths, diff))


def _usage_summary(payload, paths, diff):
    counts = payload.get("counts") or {}
    rule_map = payload.get("rule_map") or {}
    revision = payload.get("revision") or {}
    lines = ["# 用量重放 %s" % (payload.get("period") or {}).get("from", "")[:7],
             "",
             "- 事件：收到 %d / 计入 %d / 重复 %d / 冲突 %d / 排除 %d / 迟到 %d"
             % (counts.get("received", 0), counts.get("included", 0),
                counts.get("duplicates", 0), counts.get("conflicts", 0),
                counts.get("excluded", 0), counts.get("late", 0)),
             "- RuleMap：%s（%s）"
             % (rule_map.get("hash"), "已批准" if rule_map.get("approved")
                else "未批准，仅草稿"),
             "- revision：%s effect=%s parent=%s"
             % (revision.get("revision_id"), revision.get("effect"),
                revision.get("parent_revision_id") or "none"),
             "- Delivery：%s（重放只覆盖计费口径）"
             % payload.get("delivery_state"),
             ""]
    if payload.get("aggregation_state") != "OK":
        lines.append("注意：%s" % payload.get("aggregation_state"))
        lines.append("")
    metrics = payload.get("metrics") or []
    if metrics:
        lines.append("## 指标")
        for metric in metrics:
            amount = metric.get("amount")
            amount_text = ("%s %s" % (amount, metric.get("currency") or "")
                           if amount is not None else
                           "金额 null（%s）" % (metric.get("amount_reason") or "UNKNOWN"))
            lines.append("- `%s` %s：%s %s，%s"
                         % (metric.get("metric_code"), metric.get("aggregate"),
                            metric.get("quantity"), metric.get("unit"),
                            amount_text))
        lines.append("")
    if payload.get("duplicates"):
        lines.append("## 重复（只计一次）")
        lines.extend("- `%s` ← %s" % (item.get("dedupe_key"), item.get("source_ref"))
                     for item in payload["duplicates"])
        lines.append("")
    if payload.get("conflicts"):
        lines.append("## 冲突（不合并，人工复核）")
        lines.extend("- `%s` %s ↔ %s"
                     % (item.get("dedupe_key"), item.get("prior_properties_hash"),
                        item.get("properties_hash"))
                     for item in payload["conflicts"])
        lines.append("")
    if diff is not None:
        lines.append("## 与上一版差异")
        lines.append("- 新增 %d / 移除 %d / 变化 %d"
                     % (len(diff.get("added") or []),
                        len(diff.get("removed") or []),
                        len(diff.get("changed") or [])))
        lines.append(diff.get("note", ""))
        lines.append("")
    for label, path in sorted(paths.items()):
        lines.append("%s: %s" % (label, path))
    return "\n".join(lines).rstrip() + "\n"


def cmd_reproduce(args):
    if args.reproduce_command == "build":
        result = reproduce_mod.build_bundle(
            [Path(item) for item in args.input], period=args.period,
            rule_map_path=Path(args.rule_map), out_dir=Path(args.out),
            identity_map_path=Path(args.identity_map) if args.identity_map else None,
            closed_at=args.closed_at, previous_revision=args.previous_revision,
            overwrite=args.overwrite, data_classification=args.data_classification,
            sharing_approved_by=args.sharing_approved_by)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        manifest = result["manifest"]
        print("\n".join([
            "# 重放验收包 %s" % manifest["period"]["from"][:7],
            "",
            "- 输出：%s" % result["out"],
            "- input_fingerprint：%s" % manifest["input_fingerprint"],
            "- revision：%s" % manifest["revision_id"],
            "- RuleMap：%s（%s）"
            % (manifest["rule_map"]["hash"],
               "已批准" if manifest["rule_map"]["approved"] else "未批准，金额不可作为计费依据"),
            "- 身份映射：%d 条可支撑尝试级复核，%d 条缺项，%d 条悬空引用"
            % (manifest["identity"]["complete_for_attempt"],
               manifest["identity"]["unmatched_count"],
               manifest["identity"]["dangling_count"]),
            "- 下一步：`%s`" % manifest["verify_command"],
            "",
        ]))
        return
    if args.reproduce_command == "publish":
        result = reproduce_mod.publish_bundle(
            Path(args.path), Path(args.out), approved_by=args.by)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("公开重放包：%s（%s）" % (result["out"], result["verification"]["verdict"]))
            print("公开副本已清理原始路径；Delivery 仍保持 UNKNOWN，买方决定不由工具代签。")
        return
    if args.reproduce_command == "proof":
        result = reproduce_proof.export_proof(Path(args.path), Path(args.out), by=args.by)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Prove Anything 投影：%s；源包未修改，外部结果仍未观察。" % result["out"])
        return
    result = reproduce_mod.verify_bundle(Path(args.path))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("verdict: %s" % result["verdict"])
        for check in result["checks"]:
            if not check["ok"]:
                print("- %s: %s" % (check["name"], check.get("detail") or "failed"))
    if result["verdict"] != "PASS":
        sys.exit(2)


def cmd_alloc_check(args):
    from . import allocation_policy as alloc
    policy = alloc.load_policy(Path(args.policy))
    report = alloc.check_policy(policy)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    lines = ["# 分摊策略检查 %s v%s"
             % (policy.get("policy_id"), policy.get("policy_version")),
             "",
             "- 状态：%s" % report.get("state"),
             "- 指纹：%s" % report.get("policy_hash"),
             ]
    diagnostics = report.get("diagnostics") or []
    if diagnostics:
        lines.append("- 诊断：")
        lines.extend("  - %s %s" % (item["code"], item["detail"])
                     for item in diagnostics)
    else:
        lines.append("- 诊断：无（可复核）")
    print("\n".join(lines))


def cmd_alloc_approve(args):
    from . import allocation_policy as alloc
    policy = alloc.load_policy(Path(args.policy))
    approved = alloc.approve_policy(policy, approved_by=args.approved_by,
                                    approved_at=args.approved_at,
                                    approval_ref=args.approval_ref)
    out = Path(args.out) if args.out else Path(args.policy).with_suffix(
        ".approved.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(approved, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    if args.json:
        print(json.dumps({"path": str(out), "policy": approved},
                         ensure_ascii=False, indent=2))
        return
    print("已批准 %s v%s（%s）→ %s"
          % (approved.get("policy_id"), approved.get("policy_version"),
             approved.get("approval_ref"), out))


def cmd_alloc_allocate(args):
    from . import allocation_policy as alloc
    policy = alloc.load_policy(Path(args.policy))
    charges = alloc.load_json(Path(args.charges))
    driver = alloc.load_json(Path(args.driver))
    economics = alloc.load_json(Path(args.economics)) if args.economics else None
    result = alloc.allocate(policy, charges=charges, driver=driver,
                            economics=economics)
    paths = alloc.write_result(result, Path(args.out))
    if args.json:
        output = dict(result)
        output["paths"] = paths
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return
    print(alloc.render_summary(result))
    for label, path in sorted(paths.items()):
        print("%s: %s" % (label, path))


def cmd_alloc_diff(args):
    from . import allocation_policy as alloc
    before = alloc.load_policy(Path(args.before))
    after = alloc.load_policy(Path(args.after))
    diff = alloc.diff_policies(before, after)
    if args.json:
        print(json.dumps(diff, ensure_ascii=False, indent=2))
        return
    lines = ["# 分摊策略差异 %s" % (diff["policy_before"].get("policy_id")),
             "",
             "- v%s %s → v%s %s（hash %s）"
             % (diff["policy_before"].get("policy_version"),
                diff["policy_before"].get("policy_hash"),
                diff["policy_after"].get("policy_version"),
                diff["policy_after"].get("policy_hash"),
                "变化" if diff["hash_changed"] else "未变"),
             "- %s" % diff["explanation"],
             ""]
    if diff["changes"]:
        lines.append("## 变更项")
        for item in diff["changes"]:
            lines.append("- `%s`：%s → %s（%s）"
                         % (item["field"], item["before"], item["after"],
                            "影响结果" if item["result_affecting"] else "元数据"))
        lines.append("")
    lines.append(diff["note"])
    print("\n".join(lines).rstrip())


def cmd_acceptance_dataset(args):
    from . import acceptance_dataset as acc
    raw = acc.load_json(Path(args.samples))
    if isinstance(raw, dict) and "samples" in raw:
        raw = raw["samples"]
    if not isinstance(raw, list):
        raise ValueError("samples JSON must be a list or {\"samples\": [...]}")
    criteria = acc.load_json(Path(args.criteria)) if args.criteria else None
    previous = acc.load_json(Path(args.previous)) if args.previous else None
    window = None
    if args.window_start or args.window_end:
        if not (args.window_start and args.window_end):
            raise ValueError("--window-start and --window-end go together")
        window = {"start": args.window_start, "end": args.window_end}
    dataset = acc.build_dataset(
        raw, dataset_id=args.dataset_id, dataset_version=args.dataset_version,
        window=window, criteria=criteria, previous=previous,
        reference_root=Path(args.reference_root) if args.reference_root else None)
    paths = acc.write_dataset(dataset, Path(args.out))
    if args.json:
        output = dict(dataset)
        output["paths"] = paths
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return
    print("# 评测数据集 %s v%s" % (dataset["dataset_id"],
                                  dataset["dataset_version"]))
    print("- 指纹：%s" % dataset["dataset_hash"])
    print("- 样本：%d；缺标准：%d；引用失效：%d"
          % (dataset["counts"]["samples"],
             dataset["counts"]["criteria_missing"],
             dataset["counts"]["broken_references"]))
    print("- 标准：%s"
          % ("未登记" if not dataset["criteria"] else
             "%s v%s（%d 项检查）"
             % (dataset["criteria"]["criteria_id"],
                dataset["criteria"]["criteria_version"],
                len(dataset["criteria"]["checks"]))))
    for label, path in sorted(paths.items()):
        print("%s: %s" % (label, path))


def cmd_acceptance_evaluate(args):
    from . import acceptance_dataset as acc
    dataset = acc.load_dataset(Path(args.dataset))
    scores = acc.load_json(Path(args.scores))
    if isinstance(scores, dict) and "scores" in scores:
        scores = scores["scores"]
    labels = acc.load_json(Path(args.labels)) if args.labels else []
    adjudications = (acc.load_json(Path(args.adjudications))
                     if args.adjudications else [])
    previous = acc.load_json(Path(args.previous)) if args.previous else None
    run = acc.evaluate(dataset, service_version=args.service_version,
                       evaluator_version=args.evaluator_version,
                       scores=scores, human_labels=labels,
                       adjudications=adjudications, previous=previous)
    paths = acc.write_run(run, Path(args.out))
    if args.json:
        output = dict(run)
        output["paths"] = paths
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return
    print(acc.render_calibration(run))
    for label, path in sorted(paths.items()):
        print("%s: %s" % (label, path))


def cmd_acceptance_calibrate(args):
    from . import acceptance_dataset as acc
    run = acc.load_json(Path(args.run))
    calibration = acc.calibrate(run)
    if args.json:
        print(json.dumps(calibration, ensure_ascii=False, indent=2))
        return
    print(acc.render_calibration(run))


def cmd_close_recover(args):
    from . import close_revision
    result = close_revision.recover(
        args.case, apply=args.apply, rollback=args.rollback)
    if args.apply:
        if result["applied"]:
            print("applied %d transaction(s): %s"
                  % (len(result["applied"]),
                     ", ".join(item.get("revision_id") or "?"
                               for item in result["applied"])))
        else:
            print("nothing to recover; index and revisions are consistent")
        return
    if args.rollback:
        if result["rolled_back"]:
            print("rolled back %d transaction(s): %s"
                  % (len(result["rolled_back"]),
                     ", ".join(item.get("revision_id") or "?"
                               for item in result["rolled_back"])))
        else:
            print("nothing to roll back; index and revisions are consistent")
        return
    issues = result["issues"]
    if not issues:
        print("nothing to recover; index and revisions are consistent")
        return
    print("recovery plan (dry-run): %d transaction(s)" % len(issues))
    for issue in issues:
        print("  %s %s: %s" % (issue.get("revision_id") or "?",
                               issue.get("journal_kind") or issue.get("kind"),
                               issue.get("state")))
        for step in issue.get("steps") or []:
            suffix = ""
            if step.get("problems"):
                suffix = " [%s]" % ", ".join(
                    problem.get("problem", "?")
                    for problem in step["problems"])
            print("    %s: %s%s" % (step.get("name"), step.get("state"),
                                    suffix))
        if issue.get("message"):
            print("    %s" % issue["message"])
    print("apply:    close recover %s --apply" % args.case)
    print("rollback: close recover %s --rollback" % args.case)


def _recover_before_write(args):
    from . import close_revision
    recovery = close_revision.ensure_recovered(args.case, strict=True)
    if recovery["state"] == "recovered":
        print("recovery: %s" % recovery["message"])
    return recovery


def cmd_close_calculate(args):
    from . import close_report, close_revision
    _recover_before_write(args)
    if close_revision.list_revisions(args.case):
        raise close_mod.CloseError(
            "REVIEW_REQUIRED",
            "the root is revision r1 and must stay immutable; create a new "
            "revision with close revise instead of recalculating it")
    target = args.case
    calculation = close_report.calculate(target)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(calculation, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print("calculation: %s/calculation.json" % target)
    print("schema: %s" % calculation["schema"])
    print("canonical fingerprint: %s" % calculation["canonical_snapshot"]["fingerprint"])
    print("axes: %s" % json.dumps(calculation["statuses"], ensure_ascii=False))
    print("calculation fingerprint: %s" % calculation["calculation_fingerprint"])


def cmd_close_report(args):
    from . import close_report, close_revision
    _recover_before_write(args)
    if close_revision.list_revisions(args.case):
        raise close_mod.CloseError(
            "REVIEW_REQUIRED",
            "the root is revision r1 and must stay immutable; create a new "
            "revision with close revise instead of rebuilding its report")
    target = args.case
    report = close_report.build_report(target)
    print("report dir: %s/report" % target)
    for name in ("report.md", "report.json", "line-reconciliation.csv",
                 "review-queue.csv", "evidence-index.json", "manifest.json"):
        print("  %s" % name)
    print("finance=%s verification=%s delivery=%s economics=%s"
          % (report["finance_status"], report["verification_status"],
             report["delivery_status"], report["economics_status"]))
    print("open difference: %s %s" % (
        report["amounts"]["open_difference"] or "—",
        report["amounts"]["currency"]))
    print("review: %s" % report["review"]["state"])


def cmd_close_review(args):
    from . import close_report, close_revision
    _recover_before_write(args)
    if not args.revision and close_revision.list_revisions(args.case):
        raise close_mod.CloseError(
            "REVIEW_REQUIRED",
            "the root is revision r1 and must stay immutable; record a "
            "reference review before revisions or use --revision to review a named revision")
    target = close_revision.resolve_case_dir(args.case, args.revision)
    review = close_report.record_review(
        target, reviewer=args.by, approved_scope=args.scope,
        reservations=args.reservation, next_review=args.next_review)
    print("reviewed by %s (%s)" % (review["reviewer"], review["attestation"]))
    print("scope: %s" % (review["approved_scope"] or "—"))
    print("signoff state: %s" % review["signoff"]["state"])
    print("unresolved items: %d (never closed by review)"
          % len(review["unresolved_items"]))
    print("payment authorization: %s" % review["payment_authorization"])


def cmd_handoff_import_growovo(args):
    from . import growovo_handoff
    result = growovo_handoff.import_handoff(args.source, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("边界：交接完整性已核验 ≠ 外部回执、业务结果或账单金额已由 Verovo 独立核验。")


def cmd_handoff_normalize_growovo(args):
    from . import handoff_evidence
    result = handoff_evidence.normalize_handoff_file(args.source, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("边界：tasks/runs/outcomes 是证据、不是入账；committed 是动作事实，"
          "product event 一律未经验证（verified=false），dry-run 不计执行与成本。")


def cmd_close_action(args):
    """Record/list explicit difference dispositions; never execute external writes."""
    from . import close_actions, close_revision
    close_revision.ensure_recovered(args.case, strict=not args.list)
    target = close_revision.resolve_case_dir(args.case, args.revision)
    if args.list:
        print(json.dumps(close_actions.list_actions(target), ensure_ascii=False, indent=2))
        return
    record = close_actions.record_action(
        target, open_item_ref=args.open_item, action=args.type,
        owner=args.owner, by=args.by, source_ref=args.source_ref,
        note=args.note, occurred_at=args.occurred_at)
    print("difference action: %s (%s)" % (record["action_id"], record["action"]))
    print("open item: %s" % record["open_item_ref"])
    print("owner: %s | by: %s | occurred_at: %s" % (
        record["owner"], record["by"], record["occurred_at"]))
    print("source ref: %s (%s)" % (
        record["source_ref"], record["receipt_verification"]))
    print("idempotent: %s" % record.get("idempotent", False))
    print("external action executed: False (local declaration only)")


def cmd_close_revise(args):
    from . import close_revision
    _recover_before_write(args)
    result = close_revision.revise(
        args.case, bundle=args.bundle or None, source=args.source or None,
        role=args.role, reason=args.reason, reviewer=args.review_by,
        control_domains=_parse_control_domains(args.control_domain))
    revision = result["revision"]
    print("revision: %s (parent %s)" % (
        revision["revision_id"], revision["parent_revision_id"]))
    print("idempotent: %s" % result["idempotent"])
    print("status: %s" % revision["status"])
    print("sources: added=%s removed=%s changed=%s" % (
        revision["source_added"], revision["source_removed"],
        revision["source_changed"]))
    if result.get("delta"):
        amounts = result["delta"]["amounts"]
        print("delta invoice_total: %s -> %s (%s)" % (
            amounts["invoice_total"]["before"], amounts["invoice_total"]["after"],
            amounts["invoice_total"]["delta"]))
        print("delta axes: %s" % (result["delta"]["changed_axes"] or "none"))
        credits = result["delta"]["credits"]
        print("credits applied delta=%s pending delta=%s realized delta=%s"
              % (credits["applied"]["delta"], credits["pending"]["delta"],
                 credits["realized"]["delta"]))
    if result.get("path"):
        print("path: %s" % result["path"])


def cmd_close_signoff(args):
    from . import close_revision
    _recover_before_write(args)
    record = close_revision.signoff(
        args.case, reviewer=args.by, approved_scope=args.scope,
        reservations=args.reservation, note=args.note,
        revision_id=args.revision)
    print("signed revision %s by %s (%s)"
          % (record["revision_id"], record["reviewer"], record["state"]))
    print("unresolved items: %d (not closed by signoff)"
          % len(record["unresolved_items"]))
    print("payment authorization: %s" % record["payment_authorization"])
    print("scope hash: %s" % record["scope_hash"])
    target = close_revision.resolve_case_dir(Path(args.case),
                                             record["revision_id"])
    print("delivered report: %s"
          % (target / "report" / "report-delivered.md"))
    print("delivered payload: %s"
          % (target / "report" / "report-delivered.json"))


def build_parser():
    p = argparse.ArgumentParser(prog="buyer-ledger",
                                description="买方侧统一费用账本与对账审查")
    p.add_argument("--db", default=DEFAULT_DB, help="SQLite 账本路径")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="初始化账本").set_defaults(func=cmd_init)

    sp = sub.add_parser("import", help="导入材料（L2 / 浏览器下载物）")
    sp.add_argument("file")
    sp.add_argument("--provider", required=True)
    sp.add_argument("--tier", required=True,
                    choices=["bill_fact", "platform_usage", "reference"])
    sp.add_argument("--period", default="")
    sp.add_argument("--doc-type", default="bill")
    sp.add_argument("--account", default="")
    sp.add_argument("--agent", action="store_true",
                    help="指纹未命中时用 LLM 生成解析规格")
    sp.set_defaults(func=cmd_import)

    sp = sub.add_parser("parse", help="解析已登记文档（确定性重放或 --agent）")
    sp.add_argument("--document", required=True)
    sp.add_argument("--agent", action="store_true")
    sp.set_defaults(func=cmd_parse)

    sp = sub.add_parser("rule", help="查看/批准 LLM 生成的解析规则草案")
    sp.add_argument("action", choices=["list", "show", "approve"])
    sp.add_argument("fingerprint", nargs="?", default="")
    sp.add_argument("--provider", default="")
    sp.add_argument("--doc-type", default="")
    sp.add_argument("--tier", default="")
    sp.set_defaults(func=cmd_rule)

    sp = sub.add_parser("preset", help="内置导出格式预设（登记已知表头的解析规则）")
    sp.add_argument("action", choices=["list", "show", "apply"])
    sp.add_argument("name", nargs="?", default="")
    sp.set_defaults(func=cmd_preset)

    sp = sub.add_parser("catalog", help="国内外 Agent 工具账单/计费/审计能力目录")
    sp.add_argument("action", choices=["list", "show", "coverage", "summary"])
    sp.add_argument("provider", nargs="?", default="")
    sp.add_argument("--region", choices=["cn", "global"])
    sp.add_argument("--role", choices=["buyer", "seller"])
    sp.add_argument("--category", default="")
    sp.add_argument("--query", default="")
    sp.add_argument("--period", default="")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_catalog)

    sp = sub.add_parser("policy", help="登记价格政策（同版本不可变）")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_policy)

    sp = sub.add_parser("price", help="把价格政策应用到用量（复算估算层）")
    sp.add_argument("--provider", required=True)
    sp.add_argument("--period", required=True)
    sp.set_defaults(func=cmd_price)

    sp = sub.add_parser("reconcile", help="对账一个账期")
    sp.add_argument("--period", required=True)
    sp.set_defaults(func=cmd_reconcile)

    sp = sub.add_parser("report", help="生成对账报告")
    sp.add_argument("--period", required=True)
    sp.add_argument("--out", default="")
    sp.add_argument("--perspective", choices=["buyer", "seller"], default="buyer",
                    help="买家对账或卖家计量完整性视角")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("run", help="一键复算、对账并生成报告")
    sp.add_argument("--period", required=True)
    sp.add_argument("--out", default="")
    sp.add_argument("--perspective", choices=["buyer", "seller"], default="buyer")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("recipe", help="配方校验/演练")
    sp.add_argument("action", choices=["validate", "dryrun"])
    sp.add_argument("file")
    sp.set_defaults(func=cmd_recipe)

    sp = sub.add_parser("session", help="会话管理")
    sp.add_argument("action", choices=["login", "status", "cdp"])
    sp.add_argument("--recipe", default="")
    sp.add_argument("--account", default="")
    sp.add_argument("--endpoint", default="",
                    help="本地 CDP 地址，如 http://127.0.0.1:9222")
    sp.set_defaults(func=cmd_session)

    sp = sub.add_parser("fetch", help="L1 浏览器采集（配方重放）")
    sp.add_argument("--recipe", required=True)
    sp.add_argument("--period", required=True)
    sp.add_argument("--account", default="")
    sp.add_argument("--cdp", default="",
                    help="附着本地 Chrome CDP，如 http://127.0.0.1:9222")
    sp.add_argument("--analyze", action="store_true",
                    help="解析成功后自动复算、对账并生成报告")
    sp.add_argument("--out", default="")
    sp.add_argument("--perspective", choices=["buyer", "seller"], default="buyer")
    sp.add_argument("--headed", action="store_true", help="有头模式（调试）")
    sp.set_defaults(func=cmd_fetch)

    sp = sub.add_parser("show", help="查看任意行的完整字段与证据")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_show)

    sub.add_parser("status", help="账本概况").set_defaults(func=cmd_status)

    sp = sub.add_parser("audit", help="审计链校验")
    sp.add_argument("action", choices=["verify"])
    sp.add_argument("file")
    sp.set_defaults(func=cmd_audit)

    sp = sub.add_parser("contract", help="机器可读交付合同（意图/终态/排除项/采购分支）")
    sp.add_argument("action", choices=["show"])
    sp.set_defaults(func=cmd_contract)

    sp = sub.add_parser(
        "bill-candidate",
        help="账单截图/OCR/粘贴文本候选：字段级证据草稿→人工复核→book 入月结链（V-08）")
    bc_sub = sp.add_subparsers(dest="bc_command", required=True)

    sp = bc_sub.add_parser(
        "add", help="从截图/粘贴文本产出 unreviewed 候选草稿（永不自动入账）")
    sp.add_argument("store", nargs="?", default="bill-candidates",
                    help="候选存储目录（默认 ./bill-candidates）")
    group = sp.add_mutually_exclusive_group()
    group.add_argument("--text", default="", help="账单文本文件路径")
    group.add_argument("--paste", action="store_true", help="从 stdin 读粘贴文本")
    group.add_argument("--image", default="", help="账单截图路径（png/jpg）")
    sp.add_argument("--engine", choices=bc.ENGINES, default=None,
                    help="text=本地文本；vision=本地 macOS Vision OCR（非 darwin 明确不可用）；"
                         "paid-cloud-ocr=付费云 OCR（输出 requires_procurement，不购买）")
    sp.add_argument("--ocr-json", default="",
                    help="已注入的 OCR 结果（verovo.ocr-lines/1），测试与离线链路用")
    sp.add_argument("--ocr-script", default="",
                    help="本地 Vision 运行器路径（默认 scripts/ocr_image.swift）")
    sp.set_defaults(func=cmd_bc_add)

    sp = bc_sub.add_parser("list", help="列出候选与待复核队列大小")
    sp.add_argument("store", nargs="?", default="bill-candidates")
    sp.set_defaults(func=cmd_bc_list)

    sp = bc_sub.add_parser(
        "engines", help="列出 OCR 引擎：哪些可用、哪些需要自行采购及替代路径")
    sp.set_defaults(func=cmd_bc_engines)

    sp = bc_sub.add_parser("show", help="候选详情：字段证据、复核状态、终态")
    sp.add_argument("store", nargs="?", default="bill-candidates")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_bc_show)

    sp = bc_sub.add_parser("review", help="人工复核一个字段（可纠正 OCR 值）")
    sp.add_argument("store", nargs="?", default="bill-candidates")
    sp.add_argument("id")
    sp.add_argument("--field", required=True,
                    help="vendor/invoice_no/period/currency/amount/line_items")
    sp.add_argument("--value", required=True,
                    help="确认值；period 接受 YYYY-MM 或 from..to（to 为排除端）")
    sp.add_argument("--by", required=True, help="复核人（attested_by_declaration）")
    sp.add_argument("--note", default="")
    sp.set_defaults(func=cmd_bc_review)

    sp = bc_sub.add_parser("book", help="复核完成后显式入账：导出 bills 源 + period-job")
    sp.add_argument("store", nargs="?", default="bill-candidates")
    sp.add_argument("id")
    sp.add_argument("--by", required=True, help="入账确认人；OCR 草稿永不自行入账")
    sp.add_argument("--into", required=True, help="目标账期包目录")
    sp.add_argument("--account", default="", help="job 账户（必填，不猜测）")
    sp.add_argument("--job-id", default="")
    sp.add_argument("--buyer", default="")
    sp.add_argument("--timezone", default="UTC")
    sp.add_argument("--usage", default="", help="包内 usage 导出文件名（独立复算来源）")
    sp.add_argument("--rule-map", default="", help="包内规则表文件名")
    sp.set_defaults(func=cmd_bc_book)

    sp = sub.add_parser("handoff", help="跨产品证据交接（只导入，不自动入账）")
    handoff_sub = sp.add_subparsers(dest="handoff_command", required=True)
    sp = handoff_sub.add_parser(
        "import-growovo", help="校验 growovo.verovo-handoff/1 并落为未入账证据包")
    sp.add_argument("source", help="Growovo 导出的交接 JSON")
    sp.add_argument("--out", required=True, help="空目录或新目录；拒绝覆盖已有证据")
    sp.set_defaults(func=cmd_handoff_import_growovo)
    sp = handoff_sub.add_parser(
        "normalize-growovo",
        help="把已核验的 growovo.verovo-handoff/1 规范化为 Period Intake 可导入的 "
             "tasks/runs/outcomes（不派生成本、不自动入账）")
    sp.add_argument("source", help="Growovo 导出的交接 JSON（完整性校验后才会写）")
    sp.add_argument("--out", required=True, help="空目录或新目录；拒绝覆盖已有文件")
    sp.set_defaults(func=cmd_handoff_normalize_growovo)

    sp = sub.add_parser("close",
                        help="账期包导入、人工字段映射与 BuyerCloseCase（V-02）")
    close_sub = sp.add_subparsers(dest="close_command", required=True)

    sp = close_sub.add_parser("init", help="创建账期案例（--from-job 或显式参数）")
    sp.add_argument("case", help="案例目录，如 ./close-2026-10")
    sp.add_argument("--from-job", default="", help="verovo.period-job/1 JSON 路径")
    sp.add_argument("--job-id", default="")
    sp.add_argument("--buyer", default="")
    sp.add_argument("--vendor", default="")
    sp.add_argument("--account", default="")
    sp.add_argument("--period", default="", help="YYYY-MM；写入半开区间 [from, to)")
    sp.add_argument("--currency", default="")
    sp.add_argument("--timezone", default="UTC", help="IANA 时区，与账单展示口径一致")
    sp.add_argument("--rule-map", default="")
    sp.add_argument("--task-economics", action="store_true")
    sp.set_defaults(func=cmd_close_init)

    sp = close_sub.add_parser("ingest", help="扫描账期包并生成 mapping-draft.json")
    sp.add_argument("case")
    sp.add_argument("--bundle", default="", help="账期包目录（默认同 case）")
    sp.set_defaults(func=cmd_close_ingest)

    sp = close_sub.add_parser("mapping", help="查看或人工确认字段映射")
    sp.add_argument("case")
    sp.add_argument("--confirm", action="store_true",
                    help="记录人工确认；确认后才生成 canonical")
    sp.add_argument("--review-by", default="", help="确认人（attested_by_declaration）")
    sp.add_argument("--control-domain", action="append", default=[],
                    metavar="ROLE=VENDOR|BUYER",
                    help="解决 pending 控制域，可重复")
    sp.add_argument("--note", default="")
    sp.set_defaults(func=cmd_close_mapping)

    for name, help_text in (
            ("status", "只读 BuyerCloseCase 投影（四完成轴与待办）"),
            ("show", "同 status：只读 BuyerCloseCase 投影")):
        sp = close_sub.add_parser(name, help=help_text)
        sp.add_argument("case")
        sp.add_argument("--revision", default="", help="指定 revision；默认最新")
        sp.add_argument("--format", choices=("auto", "json", "md"), default="auto",
                        help="输出格式；auto=终端用 md、管道用 json（默认 auto）")
        sp.set_defaults(func=cmd_close_status)

    sp = close_sub.add_parser("calculate", help="对 canonical bundle 运行既有重算链（V-03）")
    sp.add_argument("case")
    sp.add_argument("--json", default="", help="另存一份 calculation JSON")
    sp.set_defaults(func=cmd_close_calculate)

    sp = close_sub.add_parser("report", help="生成六份可签核月结报告（V-03）")
    sp.add_argument("case")
    sp.add_argument("--format", choices=["all", "md", "json"], default="all",
                    help="输出投影提示；V-03 始终写全六份报告对象")
    sp.set_defaults(func=cmd_close_report)

    sp = close_sub.add_parser("review", help="记录署名复核；不关闭未决项（V-03/V-04 门禁）")
    sp.add_argument("case")
    sp.add_argument("--revision", default="", help="复核指定 revision；默认当前根版本")
    sp.add_argument("--by", default="",
                    help="复核人（自报声明，非强身份；缺失即 INPUT_REQUIRED）")
    sp.add_argument("--scope", default="", help="批准/复核范围")
    sp.add_argument("--reservation", action="append", default=[],
                    help="保留意见，可重复")
    sp.add_argument("--next-review", default="", help="下次复核日期 YYYY-MM-DD")
    sp.set_defaults(func=cmd_close_review)

    sp = close_sub.add_parser(
        "action", help="登记差异问询/更正/争议/预留/解决凭据；不代发、不付款")
    sp.add_argument("case")
    sp.add_argument("--revision", default="", help="指定 revision；默认最新")
    sp.add_argument("--list", action="store_true", help="只读列出处置记录与当前投影")
    sp.add_argument("--open-item", default="", help="报告处理清单中的 D-N ID")
    sp.add_argument("--type", choices=("inquiry", "correction", "dispute", "reserve", "resolved"))
    sp.add_argument("--owner", default="", help="该处置的责任人/团队")
    sp.add_argument("--by", default="", help="登记人（自报声明，非强身份）")
    sp.add_argument("--source-ref", default="", help="工单、邮件、更正单、争议或预留凭据引用")
    sp.add_argument("--note", default="", help="处置说明；resolved 必填解决依据")
    sp.add_argument("--occurred-at", default="", help="实际处置时间 ISO 8601，必须含时区")
    sp.set_defaults(func=cmd_close_action)

    sp = close_sub.add_parser("revise", help="从新来源生成独立 revision（V-04）")
    sp.add_argument("case")
    sp.add_argument("--bundle", default="", help="完整的新账期包目录")
    sp.add_argument("--source", default="", help="替换或具名新增单个来源文件")
    sp.add_argument("--role", default="", help="--source 对应的 role；新来源必须同时指定 --control-domain")
    sp.add_argument("--reason", default="", help="修订原因")
    sp.add_argument("--review-by", default="", help="确认新映射的人；缺失即拒绝")
    sp.add_argument("--control-domain", action="append", default=[],
                    metavar="ROLE=VENDOR|BUYER")
    sp.set_defaults(func=cmd_close_revise)

    sp = close_sub.add_parser("signoff", help="对指定 revision 记录署名签核（V-04）")
    sp.add_argument("case")
    sp.add_argument("--revision", default="", help="指定 revision；默认最新")
    sp.add_argument("--by", default="", help="签核人；缺失即 INPUT_REQUIRED")
    sp.add_argument("--scope", default="", help="签核范围")
    sp.add_argument("--reservation", action="append", default=[],
                    help="保留意见，可重复")
    sp.add_argument("--note", default="")
    sp.set_defaults(func=cmd_close_signoff)

    sp = close_sub.add_parser(
        "bundle",
        help="只读 Evidence Bundle：Charges / Delivery / Coverage 三栏（V-P0）")
    sp.add_argument("case")
    sp.add_argument("--revision", default="", help="指定 revision；默认最新")
    sp.add_argument("--json", action="store_true",
                    help="输出页面 JSON（含 html_path）")
    sp.add_argument("--html", default="",
                    help="另存自包含 HTML；拒绝覆盖已有文件")
    sp.set_defaults(func=cmd_close_bundle)

    sp = close_sub.add_parser(
        "recover", help="检查/完成/回滚中断的 revision 或 signoff 事务（V-06）")
    sp.add_argument("case")
    group = sp.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true",
                       help="完成 journal 记录的事务（默认 dry-run）")
    group.add_argument("--rollback", action="store_true",
                       help="回滚到事务前的一致状态")
    sp.set_defaults(func=cmd_close_recover)

    sp = sub.add_parser(
        "usage", help="用量事件重放（V-NEXT-1）：重复、冲突、迟到与计费口径")
    usage_sub = sp.add_subparsers(dest="usage_command", required=True)
    sp = usage_sub.add_parser(
        "replay", help="本地重放授权用量文件；不生成 invoice、不付款")
    sp.add_argument("--input", action="append", required=True, metavar="FILE",
                    help="事件文件 .jsonl/.json/.csv，可重复")
    sp.add_argument("--period", required=True,
                    help="账期 YYYY-MM；半开区间 [from,to)")
    sp.add_argument("--rule-map", required=True,
                    help="已批准 verovo.usage-rule-map/1 JSON")
    sp.add_argument("--out", default="",
                    help="输出目录；写 usage-replay.json、CSV 与差异说明")
    sp.add_argument("--closed-at", default="",
                    help="关账时间 ISO 8601（含时区）；之后导入算迟到")
    sp.add_argument("--previous-revision", default="",
                    help="上一版 revision_id（写入 parent）")
    sp.add_argument("--previous", default="",
                    help="上一版 usage-replay.json，用于差异对照")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_usage_replay)

    sp = sub.add_parser(
        "alloc", help="分摊策略与指纹（V-NEXT-2）：版本、批准、定投与残差")
    alloc_sub = sp.add_subparsers(dest="alloc_command", required=True)
    sp = alloc_sub.add_parser("check", help="校验策略与可复核性诊断")
    sp.add_argument("--policy", required=True, help="verovo.allocation-policy/1 JSON")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_alloc_check)
    sp = alloc_sub.add_parser("approve", help="具名批准策略，生成新文件")
    sp.add_argument("--policy", required=True)
    sp.add_argument("--approved-by", required=True)
    sp.add_argument("--approved-at", required=True, help="带时区的 ISO 8601")
    sp.add_argument("--approval-ref", required=True)
    sp.add_argument("--out", default="", help="默认 <policy>.approved.json")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_alloc_approve)
    sp = alloc_sub.add_parser("allocate", help="按策略分摊费用并写只读结果")
    sp.add_argument("--policy", required=True, help="已批准策略 JSON")
    sp.add_argument("--charges", required=True, help="费用清单 JSON")
    sp.add_argument("--driver", required=True, help="驱动量 JSON")
    sp.add_argument("--economics", default="", help="可选 benefit/cost JSON")
    sp.add_argument("--out", required=True, help="输出目录")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_alloc_allocate)
    sp = alloc_sub.add_parser("diff", help="两版策略逐项差异")
    sp.add_argument("--before", required=True)
    sp.add_argument("--after", required=True)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_alloc_diff)

    sp = sub.add_parser(
        "acceptance",
        help="买方标准回归集与人机评分校准（V-NEXT-3）：版本、分歧与校准")
    acceptance_sub = sp.add_subparsers(dest="acceptance_command",
                                       required=True)
    sp = acceptance_sub.add_parser("dataset", help="构建不可变评测数据集")
    sp.add_argument("--samples", required=True, help="样本 JSON 列表")
    sp.add_argument("--id", dest="dataset_id", required=True)
    sp.add_argument("--version", dest="dataset_version", required=True)
    sp.add_argument("--criteria", default="", help="买方标准 JSON")
    sp.add_argument("--previous", default="", help="上一版数据集，校验版本冲突")
    sp.add_argument("--reference-root", default="",
                    help="本地引用根目录；用于发现 BROKEN_REFERENCE")
    sp.add_argument("--window-start", default="")
    sp.add_argument("--window-end", default="")
    sp.add_argument("--out", required=True, help="输出目录")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_acceptance_dataset)

    sp = acceptance_sub.add_parser("evaluate", help="运行本地评分并保留人工标签")
    sp.add_argument("--dataset", required=True)
    sp.add_argument("--scores", required=True, help="自动评分 JSON 列表")
    sp.add_argument("--service-version", required=True)
    sp.add_argument("--evaluator-version", required=True)
    sp.add_argument("--labels", default="", help="人工标签 JSON 列表")
    sp.add_argument("--adjudications", default="", help="裁决 JSON 列表")
    sp.add_argument("--previous", default="", help="上一轮 run，校验评分器版本冲突")
    sp.add_argument("--out", required=True, help="输出目录")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_acceptance_evaluate)

    sp = acceptance_sub.add_parser("calibrate", help="重放并打印校准摘要")
    sp.add_argument("--run", required=True, help="evaluation-run.json")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_acceptance_calibrate)

    sp = sub.add_parser(
        "reproduce",
        help="第三方可复核的重放验收包：输入副本、身份映射、差异分类与自行复核")
    reproduce_sub = sp.add_subparsers(dest="reproduce_command", required=True)
    sp = reproduce_sub.add_parser(
        "build", help="打包重放验收包；输入先复制进包内，不读原始位置")
    sp.add_argument("--input", action="append", required=True, metavar="FILE",
                    help="事件文件 .jsonl/.json/.csv，可重复")
    sp.add_argument("--period", required=True, help="账期 YYYY-MM")
    sp.add_argument("--rule-map", required=True,
                    help="已批准 verovo.usage-rule-map/1 JSON")
    sp.add_argument("--out", required=True, help="输出目录（建议为空目录）")
    sp.add_argument("--identity-map", default="",
                    help="可选 {transaction_id: {...}} 身份映射文件")
    sp.add_argument("--closed-at", default="", help="关账时间 ISO 8601（含时区）")
    sp.add_argument("--previous-revision", default="", help="上一版 revision_id")
    sp.add_argument("--overwrite", action="store_true",
                    help="允许写入非空目录；会覆盖同名文件")
    sp.add_argument("--data-classification", default="confidential",
                    choices=list(reproduce_mod.DATA_CLASSIFICATIONS),
                    help="默认 confidential；公开仅用 synthetic 或已授权 public-redacted")
    sp.add_argument("--sharing-approved-by", default="",
                    help="public-redacted 必需的具名脱敏发布批准人")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_reproduce)
    sp = reproduce_sub.add_parser(
        "verify", help="只读复核：校验哈希并重跑重放；PASS/FAIL/UNPROVEN")
    sp.add_argument("path", help="验收包目录")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_reproduce)
    sp = reproduce_sub.add_parser(
        "publish", help="发布 synthetic/public-redacted 重放包；不上传，只写本地目录")
    sp.add_argument("path", help="已通过 verify 的私有重放包")
    sp.add_argument("--out", required=True, help="为空的公开副本目录")
    sp.add_argument("--by", required=True, help="具名发布人；public-redacted 须匹配脱敏批准人")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_reproduce)
    sp = reproduce_sub.add_parser(
        "proof", help="导出 Prove Anything 只读投影；不复制私密输入，不执行外部动作")
    sp.add_argument("path", help="重放包目录")
    sp.add_argument("--out", required=True, help="新的证明包目录")
    sp.add_argument("--by", required=True, help="具名证明审核人")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_reproduce)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (LedgerError, LLMError, browser.RecipeError, ValueError, OSError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        sys.exit(2)
    return 0


if __name__ == "__main__":
    main()
