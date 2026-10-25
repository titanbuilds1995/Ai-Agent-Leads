"""Command line: init-db | run-daily | check-replies | report | status | install-cron | webhook | gmail-auth | doctor | score | value | enrich | outreach"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import config, db
from .llm import LLM, LLMUnavailable
from .util import log, setup_logging, today_et


def build_clients(cfg, conn, need=("apify", "llm", "gmail", "twilio"), interactive: bool = False) -> dict[str, Any]:
    """Construct whichever external clients have credentials. Missing ones are None and get reported, never fatal."""
    out: dict[str, Any] = {"apify": None, "llm": None, "gmail": None, "twilio": None, "missing": []}
    if "apify" in need:
        from .ingest.apify import ApifyClient
        client = ApifyClient(cfg.secret("APIFY_TOKEN"), cfg.get("ingest.apify.timeout_seconds", 900), cfg.path("raw"))
        if cfg.secret("APIFY_TOKEN"):
            out["apify"] = client
        else:
            ok, _ = client.auth_ok()      # a network-layer secret (cloud environment) authenticates without the variable
            if ok:
                out["apify"] = client
            else:
                out["missing"].append("APIFY_TOKEN")
    if "llm" in need:
        try:
            out["llm"] = LLM(cfg, conn)
            if not out["llm"].available:
                out["missing"].append("Claude (no `claude` CLI or API key: using rules fallback)")
        except LLMUnavailable as e:
            out["missing"].append(f"ANTHROPIC_API_KEY ({e})")
    if "gmail" in need:
        token_path = cfg.root / (cfg.secret("GMAIL_TOKEN_JSON") or "secrets/gmail_token.json")
        if cfg.secret("GMAIL_TOKEN_JSON_CONTENT") and not token_path.exists():
            # cloud sessions: the token file's contents live in an environment variable, not on disk
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(cfg.secret("GMAIL_TOKEN_JSON_CONTENT"))
        if (cfg.secret("GMAIL_OAUTH_JSON") or token_path.exists()) and cfg.secret("GMAIL_SENDER"):
            from .outreach.gmail_client import GmailClient, GmailUnavailable
            try:
                out["gmail"] = GmailClient(cfg.root / (cfg.secret("GMAIL_OAUTH_JSON") or "secrets/gmail_oauth_client.json"), token_path,
                                           cfg.secret("GMAIL_SENDER"), cfg.get("operator.sender_name"), interactive=interactive)
            except GmailUnavailable as e:
                out["missing"].append(f"Gmail ({e})")
            except Exception as e:  # noqa: BLE001
                out["missing"].append(f"Gmail ({type(e).__name__}: {e})")
        else:
            out["missing"].append("GMAIL_OAUTH_JSON/GMAIL_SENDER")
    if "twilio" in need:
        if cfg.has("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_MESSAGING_SERVICE_SID"):
            from .outreach.twilio_client import TwilioClient
            cb = (cfg.secret("WEBHOOK_PUBLIC_URL") or "").rstrip("/")
            out["twilio"] = TwilioClient(cfg.secret("TWILIO_ACCOUNT_SID"), cfg.secret("TWILIO_AUTH_TOKEN"), cfg.secret("TWILIO_MESSAGING_SERVICE_SID"),
                                         status_callback=f"{cb}/twilio/status" if cb else None)
        else:
            out["missing"].append("TWILIO_*")
    if out["missing"]:
        log.warning("missing credentials: %s", ", ".join(out["missing"]))
    return out


def _open(cfg):
    conn = db.connect(cfg.db_path)
    db.init_db(conn)
    return conn


def cmd_init_db(cfg, args):
    _open(cfg).close()
    print(f"schema ready at {cfg.db_path}")


def cmd_run_daily(cfg, args):
    from .enrich import Enricher
    from .ingest.pipeline import ingest
    from .outreach.send import run_outreach
    from .ranking import rerank
    from .scoring import run_scoring
    from .valuation import apify_comps_fn, run_valuation
    conn = _open(cfg)
    c = build_clients(cfg, conn)
    summary: dict[str, Any] = {"date": today_et(), "missing": c["missing"]}
    steps = args.steps.split(",") if args.steps else ["ingest", "score", "value", "enrich", "outreach"]
    fixture = Path(args.fixture) if args.fixture else None
    try:
        if "ingest" in steps:
            summary["ingest"] = ingest(conn, cfg, c["apify"], fixture=fixture)
    except Exception as e:  # noqa: BLE001 — a dead scrape must not block scoring of what we already have
        log.exception("ingest failed: %s", e)
        summary["ingest"] = {"error": str(e)}
    if "score" in steps:
        summary["score"] = run_scoring(conn, cfg, c["llm"], force=args.force)
    if "value" in steps:
        comps_fn = None
        if args.comps_fixture:
            comps = json.loads(Path(args.comps_fixture).read_text())
            comps_fn = lambda p: comps  # noqa: E731
        elif cfg.get("valuation.comps_source", "homeharvest") == "homeharvest":
            from .ingest.homeharvest_src import comps_fn as hh_comps_fn
            comps_fn = hh_comps_fn(cfg, conn)
        elif c["apify"]:
            comps_fn = apify_comps_fn(cfg, c["apify"], conn)
        summary["value"] = run_valuation(conn, cfg, comps_fn)
        summary["rank"] = rerank(conn, cfg)
    if "enrich" in steps:
        if args.offline:
            summary["enrich"] = {"skipped": "offline"}
        else:
            summary["enrich"] = Enricher(cfg, conn).run()
    if "outreach" in steps:
        summary["outreach"] = run_outreach(conn, cfg, c["llm"], c["gmail"], c["twilio"], asana=_asana(cfg))
    from .dashboard_sync import sync as sync_dashboard
    summary["dashboard"] = sync_dashboard(conn, cfg, include_connections=True)
    print(json.dumps(summary, indent=2, default=str))
    print(plain_summary(conn, summary))


def plain_summary(conn, summary: dict[str, Any], top: int = 10) -> str:
    """The last lines of every run, in plain words: what came in, and the best leads with the offer and the agent."""
    out = ["", "=" * 70, f"RUN SUMMARY {summary.get('date', '')}"]
    ing = summary.get("ingest") or {}
    if ing.get("error"):
        out.append(f"Scrape failed: {ing['error'][:200]}")
    elif ing:
        for name, st in (ing.get("sources") or {}).items():
            out.append(f"  {name:<12} " + (f"{st.get('count', ing.get('seen', 0))} listings" if st.get("ok") else f"FAILED: {str(st.get('error'))[:120]}"))
        out.append(f"  kept {ing.get('kept', 0)} of {ing.get('seen', 0)} (new {ing.get('new', 0)}), filtered out {ing.get('filtered', 0)}")
    sc = summary.get("score") or {}
    if sc:
        out.append(f"  scored {sc.get('scored', 0)} (Claude {sc.get('claude', 0)}, rules {sc.get('rules', 0)}), cost ${sc.get('llm_cost_usd', 0):.2f}")
        if sc.get("excluded_reo_short_sale"):
            out.append(f"  removed {sc['excluded_reo_short_sale']} REO / short sale listings")
        if sc.get("claude_failed"):
            out.append(f"  WARNING: {sc['claude_failed']} Claude scoring calls failed. " + (sc.get("warning") or "See logs/titan.log."))
    if sc.get("claude_cap_reached"):
        out.append("  Claude cap reached this run (scoring.max_claude_calls_per_run); the rest are scored on the next runs")
    counts = dict(conn.execute("SELECT rating, COUNT(*) FROM valuations GROUP BY rating").fetchall())
    lanes = dict(conn.execute("SELECT COALESCE(lane,'?'), COUNT(*) FROM valuations GROUP BY lane").fetchall())
    ideal_n = conn.execute("SELECT COUNT(*) FROM valuations v JOIN properties p ON p.id=v.property_id WHERE v.lane='SITTING' "
                           "AND (p.price_cuts > 0 OR p.back_on_market)").fetchone()[0]
    out.append(f"  targets: sitting 60+ days {lanes.get('SITTING', 0)} (with price drops / back on market: {ideal_n}) · "
               f"fresh deals {lanes.get('FRESH_DEAL', 0)} · watching {lanes.get('WATCH', 0)}")
    out.append(f"  ratings: HOT {counts.get('HOT', 0)} · WARM {counts.get('WARM', 0)} · COLD {counts.get('COLD', 0)}")
    rows = conn.execute(
        "SELECT p.street, p.city, v.rating, v.list_price, v.offer_price, v.arv, s.score, a.name, a.email, v.lane, p.days_on_market "
        "FROM valuations v JOIN properties p ON p.id=v.property_id JOIN scores s ON s.property_id=p.id "
        "LEFT JOIN agents a ON a.id=p.agent_id WHERE v.rating IN ('HOT','WARM') "
        "ORDER BY CASE v.rating WHEN 'HOT' THEN 0 ELSE 1 END, CASE v.lane WHEN 'FRESH_DEAL' THEN 0 WHEN 'SITTING' THEN 1 ELSE 2 END, "
        "s.score DESC LIMIT ?", (top,)).fetchall()
    if rows:
        out.append(f"  best {len(rows)}:")
        for r in rows:
            out.append(f"   {r[2]:<4} {(r[9] or ''):<10} {r[10] if r[10] is not None else '?'}d  {r[0]}, {r[1]}  list ${r[3] or 0:,}  ARV ${r[5] or 0:,}  score {r[6]}"
                       f"  agent {r[7] or '-'} {r[8] or '(no email yet)'}")
    else:
        out.append("  no HOT or WARM leads this run")
    out.append("Next: upload reports/dashboard.json to Claude so it can review these and write the Gmail drafts.")
    out.append("=" * 70)
    return "\n".join(out)


def cmd_check_replies(cfg, args):
    from .replies.inbox import check_replies
    conn = _open(cfg)
    c = build_clients(cfg, conn, need=("llm", "gmail", "twilio"))
    res = check_replies(conn, cfg, c["gmail"], c["llm"], c["twilio"])
    asana = _asana(cfg)
    if asana is not None:   # every reply shows up on the agent's Asana card (conversations mirror)
        from .conversations import mirror_replies
        res["asana_mirror"] = mirror_replies(conn, cfg, asana)
    from .dashboard_sync import sync as sync_dashboard
    res["dashboard"] = sync_dashboard(conn, cfg)
    print(json.dumps(res, indent=2))


def cmd_report(cfg, args):
    from .report import build_report, run_report
    conn = _open(cfg)
    gmail = None if args.no_email else build_clients(cfg, conn, need=("gmail",))["gmail"]
    path = run_report(conn, cfg, gmail)
    from .dashboard_sync import sync as sync_dashboard
    sync_dashboard(conn, cfg, include_connections=True)
    print(path)
    if args.print:
        print(build_report(conn, cfg))


def cmd_status(cfg, args):
    from .status import render
    print(render(_open(cfg), cfg, today_et()))


def cmd_install_cron(cfg, args):
    py = Path(sys.executable)
    root = cfg.root
    w = cfg["outreach"]["sms_window"]
    lines = [
        "# --- titan lead engine (managed by `make install-cron`) ---",
        f"CRON_TZ={w.get('timezone', 'America/New_York')}",
        f"PATH={Path.home() / '.local/bin'}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        f"*/10 * * * * cd {root} && git pull -q --ff-only >> {root}/logs/cron.log 2>&1 && {py} -m pip install -q -r requirements.txt >> {root}/logs/cron.log 2>&1   # self-update: merged fixes land without anyone at the Mac",
        f"30 5 * * * cd {root} && /usr/bin/caffeinate -i {py} -m titan.cli run-daily >> {root}/logs/cron.log 2>&1",
        f"*/15 * * * * cd {root} && {py} -m titan.cli check-replies >> {root}/logs/cron.log 2>&1",
        f"*/20 8-17 * * * cd {root} && {py} -m titan.cli approvals --jitter 600 >> {root}/logs/cron.log 2>&1   # Asana-approved emails, spread out",
        f"0 {cfg.get('report.hour_et', 7)} * * * cd {root} && {py} -m titan.cli report >> {root}/logs/cron.log 2>&1",
        f"5 {w['start_hour']} * * 1-5 cd {root} && {py} -m titan.cli run-daily --steps outreach >> {root}/logs/cron.log 2>&1   # second outreach pass inside the SMS window",
        "# --- end titan ---",
    ]
    block = "\n".join(lines)
    if args.dry_run:
        print(block)
        return
    import subprocess
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = cur.stdout if cur.returncode == 0 else ""
    if "# --- titan lead engine" in existing:
        pre = existing.split("# --- titan lead engine")[0]
        post = existing.split("# --- end titan ---")[-1]
        existing = pre + post.lstrip("\n")
    new = existing.rstrip("\n") + ("\n" if existing.strip() else "") + block + "\n"
    subprocess.run(["crontab", "-"], input=new, text=True, check=True)
    print("crontab updated:\n" + block)


def cmd_webhook(cfg, args):
    import uvicorn
    from .webhook import build_app
    uvicorn.run(build_app(cfg), host=args.host, port=args.port)


def cmd_dashboard(cfg, args):
    from .dashboard_local import serve
    serve(cfg, port=args.port, open_browser=not args.no_open)


def cmd_sync_dashboard(cfg, args):
    from .dashboard_sync import sync as sync_dashboard
    print(json.dumps(sync_dashboard(_open(cfg), cfg, include_connections=not args.fast), indent=2))


def cmd_mls_shortlist(cfg, args):
    from .ingest import mls_csv
    from .shortlist import rank, write
    src = Path(args.csv)
    rows = rank(mls_csv.parse_file(src), cfg)
    out = Path(args.out) if args.out else cfg.root / "reports" / f"mls_shortlist_{today_et()}.csv"
    paste = write(rows, out, args.paste)
    print(f"{len(rows)} listings pass the filters → {out}\nTop {args.paste} MLS numbers for Matrix (paste into MLS # search): {paste}")
    for r in rows[:15]:
        print(f"{r['rank']:>3}. {r['points']:>5}  {r['mls']}  {r['address']}, {r['county']}  ${r['price']:,}  {r['why']}")


def cmd_scrape_test(cfg, args):
    """Small live HomeHarvest pull (one ZIP or city) so the Mac can confirm realtor.com answers before a full run."""
    from .ingest import homeharvest_src, realtor
    from .ingest.normalize import passes_filters
    try:
        scrape = homeharvest_src._scraper()
    except ImportError:
        print("HomeHarvest is not installed. Run: make setup")
        sys.exit(1)
    m = cfg["market"]
    try:
        items = scrape(args.location, listing_type="for_sale", return_type="raw", property_type=homeharvest_src._types(cfg),
                       price_min=m["price_min"], price_max=m["price_max"], past_days=m["lookback_days"], limit=args.limit,
                       exclude_pending=True, extra_property_data=True)
    except Exception as e:  # noqa: BLE001
        print(f"FAILED: realtor.com did not answer ({type(e).__name__}: {e}).\nIf this is a cloud computer, that is expected: run it on the Mac.")
        sys.exit(1)
    ls = [l for l in (realtor.map_item(i) for i in items) if l]
    kept = [l for l in ls if passes_filters(l, cfg)[0]]
    with_email = sum(1 for l in ls if l.agent_email)
    print(f"OK: {len(items)} listings from realtor.com, {len(ls)} read, {len(kept)} pass your filters, {with_email} have an agent email.")
    for l in ls[:10]:
        print(f"  ${l.list_price or 0:>9,}  {l.street}, {l.city}  agent: {l.agent_name or '-'} {l.agent_email or ''}")


def cmd_approve_offer(cfg, args):
    from .approve import approve_offer
    conn = _open(cfg)
    gmail = build_clients(cfg, conn, need=("gmail",))["gmail"]
    res = approve_offer(conn, cfg, args.property_id, args.price, gmail, args.note)
    print(json.dumps(res, indent=2))
    print("Draft only: read it in Gmail (Titan/Review) or the file above, then send it yourself." if res["status"] == "draft" else "")


def cmd_review_queue(cfg, args):
    from .review import pending
    files = pending(cfg)
    if not files:
        print("No leads waiting for review.")
    for f in files:
        print(f"\n{'=' * 70}\n{f}\n{'=' * 70}\n{f.read_text()}")


def cmd_score_test(cfg, args):
    """Score 3 stored listings through the configured provider and show the result (checks the Claude Code plan path)."""
    from .scoring import score_llm_batch
    conn = _open(cfg)
    llm = LLM(cfg, conn)
    print(f"provider: {llm.provider}" + ("  (Claude Code plan, no API spend)" if llm.provider == "claude_code" else ""))
    if not llm.available:
        print("Claude is not available: install/log in to Claude Code (`claude` then /login), then retry.")
        sys.exit(1)
    rows = [db.row_to_dict(r) for r in conn.execute("SELECT * FROM properties WHERE remarks != '' AND lead_status != 'EXCLUDED' "
                                                     "ORDER BY days_on_market DESC LIMIT 3")]
    if not rows:
        print("No stored listings yet: run `make run-daily` (or the ingest step) first.")
        sys.exit(1)
    res = score_llm_batch(llm, rows, cfg.get("scoring.signals", {}), cfg.get("scoring.negatives", {}))
    for p in rows:
        r = res.get(p["id"])
        print(f"- {p['street']}, {p['city']} ({p.get('days_on_market')}d): " +
              (f"score {r['score']} · {r['summary']}" if r else f"no result ({llm.last_error or 'see logs'})"))
    print("OK" if res else "FAILED")


def _asana(cfg):
    if cfg.get("outreach.approval") == "asana" and cfg.secret("ASANA_TOKEN"):
        from .asana import Asana
        return Asana(cfg.secret("ASANA_TOKEN"))
    return None


def cmd_approvals(cfg, args):
    """Every 20 minutes in the day: rewrite cards that got feedback, then send up to per_tick approved emails."""
    from .approvals import send_approved, sync_feedback
    conn = _open(cfg)
    asana = _asana(cfg)
    if asana is None:
        print("ASANA_TOKEN missing in .env (Asana → My settings → Apps → Developer apps → Personal access token)")
        sys.exit(1)
    c = build_clients(cfg, conn, need=("llm", "gmail"))
    fb = sync_feedback(conn, cfg, asana, c["llm"], c["gmail"])
    sent = send_approved(conn, cfg, asana, c["gmail"], jitter_s=args.jitter, llm=c["llm"])
    from .conversations import mirror_replies
    mirrored = mirror_replies(conn, cfg, asana)
    print(json.dumps({"feedback": fb, "send": sent, "asana_mirror": mirrored}, indent=2))


def cmd_test_email(cfg, args):
    """Send a [TEST] copy of one card's email (by outreach id) to an address, from Charles's Gmail. The agent gets nothing."""
    from .approvals import send_test_copy
    conn = _open(cfg)
    row = db.row_to_dict(conn.execute("SELECT * FROM outreach WHERE id=?", (args.outreach_id,)).fetchone())
    if not row:
        print(f"no outreach #{args.outreach_id}"); sys.exit(1)
    gmail = build_clients(cfg, conn, need=("gmail",))["gmail"]
    asana = _asana(cfg)
    args.to = args.to or cfg.secret("REPORT_TO")
    card = {"to": row["to_address"], "subject": row["subject"], "body": row["body"]}
    if gmail is None:
        print("Charles's Gmail is not connected on this machine"); sys.exit(1)
    gmail.send(args.to, f"[TEST] {card['subject']}", f"(Test copy. The real email would go to {card['to']}.)\n\n{card['body']}")
    if asana and row.get("asana_task_id"):
        asana.comment(row["asana_task_id"], f"Test copy sent to {args.to} by hand.")
    print(f"test copy of outreach #{args.outreach_id} sent to {args.to}")


def cmd_rewrite_pending(cfg, args):
    """Regenerate every email still in To Review with the current protocol and refresh its Asana card."""
    from .approvals import rewrite_pending
    conn = _open(cfg)
    asana = _asana(cfg)
    if asana is None:
        print("ASANA_TOKEN missing in .env")
        sys.exit(1)
    c = build_clients(cfg, conn, need=("llm",))
    print(json.dumps(rewrite_pending(conn, cfg, asana, c["llm"], only_old_style=not args.all), indent=2))


def cmd_doctor(cfg, args):
    from . import doctor
    rows = doctor.run(cfg)
    print(doctor.render(rows))
    if args.strict and any(r["status"] in ("fail", "warn") for r in rows):
        sys.exit(1)


def cmd_gmail_auth(cfg, args):
    conn = _open(cfg)
    c = build_clients(cfg, conn, need=("gmail",), interactive=True)
    print("gmail ok" if c["gmail"] else f"gmail not configured: {c['missing']}")


def cmd_gmail_test(cfg, args):
    """Prove the outreach mailbox works end to end: one draft to itself under the review label. Sends nothing."""
    conn = _open(cfg)
    c = build_clients(cfg, conn, need=("gmail",), interactive=False)
    if not c["gmail"]:
        print(f"gmail NOT working: {c['missing']} (run `make doctor` for the fix)")
        sys.exit(1)
    g = c["gmail"]
    addr = g.svc.users().getProfile(userId="me").execute()["emailAddress"]
    label = cfg.get("outreach.gmail_review_label", "Titan/Review")
    d = g.create_draft(addr, "Titan test draft (delete me)", "Test only. If you can read this in Drafts, the engine can draft from this mailbox.", label=label)
    print(f"gmail OK: signed in as {addr}; .env GMAIL_SENDER = {cfg.secret('GMAIL_SENDER')}; test draft {d['draft_id']} is in Drafts under {label}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="titan")
    ap.add_argument("--config", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    rd = sub.add_parser("run-daily")
    rd.add_argument("--steps", default=None, help="comma list of ingest,score,value,enrich,outreach")
    rd.add_argument("--fixture", default=None, help="JSON listings fixture instead of live scrape")
    rd.add_argument("--comps-fixture", default=None)
    rd.add_argument("--offline", action="store_true", help="skip network enrichment")
    rd.add_argument("--force", action="store_true", help="re-score everything")
    sub.add_parser("check-replies")
    rp = sub.add_parser("report")
    rp.add_argument("--no-email", action="store_true")
    rp.add_argument("--print", action="store_true")
    sub.add_parser("status")
    ic = sub.add_parser("install-cron")
    ic.add_argument("--dry-run", action="store_true")
    wh = sub.add_parser("webhook")
    wh.add_argument("--host", default="0.0.0.0")
    wh.add_argument("--port", type=int, default=8080)
    sub.add_parser("gmail-auth")
    sub.add_parser("gmail-test", help="make one test draft in the outreach mailbox (sends nothing)")
    dl = sub.add_parser("dashboard", help="open the dashboard from this machine (no hosting needed)")
    dl.add_argument("--port", type=int, default=8765)
    dl.add_argument("--no-open", action="store_true")
    sd = sub.add_parser("sync-dashboard", help="push the dashboard snapshot to Vercel Blob now")
    sd.add_argument("--fast", action="store_true", help="skip the connection checks")
    dr = sub.add_parser("doctor", help="check every credential/setting the live run needs")
    dr.add_argument("--strict", action="store_true", help="exit 1 on any warn/fail (for scripts)")
    ms = sub.add_parser("mls-shortlist", help="rank a thin MLS hot sheet; writes a CSV + MLS numbers to pull a full export")
    ms.add_argument("csv")
    ms.add_argument("--out", default=None)
    ms.add_argument("--paste", type=int, default=100, help="how many MLS numbers to list for the Matrix search")
    ao = sub.add_parser("approve-offer", help="Rodrigo approves a price for one house -> written offer as a Gmail draft (never sends)")
    ao.add_argument("property_id", type=int)
    ao.add_argument("price", type=int)
    ao.add_argument("--note", default=None)
    sub.add_parser("review-queue", help="print every lead waiting for Rodrigo's review")
    ap_ = sub.add_parser("approvals", help="Asana email approvals: rewrite from comments, send approved emails in the window")
    ap_.add_argument("--jitter", type=int, default=0, help="wait a random 0..N seconds first, so send times vary")
    te = sub.add_parser("test-email", help="send a [TEST] copy of one card's email to yourself (the agent gets nothing)")
    te.add_argument("outreach_id", type=int)
    te.add_argument("--to", default=None, help="defaults to REPORT_TO")
    rp = sub.add_parser("rewrite-pending", help="regenerate To Review emails written before the protocol and update their cards")
    rp.add_argument("--all", action="store_true", help="redo every To Review card, not only the old-style ones")
    sub.add_parser("score-test", help="score 3 stored listings with the configured provider (Claude Code plan by default)")
    st = sub.add_parser("scrape-test", help="quick live check of the free Realtor.com scraper (run on the Mac)")
    st.add_argument("location", nargs="?", default="33023")
    st.add_argument("--limit", type=int, default=20)
    args = ap.parse_args(argv)
    cfg = config.load(args.config) if args.config else config.load()
    setup_logging(cfg.path("logs"))
    {"init-db": cmd_init_db, "run-daily": cmd_run_daily, "check-replies": cmd_check_replies, "report": cmd_report, "status": cmd_status,
     "install-cron": cmd_install_cron, "webhook": cmd_webhook, "gmail-auth": cmd_gmail_auth, "gmail-test": cmd_gmail_test, "doctor": cmd_doctor, "sync-dashboard": cmd_sync_dashboard, "dashboard": cmd_dashboard, "mls-shortlist": cmd_mls_shortlist, "scrape-test": cmd_scrape_test, "approve-offer": cmd_approve_offer, "review-queue": cmd_review_queue, "score-test": cmd_score_test, "approvals": cmd_approvals, "rewrite-pending": cmd_rewrite_pending, "test-email": cmd_test_email}[args.cmd](cfg, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
