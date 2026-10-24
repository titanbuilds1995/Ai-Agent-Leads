"""OUTREACH step: pick targets, compose, then draft or send depending on mode/channel. All caps and cooldowns enforced here."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from .. import db
from ..llm import LLM
from ..util import in_send_window, iso, log, now_utc, parse_iso, today_et
from .compose import compose_email, compose_sms, seed_for, validate_sms

CONF_RANK = {"high": 3, "medium": 2, "low": 1}


def _recent_contact(conn, agent_id: int, channel: str, days: int) -> bool:
    since = iso(now_utc() - timedelta(days=days))
    r = conn.execute("SELECT 1 FROM outreach WHERE agent_id=? AND channel=? AND status IN ('sent','draft','queued','pending_approval') AND created_at>=? LIMIT 1",
                     (agent_id, channel, since)).fetchone()
    return r is not None


def _already_contacted_property(conn, property_id: int, channel: str | None = None) -> bool:
    rows = conn.execute("SELECT property_ids FROM outreach WHERE status IN ('sent','draft','queued','pending_approval','skipped')" + (" AND channel=?" if channel else ""),
                        ((channel,) if channel else ())).fetchall()
    return any(property_id in json.loads(r["property_ids"]) for r in rows)


def _today_count(conn, channel: str, run_date: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM outreach WHERE run_date=? AND channel=? AND status IN ('sent','draft','queued','pending_approval')",
                        (run_date, channel)).fetchone()[0]


def candidate_rows(conn, cfg: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT p.*, s.score, s.cited, v.rating, v.mao, v.offer_price, v.arv, v.repair_tier, v.repair_cost, v.arv_method, v.comp_count, a.id AS a_id, a.name AS a_name, a.email AS a_email, "
        "a.email_confidence AS a_conf, a.phone AS a_phone, a.is_office_line AS a_office "
        "FROM properties p JOIN scores s ON s.property_id=p.id JOIN valuations v ON v.property_id=p.id JOIN agents a ON a.id=p.agent_id "
        "WHERE v.rating IN ('HOT','WARM') AND p.lead_status NOT IN ('CLOSED','DNC','EXCLUDED','HOT_LEAD','NEEDS_CALL','NEEDS_REVIEW','DISCUSSING','FLEXIBLE','RESPONDED') "
        "ORDER BY CASE v.rating WHEN 'HOT' THEN 0 ELSE 1 END, "
        "CASE v.lane WHEN 'FRESH_DEAL' THEN 0 WHEN 'SITTING' THEN 1 ELSE 2 END, "    # fresh deals expire first; sitting = ideal
        "(p.price_cuts > 0 OR p.back_on_market) DESC, s.score DESC, v.spread_pct ASC").fetchall()
    return [db.row_to_dict(r) for r in rows]


def plan(conn, cfg: Any, run_date: str, now=None) -> dict[str, list[dict[str, Any]]]:
    """Decide, per property, the channel for today's first touch. Returns {'email': [batch...], 'sms': [item...]}."""
    channel_mode = cfg.channel
    o = cfg["outreach"]
    min_conf = CONF_RANK[o.get("email_min_confidence", "medium")]
    email_by_agent: dict[int, dict[str, Any]] = {}
    sms_items: list[dict[str, Any]] = []
    for p in candidate_rows(conn, cfg):
        aid = p["a_id"]
        if db.is_dnc(conn, agent_id=aid, phone=p["a_phone"], email=p["a_email"]):
            continue
        if _already_contacted_property(conn, p["id"]):
            continue
        can_email = bool(p["a_email"]) and CONF_RANK.get(p["a_conf"] or "low", 1) >= min_conf and not _recent_contact(conn, aid, "email", o["agent_cooldown_days"])
        can_sms = bool(p["a_phone"]) and not p["a_office"] and not _recent_contact(conn, aid, "sms", o["agent_cooldown_days"]) and not _sms_blocked(conn, aid, o["max_unanswered_sms"])
        use_email = use_sms = False
        if channel_mode == "BOTH":
            use_email, use_sms = can_email, can_sms
        elif channel_mode == "SMS_FIRST":
            use_sms = can_sms
            use_email = can_email and not can_sms
        else:  # EMAIL_FIRST
            use_email = can_email
            use_sms = can_sms and not can_email
        if use_email:
            b = email_by_agent.setdefault(aid, {"agent": {"id": aid, "name": p["a_name"], "email": p["a_email"], "phone": p["a_phone"]}, "properties": []})
            b["properties"].append(p)
        if use_sms and not any(i["agent"]["id"] == aid for i in sms_items):   # one text per agent per day
            sms_items.append({"agent": {"id": aid, "name": p["a_name"], "phone": p["a_phone"], "email": p["a_email"]}, "property": p})
    email_cap = max(0, o["daily_email_cap"] - _today_count(conn, "email", run_date))
    sms_cap = max(0, o["daily_sms_cap"] - _today_count(conn, "sms", run_date))
    return {"email": list(email_by_agent.values())[:email_cap], "sms": sms_items[:sms_cap]}


