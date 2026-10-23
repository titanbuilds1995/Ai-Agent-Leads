"""MORNING REPORT: top HOT_LEADs, funnel, cost, scrape health, blockers → reports/YYYY-MM-DD.md + email."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from . import db
from .util import iso, log, money, now_utc, today_et


def _one_line(s: str | None, n: int = 110) -> str:
    s = " ".join((s or "").split())
    return (s[: n - 1] + "…") if len(s) > n else s


def hot_leads(conn, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT p.*, v.mao, v.offer_price, v.arv, v.arv_method, v.comp_count, v.repair_tier, v.rating, s.score, a.name AS agent_name, a.phone AS agent_phone, a.email AS agent_email "
        "FROM properties p LEFT JOIN valuations v ON v.property_id=p.id LEFT JOIN scores s ON s.property_id=p.id LEFT JOIN agents a ON a.id=p.agent_id "
        "WHERE p.lead_status IN ('HOT_LEAD','NEEDS_CALL') ORDER BY p.lead_status_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        p = db.row_to_dict(r)
        last = conn.execute("SELECT m.body, m.classification, t.channel, t.state FROM messages m JOIN threads t ON t.id=m.thread_id "
                            "WHERE m.direction='in' AND t.agent_id=? ORDER BY m.created_at DESC LIMIT 1", (p["agent_id"],)).fetchone()
        p["last_message"] = _one_line(last["body"]) if last else "(no reply yet)"
        p["last_class"] = last["classification"] if last else None
        p["channel"] = last["channel"] if last else None
        p["say_this"] = say_this(p, last)
        out.append(p)
    return out


def say_this(p: dict[str, Any], last) -> str:
    cls = last["classification"] if last else None
    mao, offer = p.get("mao"), p.get("offer_price")
    if p.get("lead_status") == "NEEDS_CALL" and "email" in (p.get("lead_note") or ""):
        return f"Agent is interested but never sent an email. Call, get the email, send the written offer at {money(offer)}."
    if cls == "COUNTER_WITH_NUMBER":
        if "bridge" in (p.get("lead_note") or ""):
            return f"Their counter is above our bridge. Decide: walk, or go to {money(mao)} max (MAO)."
        return f"Their number is within 10% of MAO {money(mao)}. Confirm terms and lock the contract today."
    if cls == "ASK_FOR_OFFER":
        return f"Confirm they received the written offer at {money(offer)}; ask when the seller will respond."
    if cls == "INTERESTED":
        return f"Seller is open. Ask what number gets it done; we're at {money(offer)}, MAO {money(mao)}."
    if cls == "QUESTION":
        return "Answer their question on the call (outside auto-reply scope), then ask for a number."
    return f"Open with the as-is 14-day close; anchor at {money(offer)}, ceiling {money(mao)}."


def build_report(conn, cfg: Any, run_date: str | None = None) -> str:
    run_date = run_date or today_et()
    since = iso(now_utc() - timedelta(hours=24))
    f = db.funnel(conn, run_date)
    usage = db.usage_summary(conn, since)
    usage_total = db.usage_summary(conn)
    leads = hot_leads(conn, cfg.get("report.max_hot_leads", 10))
    runs = conn.execute("SELECT step, status, counts, notes FROM daily_runs WHERE run_date=? ORDER BY id", (run_date,)).fetchall()
    lines = [f"# Titan morning report — {run_date}", "", f"Mode: **{cfg.mode}** · Channel: **{cfg.channel}**", ""]
    lines += [f"## HOT leads to call today ({len(leads)})", ""]
    if not leads:
        lines.append("_No HOT_LEADs yet. Replies land here as agents answer._")
    for i, p in enumerate(leads, 1):
        lines += [f"### {i}. {p['street']}, {p['city']} — list {money(p['list_price'])}, MAO {money(p['mao'])} ({p['lead_status']})",
                  f"- Agent: {p.get('agent_name') or 'unknown'} · {p.get('agent_phone') or 'no phone'} · {p.get('agent_email') or 'no email'}",
                  f"- Last message ({p.get('channel') or '-'} / {p.get('last_class') or '-'}): {p['last_message']}",
                  f"- Motivation {p.get('score')} · ARV {money(p.get('arv'))} ({p.get('arv_method')}, {p.get('comp_count') or 0} comps, {p.get('repair_tier')} repairs) · offer {money(p.get('offer_price'))} · {p.get('listing_url') or ''}",
                  f"- **Say this:** {p['say_this']}", ""]
    lines += ["## Pipeline", "", "| metric | count |", "|---|---|"]
    for k in ("today_properties_seen", "today_new", "properties", "scored", "valued", "HOT", "WARM", "COLD", "agents", "agents_with_email",
              "today_emails", "today_sms", "emails_sent", "emails_drafted", "sms_sent", "sms_drafted", "replies", "hot_leads", "do_not_contact"):
        if k in f:
            lines.append(f"| {k} | {f[k]} |")
    lines += ["", f"API cost last 24h: **${usage['cost_usd']:.2f}** ({usage['calls']} calls, {usage['input_tokens']:,} in / {usage['output_tokens']:,} out) · all-time ${usage_total['cost_usd']:.2f}", ""]
    warnings = []
    for r in runs:
        c = json.loads(r["counts"]) if r["counts"] else {}
        if r["step"] == "ingest":
            h = c.get("health") or {}
            if h.get("warning"):
                warnings.append(h["warning"])
            for src, st in (c.get("sources") or {}).items():
                if not st.get("ok"):
                    warnings.append(f"source {src} failed: {st.get('error')}")
        if r["status"] not in ("ok",):
            warnings.append(f"step {r['step']}: {r['status']} {r['notes'] or ''}".strip())
    if not runs:
        warnings.append("no pipeline steps ran today")
    lines += ["## Warnings", ""] + ([f"- {w}" for w in warnings] or ["- none"]) + [""]
    blockers = (cfg.root / "BLOCKERS.md")
    if blockers.exists():
        txt = blockers.read_text().strip()
        if txt:
            lines += ["## BLOCKERS.md", "", txt, ""]
    pending = conn.execute("SELECT COUNT(*) FROM outreach WHERE status='draft' AND run_date=?", (run_date,)).fetchone()[0]
    if pending:
        lines += [f"_{pending} drafts are waiting for your review (Gmail label {cfg['outreach']['gmail_review_label']} / reports/drafts/{run_date})._", ""]
    return "\n".join(lines)


def run_report(conn, cfg: Any, gmail=None, run_date: str | None = None) -> Path:
    run_date = run_date or today_et()
    text = build_report(conn, cfg, run_date)
    out_dir = cfg.path("reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_date}.md"
    path.write_text(text)
    to = cfg.secret("REPORT_TO")
    status = "written"
    if gmail is not None and to:
        try:
            gmail.send(to, f"Titan morning report {run_date}", text)
            status = "written+emailed"
        except Exception as e:  # noqa: BLE001
            log.error("report email failed: %s", e)
            status = f"written; email failed: {e}"
    rid = db.start_run(conn, "report", run_date)
    db.finish_run(conn, rid, "ok", {"path": str(path), "status": status})
    log.info("report: %s (%s)", path, status)
    return path
