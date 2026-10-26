"""SCOREBOARD (Rodrigo, 2026-10-25: "the website should work like our Asana board on a higher level, with stats that update:
reply rates, answer rates, new properties..."). One function computes everything the Board tab and the morning report show,
from the database (plus Asana card counts when a token is at hand). Pure reads; never raises into the caller."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import db
from .util import et_day_bounds, log, now_utc, parse_iso

ET = ZoneInfo("America/New_York")
CLOSED_STATES = ("CLOSED", "STOPPED", "BOUNCED")
RODRIGO_STATES = ("ESCALATED", "HOT_LEAD")


def _pct(n: int, d: int) -> float | None:
    return round(100.0 * n / d, 1) if d else None


def _day(s: str | None) -> str | None:
    dt = parse_iso(s)
    return dt.astimezone(ET).strftime("%Y-%m-%d") if dt else None


def conversation_rows(conn) -> list[dict[str, Any]]:
    """Every email/text thread with who, which house, the last message and which column of the board it belongs in."""
    rows = []
    for t in conn.execute("SELECT * FROM threads ORDER BY updated_at DESC LIMIT 300").fetchall():
        td = db.row_to_dict(t)
        msgs = [db.row_to_dict(m) for m in conn.execute(
            "SELECT id, direction, body, classification, action, created_at FROM messages WHERE thread_id=? ORDER BY id", (t["id"],)).fetchall()]
        if not msgs:
            continue
        agent = db.row_to_dict(conn.execute("SELECT id, name, brokerage, email, phone FROM agents WHERE id=?", (td["agent_id"],)).fetchone()) or {}
        pids = json.loads(td["property_ids"] or "[]")
        props = [db.row_to_dict(r) for r in conn.execute(
            f"SELECT id, street, city, list_price, lead_status FROM properties WHERE id IN ({','.join('?' * len(pids)) or 'NULL'})", pids).fetchall()]
        last = msgs[-1]
        inbound = [m for m in msgs if m["direction"] == "in" and m.get("classification") != "BOUNCE"]
        bounced = any(m.get("classification") == "BOUNCE" for m in msgs)
        leads = {p.get("lead_status") for p in props}
        if td["state"] in CLOSED_STATES or bounced:
            col = "closed"
        elif td["state"] in RODRIGO_STATES or leads & {"NEEDS_REVIEW", "NEEDS_CALL"}:
            col = "needs_rodrigo"
        elif last["direction"] == "in":
            col = "needs_answer"
        elif inbound:
            col = "waiting_on_agent"      # they answered once, we answered back, ball is on their side
        else:
            col = "no_reply_yet"          # first touch out, nothing back
        rows.append({"thread_id": td["id"], "channel": td["channel"], "state": td["state"], "column": col, "bounced": bounced,
                     "agent": agent, "properties": props, "lead_status": next(iter(leads), None),
                     "replies": len(inbound), "last_direction": last["direction"], "last_at": last["created_at"],
                     "last_class": last.get("classification"), "last_snippet": (last.get("body") or "")[:220],
                     "first_out_at": next((m["created_at"] for m in msgs if m["direction"] == "out"), None),
                     "first_reply_at": inbound[0]["created_at"] if inbound else None})
    return rows


def daily_series(conn, days: int = 14, now: datetime | None = None) -> list[dict[str, Any]]:
    """Per ET day, newest last: new listings, emails sent, replies received, answers we sent, cards posted."""
    today = (now or now_utc()).astimezone(ET).date()
    out = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        lo, hi = et_day_bounds(d)
        q = lambda sql: conn.execute(sql, (lo, hi)).fetchone()[0]  # noqa: E731
        out.append({"date": d,
                    "new_properties": q("SELECT COUNT(*) FROM properties WHERE first_seen>=? AND first_seen<?"),
                    "emails_sent": q("SELECT COUNT(*) FROM outreach WHERE channel='email' AND status='sent' AND sent_at>=? AND sent_at<?"),
                    "replies": q("SELECT COUNT(*) FROM messages WHERE direction='in' AND COALESCE(classification,'')<>'BOUNCE' AND created_at>=? AND created_at<?"),
                    "answers": q("SELECT COUNT(*) FROM messages WHERE direction='out' AND action LIKE 'reply:%' AND created_at>=? AND created_at<?"),
                    "cards": q("SELECT COUNT(*) FROM outreach WHERE asana_task_id IS NOT NULL AND kind='first_touch' AND created_at>=? AND created_at<?"),
                    "hot_found": q("SELECT COUNT(*) FROM valuations v JOIN properties p ON p.id=v.property_id WHERE v.rating='HOT' AND p.first_seen>=? AND p.first_seen<?")})
    return out


def asana_counts(cfg: Any, asana=None) -> dict[str, int] | None:
    """Cards per section on the approvals board (To Review / Approved / Sent / Skip). None when Asana isn't reachable."""
    a = cfg.get("outreach.asana", {}) or {}
    if asana is None:
        if cfg.get("outreach.approval") != "asana" or not cfg.secret("ASANA_TOKEN"):
            return None
        from .asana import Asana
        asana = Asana(cfg.secret("ASANA_TOKEN"))
    try:
        out = {k: len(asana.section_tasks(a[k])) for k in ("to_review", "approved", "sent", "skip") if a.get(k)}
        try:
            from .approvals import needs_revision_section
            out["needs_revision"] = len(asana.section_tasks(needs_revision_section(cfg, asana)))
        except Exception:  # noqa: BLE001
            pass
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("stats: Asana counts unavailable: %s", e)
        return None


