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
        "WHERE p.lead_status IN ('NEEDS_REVIEW','FLEXIBLE','HOT_LEAD','NEEDS_CALL','OFFER_DRAFTED') "
        "ORDER BY CASE p.lead_status WHEN 'NEEDS_REVIEW' THEN 0 WHEN 'FLEXIBLE' THEN 1 ELSE 2 END, p.lead_status_at DESC LIMIT ?", (limit,)).fetchall()
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
    """Next step for Rodrigo. Conversation first: never a number to anchor on; Rodrigo decides the price per house."""
    cls = last["classification"] if last else None
    st = p.get("lead_status")
    if st == "OFFER_DRAFTED":
        return "Your approved offer is a draft in Gmail (Titan/Review). Read it and send it when ready."
    if cls == "COUNTER_WITH_NUMBER":
        return "The agent shared a seller number. Read the review summary, then approve a price (approve-offer) or pass."
    if cls == "ASK_FOR_OFFER":
        return "The agent invited an offer. We sent a holding reply only; review and approve a price if it works."
    if st == "FLEXIBLE" or cls == "INTERESTED":
        return "Agent says the seller has some flexibility (not confirmed with a number). A short call to learn timing and condition."
    if cls == "QUESTION":
        return "They asked something we don't answer automatically. Reply or call."
    return "Read the conversation and decide the next step."


def build_report(conn, cfg: Any, run_date: str | None = None) -> str:
    run_date = run_date or today_et()
    since = iso(now_utc() - timedelta(hours=24))
    f = db.funnel(conn, run_date)
    usage = db.usage_summary(conn, since)
    usage_total = db.usage_summary(conn)
    leads = hot_leads(conn, cfg.get("report.max_hot_leads", 10))
    runs = conn.execute("SELECT step, status, counts, notes FROM daily_runs WHERE run_date=? ORDER BY id", (run_date,)).fetchall()
    lines = [f"# Titan morning report — {run_date}", "", f"Mode: **{cfg.mode}** · Channel: **{cfg.channel}**", ""]
    lines += [f"## Conversations for you ({len(leads)})", ""]
    if not leads:
        lines.append("_No conversations need you yet. Replies land here as agents answer._")
    from .review import pending
    rv = [r for r in pending(cfg) if r.name.startswith(run_date)] or pending(cfg)[-10:]
    if rv:
        lines += ["", f"Review summaries waiting ({len(rv)}): " + ", ".join(f"`{r.relative_to(cfg.root)}`" for r in rv) +
                  " — or run `.venv/bin/python -m titan.cli review-queue`", ""]
    for i, p in enumerate(leads, 1):
        lines += [f"### {i}. {p['street']}, {p['city']} — list {money(p['list_price'])} ({p['lead_status']})",
                  f"- Agent: {p.get('agent_name') or 'unknown'} · {p.get('agent_phone') or 'no phone'} · {p.get('agent_email') or 'no email'}",
                  f"- Last message ({p.get('channel') or '-'} / {p.get('last_class') or '-'}): {p['last_message']}",
                  f"- Motivation {p.get('score')} · ARV {money(p.get('arv'))} ({p.get('arv_method')}, {p.get('comp_count') or 0} comps, {p.get('repair_tier')} repairs) · internal screening MAO {money(p.get('mao'))} (not an offer) · {p.get('listing_url') or ''}",
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
    text = build_report(conn, cfg, run_date) + "\n\n" + lead_data_section(conn, cfg)
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


def lead_data_section(conn, cfg: Any, limit: int = 40) -> str:
    """Everything a reviewer needs without access to the Mac: the top HOT/WARM targets with remarks, agent and the
    internal screening range, plus any pending review summaries. Ends with a JSON block (between TITAN-DATA markers)
    that the cloud Claude session reads from Rodrigo's inbox each morning."""
    import json
    from .valuation import preliminary_range
    rows = conn.execute(
        "SELECT p.*, v.rating, v.lane, v.arv, v.arv_method, v.comp_count, v.repair_tier, v.repair_cost, v.mao, s.score, s.summary AS score_summary, "
        "a.name AS agent_name, a.email AS agent_email, a.phone AS agent_phone, a.brokerage FROM valuations v "
        "JOIN properties p ON p.id=v.property_id JOIN scores s ON s.property_id=p.id LEFT JOIN agents a ON a.id=p.agent_id "
        "WHERE v.rating IN ('HOT','WARM') AND p.lead_status != 'EXCLUDED' "
        "ORDER BY CASE v.rating WHEN 'HOT' THEN 0 ELSE 1 END, CASE v.lane WHEN 'FRESH_DEAL' THEN 0 WHEN 'SITTING' THEN 1 ELSE 2 END, "
        "(p.price_cuts > 0 OR p.back_on_market) DESC, s.score DESC LIMIT ?", (limit,)).fetchall()
    leads = []
    for r in rows:
        p = dict(r)
        rng = preliminary_range(p, cfg)
        leads.append({"id": p["id"], "rating": p["rating"], "lane": p["lane"], "street": p["street"], "city": p["city"], "zip": p["zip"],
                      "county": p["county"], "list_price": p["list_price"], "original_list_price": p["original_list_price"],
                      "price_cuts": p["price_cuts"], "days_on_market": p["days_on_market"], "back_on_market": bool(p["back_on_market"]),
                      "beds": p["beds"], "baths": p["baths"], "sqft": p["sqft"], "year_built": p["year_built"],
                      "property_type": p["property_type"], "url": p["listing_url"], "score": p["score"], "why": p["score_summary"],
                      "remarks": (p["remarks"] or "")[:700], "agent": p["agent_name"], "agent_email": p["agent_email"],
                      "agent_phone": p["agent_phone"], "brokerage": p["brokerage"], "lead_status": p["lead_status"],
                      "internal_range": [rng["low"], rng["high"]] if rng else None, "range_uncertainty": rng["uncertainty"] if rng else None,
                      "arv": p["arv"], "arv_method": p["arv_method"], "comps": p["comp_count"]})
    counts = dict(conn.execute("SELECT COALESCE(lane,'?'), COUNT(*) FROM valuations GROUP BY lane").fetchall())
    from .review import pending
    reviews = [f.read_text()[:4000] for f in pending(cfg)[-10:]]
    lines = ["## Lead data (for review)", "",
             f"Lanes: sitting 60+ days {counts.get('SITTING', 0)} · fresh deals {counts.get('FRESH_DEAL', 0)} · watching {counts.get('WATCH', 0)}", ""]
    for l in leads[:15]:
        lines.append(f"- {l['rating']} {l['lane']} · {l['street']}, {l['city']} · ${l['list_price'] or 0:,} · {l['days_on_market']} days · "
                     f"{l['price_cuts'] or 0} cut(s) · {l['agent'] or '?'} {l['agent_email'] or ''}")
    payload = {"version": 1, "date": today_et(), "lanes": counts, "leads": leads, "reviews": reviews}
    import base64
    raw = json.dumps(payload, default=str, separators=(",", ":")).encode("utf-8")
    b64 = base64.b64encode(raw).decode("ascii")
    lines += ["", "-----BEGIN TITAN-DATA-B64-----"] + [b64[i:i + 76] for i in range(0, len(b64), 76)] + ["-----END TITAN-DATA-B64-----"]
    # base64 in 76-char lines survives email line wrapping; the old raw JSON block got newlines inside strings
    return "\n".join(lines)