def _sms_blocked(conn, agent_id: int, max_unanswered: int) -> bool:
    r = conn.execute("SELECT unanswered_count, state FROM threads WHERE agent_id=? AND channel='sms'", (agent_id,)).fetchone()
    if not r:
        return False
    return r["unanswered_count"] >= max_unanswered or r["state"] in ("STOPPED", "CLOSED", "HOT_LEAD", "ESCALATED")


def ensure_thread(conn, agent_id: int, channel: str, external_id: str | None, property_ids: list[int]) -> int:
    now = iso()
    r = conn.execute("SELECT id, property_ids FROM threads WHERE channel=? AND external_id=?", (channel, external_id)).fetchone() if external_id else None
    if r:
        pids = sorted(set(json.loads(r["property_ids"])) | set(property_ids))
        conn.execute("UPDATE threads SET property_ids=?, updated_at=? WHERE id=?", (json.dumps(pids), now, r["id"]))
        return int(r["id"])
    cur = conn.execute("INSERT INTO threads(agent_id,channel,external_id,property_ids,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                       (agent_id, channel, external_id, json.dumps(sorted(property_ids)), "OPEN", now, now))
    return int(cur.lastrowid)


def log_message(conn, thread_id: int, direction: str, body: str, external_id: str | None = None, classification: str | None = None,
                confidence: float | None = None, action: str | None = None) -> int:
    cur = conn.execute("INSERT OR IGNORE INTO messages(thread_id,direction,body,external_id,classification,confidence,action,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (thread_id, direction, body, external_id, classification, confidence, action, iso()))
    col = "last_outbound_at" if direction == "out" else "last_inbound_at"
    if direction == "out":
        conn.execute(f"UPDATE threads SET {col}=?, unanswered_count=unanswered_count+1, updated_at=? WHERE id=?", (iso(), iso(), thread_id))
    else:
        conn.execute(f"UPDATE threads SET {col}=?, unanswered_count=0, updated_at=? WHERE id=?", (iso(), iso(), thread_id))
    return int(cur.lastrowid)


def run_outreach(conn, cfg: Any, llm: LLM | None = None, gmail=None, twilio=None, now=None, drafts_dir: Path | None = None,
                 asana=None) -> dict[str, Any]:
    run_date = today_et()
    mode = cfg.mode
    run_id = db.start_run(conn, "outreach", run_date)
    counts = {"mode": mode, "channel": cfg.channel, "email_drafted": 0, "email_sent": 0, "sms_drafted": 0, "sms_sent": 0,
              "sms_skipped_window": 0, "errors": 0}
    todo = plan(conn, cfg, run_date, now)
    drafts_dir = drafts_dir or (cfg.path("reports") / "drafts" / run_date)
    o = cfg["outreach"]
    send_first_touch = mode in ("AUTO_SEND_FIRST_TOUCH", "FULL_AUTO")

    # --- email -----------------------------------------------------------------------------
    for batch in todo["email"]:
        agent, props = batch["agent"], batch["properties"]
        pids = [p["id"] for p in props]
        try:
            d = compose_email(llm, cfg, agent, props, seed_for(agent["id"], pids, run_date))
        except Exception as e:  # noqa: BLE001
            counts["errors"] += 1
            log.exception("compose email failed for agent %s: %s", agent["id"], e)
            continue
        status, ids, err = "draft", {}, None
        if asana is not None and o.get("approval") == "asana":
            # Rodrigo approves each email on Asana; approvals.send_approved sends it later in the day
            with db.tx(conn):
                cur = conn.execute("INSERT INTO outreach(agent_id,property_ids,channel,kind,status,subject,body,variant,to_address,run_date,created_at,analysis) "
                                   "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (agent["id"], json.dumps(pids), "email", "first_touch", "pending_approval",
                                                                       d["subject"], d["body"], d["variant"], agent["email"], run_date, iso(),
                                                                       json.dumps(d.get("analysis") or {})))
            try:
                from ..approvals import post_card
                post_card(conn, cfg, asana, agent, props, d, cur.lastrowid)
                counts["email_cards"] = counts.get("email_cards", 0) + 1
            except Exception as e:  # noqa: BLE001
                counts["errors"] += 1
                conn.execute("UPDATE outreach SET status='failed', error=? WHERE id=?", (f"asana: {e}"[:400], cur.lastrowid))
                log.exception("asana card failed for agent %s: %s", agent["id"], e)
            continue
        try:
            if send_first_touch and gmail is not None:
                ids = gmail.send(agent["email"], d["subject"], d["body"])
                status = "sent"
            elif gmail is not None:
                ids = gmail.create_draft(agent["email"], d["subject"], d["body"], label=o["gmail_review_label"])
            else:
                _write_local_draft(drafts_dir, f"email_{agent['id']}", f"To: {agent['email']}\nSubject: {d['subject']}\n\n{d['body']}")
                if send_first_touch:
                    status, err = "failed", "Gmail client unavailable; nothing sent"
        except Exception as e:  # noqa: BLE001
            status, err = "failed", str(e)[:400]
            counts["errors"] += 1
        with db.tx(conn):
            cur = conn.execute("INSERT INTO outreach(agent_id,property_ids,channel,kind,status,subject,body,variant,to_address,gmail_draft_id,gmail_thread_id,gmail_message_id,error,run_date,created_at,sent_at) "
                               "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (agent["id"], json.dumps(pids), "email", "first_touch", status, d["subject"], d["body"], d["variant"], agent["email"],
                                ids.get("draft_id"), ids.get("thread_id"), ids.get("message_id"), err, run_date, iso(), iso() if status == "sent" else None))
            if status == "sent":
                tid = ensure_thread(conn, agent["id"], "email", ids.get("thread_id") or f"local-{cur.lastrowid}", pids)
                log_message(conn, tid, "out", d["body"], external_id=ids.get("message_id"))
        counts["email_sent" if status == "sent" else "email_drafted"] += 1 if status != "failed" else 0

    # --- sms -------------------------------------------------------------------------------
    w = o["sms_window"]
    in_window = in_send_window(now, w["start_hour"], w["end_hour"], w.get("weekdays_only", True), w.get("timezone", "America/New_York"))
    for item in todo["sms"]:
        agent, p = item["agent"], item["property"]
        d = compose_sms(cfg, agent, p, seed_for(agent["id"], [p["id"]], run_date))
        if validate_sms(d["body"]):
            counts["errors"] += 1
            continue
        status, sid, err = "draft", None, None
        if send_first_touch:
            if not in_window:
                counts["sms_skipped_window"] += 1
                status = "queued"            # picked up by the next run inside the window
            elif twilio is None:
                status, err = "failed", "Twilio client unavailable"
                counts["errors"] += 1
            else:
                try:
                    sid = twilio.send(agent["phone"], d["body"])["sid"]
                    status = "sent"
                except Exception as e:  # noqa: BLE001
                    status, err = "failed", str(e)[:400]
                    counts["errors"] += 1
        else:
            _write_local_draft(drafts_dir, f"sms_{agent['id']}", f"To: {agent['phone']}\n\n{d['body']}")
        with db.tx(conn):
            conn.execute("INSERT INTO outreach(agent_id,property_ids,channel,kind,status,body,variant,to_address,twilio_sid,error,run_date,created_at,sent_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (agent["id"], json.dumps([p["id"]]), "sms", "first_touch", status, d["body"], d["variant"], agent["phone"], sid, err,
                          run_date, iso(), iso() if status == "sent" else None))
            if status == "sent":
                tid = ensure_thread(conn, agent["id"], "sms", agent["phone"], [p["id"]])
                log_message(conn, tid, "out", d["body"], external_id=sid)
        if status == "sent":
            counts["sms_sent"] += 1
        elif status in ("draft", "queued"):
            counts["sms_drafted"] += 1
    db.finish_run(conn, run_id, "ok" if not counts["errors"] else "partial", counts)
    log.info("outreach: %s", counts)
    return counts