def compute_stats(conn, cfg: Any, days: int = 14, now: datetime | None = None, asana=None, with_asana: bool = True) -> dict[str, Any]:
    try:
        convs = conversation_rows(conn)
        q = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
        sent = q("SELECT COUNT(*) FROM outreach WHERE channel='email' AND status='sent' AND kind='first_touch'")
        sent_agents = q("SELECT COUNT(DISTINCT agent_id) FROM outreach WHERE channel='email' AND status='sent' AND kind='first_touch'")
        replied_agents = len({c["agent"].get("id") for c in convs if c["replies"]})
        bounced = sum(1 for c in convs if c["bounced"])
        answered = sum(1 for c in convs if c["replies"] and c["column"] != "needs_answer")
        cols = {k: [c for c in convs if c["column"] == k] for k in ("needs_answer", "needs_rodrigo", "waiting_on_agent", "no_reply_yet", "closed")}
        hours = [(parse_iso(c["first_reply_at"]) - parse_iso(c["first_out_at"])).total_seconds() / 3600
                 for c in convs if c["first_reply_at"] and c["first_out_at"] and parse_iso(c["first_reply_at"]) >= parse_iso(c["first_out_at"])]
        today = (now or now_utc()).astimezone(ET).strftime("%Y-%m-%d")
        lo, hi = et_day_bounds(today)
        stats: dict[str, Any] = {
            "as_of": (now or now_utc()).isoformat(),
            "totals": {
                "emails_sent": sent, "agents_emailed": sent_agents, "agents_replied": replied_agents,
                "reply_rate_pct": _pct(replied_agents, sent_agents), "bounces": bounced, "bounce_rate_pct": _pct(bounced, sent_agents),
                "conversations": len([c for c in convs if c["replies"]]), "answered": answered,
                "answer_rate_pct": _pct(answered, len([c for c in convs if c["replies"]])),
                "awaiting_our_answer": len(cols["needs_answer"]), "needs_rodrigo": len(cols["needs_rodrigo"]),
                "waiting_on_agent": len(cols["waiting_on_agent"]), "no_reply_yet": len(cols["no_reply_yet"]), "closed": len(cols["closed"]),
                "median_hours_to_first_reply": round(sorted(hours)[len(hours) // 2], 1) if hours else None,
                "properties": q("SELECT COUNT(*) FROM properties"), "hot": q("SELECT COUNT(*) FROM valuations WHERE rating='HOT'"),
                "new_today": q("SELECT COUNT(*) FROM properties WHERE first_seen>=? AND first_seen<?", lo, hi),
                "sent_today": q("SELECT COUNT(*) FROM outreach WHERE channel='email' AND status='sent' AND sent_at>=? AND sent_at<?", lo, hi),
                "replies_today": q("SELECT COUNT(*) FROM messages WHERE direction='in' AND COALESCE(classification,'')<>'BOUNCE' AND created_at>=? AND created_at<?", lo, hi),
                "cards_waiting": q("SELECT COUNT(*) FROM outreach WHERE status='pending_approval'"),
                "do_not_contact": q("SELECT COUNT(*) FROM do_not_contact"),
            },
            "daily": daily_series(conn, days, now),
            "board": {k: v[:40] for k, v in cols.items()},
            "asana": asana_counts(cfg, asana) if with_asana else None,
        }
        return stats
    except Exception as e:  # noqa: BLE001 — the dashboard and the report must never break on the scoreboard
        log.warning("stats failed: %s", e)
        return {"error": str(e)[:200], "totals": {}, "daily": [], "board": {}, "asana": None}
