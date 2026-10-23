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
                out["missing"].append("ANTHROPIC_API_KEY (using rules fallback)")
        except LLMUnavailable as e:
            out["missing"].append(f"ANTHROPIC_API_KEY ({e})")
    if "gmail" in need:
        if cfg.secret("GMAIL_OAUTH_JSON") and cfg.secret("GMAIL_SENDER"):
            from .outreach.gmail_client import GmailClient, GmailUnavailable
            try:
                out["gmail"] = GmailClient(cfg.root / cfg.secret("GMAIL_OAUTH_JSON"), cfg.root / (cfg.secret("GMAIL_TOKEN_JSON") or "secrets/gmail_token.json"),
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
        summary["outreach"] = run_outreach(conn, cfg, c["llm"], c["gmail"], c["twilio"])
    from .dashboard_sync import sync as sync_dashboard
    summary["dashboard"] = sync_dashboard(conn, cfg, include_connections=True)
    print(json.dumps(summary, indent=2, default=str))


def cmd_check_replies(cfg, args):
    from .replies.inbox import check_replies
    conn = _open(cfg)
    c = build_clients(cfg, conn, need=("llm", "gmail", "twilio"))
    res = check_replies(conn, cfg, c["gmail"], c["llm"], c["twilio"])
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
        f"30 5 * * * cd {root} && {py} -m titan.cli run-daily >> {root}/logs/cron.log 2>&1",
        f"*/15 * * * * cd {root} && {py} -m titan.cli check-replies >> {root}/logs/cron.log 2>&1",
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
    st = sub.add_parser("scrape-test", help="quick live check of the free Realtor.com scraper (run on the Mac)")
    st.add_argument("location", nargs="?", default="33023")
    st.add_argument("--limit", type=int, default=20)
    args = ap.parse_args(argv)
    cfg = config.load(args.config) if args.config else config.load()
    setup_logging(cfg.path("logs"))
    {"init-db": cmd_init_db, "run-daily": cmd_run_daily, "check-replies": cmd_check_replies, "report": cmd_report, "status": cmd_status,
     "install-cron": cmd_install_cron, "webhook": cmd_webhook, "gmail-auth": cmd_gmail_auth, "doctor": cmd_doctor, "sync-dashboard": cmd_sync_dashboard, "dashboard": cmd_dashboard, "mls-shortlist": cmd_mls_shortlist, "scrape-test": cmd_scrape_test}[args.cmd](cfg, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