def flush_queued_sms(conn, cfg: Any, twilio, now=None) -> int:
    """Send texts that were queued outside the window (called by check-replies every 15 min)."""
    w = cfg["outreach"]["sms_window"]
    if not in_send_window(now, w["start_hour"], w["end_hour"], w.get("weekdays_only", True), w.get("timezone", "America/New_York")) or twilio is None:
        return 0
    if cfg.mode not in ("AUTO_SEND_FIRST_TOUCH", "FULL_AUTO"):
        return 0
    n = 0
    for r in conn.execute("SELECT * FROM outreach WHERE channel='sms' AND status='queued' ORDER BY id").fetchall():
        if db.is_dnc(conn, agent_id=r["agent_id"], phone=r["to_address"]):
            conn.execute("UPDATE outreach SET status='cancelled', error='dnc' WHERE id=?", (r["id"],))
            continue
        try:
            sid = twilio.send(r["to_address"], r["body"])["sid"]
        except Exception as e:  # noqa: BLE001
            conn.execute("UPDATE outreach SET status='failed', error=? WHERE id=?", (str(e)[:400], r["id"]))
            continue
        with db.tx(conn):
            conn.execute("UPDATE outreach SET status='sent', twilio_sid=?, sent_at=? WHERE id=?", (sid, iso(), r["id"]))
            tid = ensure_thread(conn, r["agent_id"], "sms", r["to_address"], json.loads(r["property_ids"]))
            log_message(conn, tid, "out", r["body"], external_id=sid)
        n += 1
    return n


def _write_local_draft(d: Path, name: str, text: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.txt").write_text(text)
