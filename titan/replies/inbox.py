"""Inbound handling shared by the Gmail poller and the Twilio webhook, plus the 15-minute check-replies job."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from .. import db
from ..llm import LLM
from ..outreach.send import ensure_thread, flush_queued_sms, log_message
from ..util import business_days_since, iso, log, now_utc, parse_iso, today_et
from .classifier import Classification, classify
from .responder import Action, decide


def thread_context(conn, thread: dict[str, Any], text: str = "") -> dict[str, Any]:
    pids = json.loads(thread["property_ids"] or "[]")
    agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (thread["agent_id"],)).fetchone()) or {}
    props = [db.row_to_dict(r) for r in conn.execute(
        f"SELECT p.*, v.mao, v.offer_price, v.list_price AS v_list FROM properties p LEFT JOIN valuations v ON v.property_id=p.id WHERE p.id IN ({','.join('?' * len(pids)) or 'NULL'})", pids).fetchall()]
    prop = props[0] if props else {}
    if len(props) > 1 and text:   # pick the property the agent is talking about
        for p in props:
            num = (p.get("street") or "").split(" ")[0]
            if num and re.search(rf"\b{re.escape(num)}\b", text):
                prop = p
                break
    msgs = conn.execute("SELECT direction, body, action FROM messages WHERE thread_id=? ORDER BY id", (thread["id"],)).fetchall()
    history_in = " ".join([m["body"] or "" for m in msgs if m["direction"] == "in"] + [text or ""])
    outs = [m for m in msgs if m["direction"] == "out" and (m["action"] or "").startswith("reply:")]
    drafted = conn.execute("SELECT body FROM outreach WHERE agent_id=? AND kind LIKE 'reply:%' AND property_ids=?",
                           (thread["agent_id"], thread["property_ids"])).fetchall()
    history_out = " ".join([m["body"] or "" for m in outs] + [d["body"] or "" for d in drafted])
    return {"agent": agent, "agent_name": agent.get("name"), "property": prop, "properties": props, "street": prop.get("street"),
            "city": prop.get("city"), "county": prop.get("county"), "list_price": prop.get("list_price"), "mao": prop.get("mao"),
            "offer_price": prop.get("offer_price"), "history_in": history_in, "history_out": history_out,
            "auto_replies": max(len(outs), len(drafted))}


def _send_or_draft(conn, cfg: Any, channel: str, thread: dict[str, Any], ctx: dict[str, Any], body: str, subject: str | None,
                   gmail, twilio, kind: str, in_reply_to: str | None = None) -> tuple[str, dict[str, Any]]:
    """FULL_AUTO sends; other modes draft (Gmail Drafts under Titan/Review; SMS stored as 'draft')."""
    mode = cfg.mode
    agent = ctx["agent"]
    status, ids, err = "draft", {}, None
    try:
        if channel == "email":
            to = agent.get("email")
            subject = subject or f"Re: {ctx.get('street') or 'your listing'}"
            if mode == "FULL_AUTO" and gmail is not None:
                ids = gmail.send(to, subject, body, thread_id=thread.get("external_id"), in_reply_to=in_reply_to)
                status = "sent"
            elif gmail is not None:
                ids = gmail.create_draft(to, subject, body, label=cfg["outreach"]["gmail_review_label"], thread_id=thread.get("external_id"), in_reply_to=in_reply_to)
            elif mode == "FULL_AUTO":
                status, err = "failed", "Gmail unavailable"
        else:
            to = agent.get("phone")
            if mode == "FULL_AUTO" and twilio is not None:
                ids = {"twilio_sid": twilio.send(to, body)["sid"]}
                status = "sent"
            elif mode == "FULL_AUTO":
                status, err = "failed", "Twilio unavailable"
    except Exception as e:  # noqa: BLE001
        status, err = "failed", str(e)[:400]
    conn.execute("INSERT INTO outreach(agent_id,property_ids,channel,kind,status,subject,body,to_address,gmail_draft_id,gmail_thread_id,gmail_message_id,twilio_sid,error,run_date,created_at,sent_at) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (agent.get("id"), thread["property_ids"], channel, kind, status, subject, body, to, ids.get("draft_id"), ids.get("thread_id"),
                  ids.get("message_id"), ids.get("twilio_sid"), err, today_et(), iso(), iso() if status == "sent" else None))
    if status == "sent":
        log_message(conn, thread["id"], "out", body, external_id=ids.get("message_id") or ids.get("twilio_sid"), action=kind)
    return status, ids


def handle_inbound(conn, cfg: Any, thread: dict[str, Any], text: str, external_id: str | None, channel: str,
                   llm: LLM | None = None, gmail=None, twilio=None, now: datetime | None = None, in_reply_to: str | None = None) -> dict[str, Any]:
    """Classify → decide → act. Idempotent on external_id."""
    if external_id and conn.execute("SELECT 1 FROM messages WHERE external_id=?", (external_id,)).fetchone():
        return {"skipped": "duplicate"}
    thread = db.row_to_dict(conn.execute("SELECT * FROM threads WHERE id=?", (thread["id"],)).fetchone())
    ctx = thread_context(conn, thread, text)
    cls = classify(text, ctx.get("list_price"), llm)
    agent = ctx["agent"]

    if cls.label == "BOUNCE":
        # The email never arrived. Mark the address so the planner stops using it, log it, and never treat it as a reply.
        with db.tx(conn):
            log_message(conn, thread["id"], "in", text[:2000], external_id=external_id, classification="BOUNCE", confidence=cls.confidence)
            conn.execute("UPDATE threads SET last_class='BOUNCE', state='BOUNCED', updated_at=? WHERE id=?", (iso(), thread["id"]))
            if agent.get("id"):
                conn.execute("UPDATE agents SET email_confidence='bounced', updated_at=? WHERE id=?", (iso(), agent["id"]))
            for pid in json.loads(thread["property_ids"] or "[]"):
                db.set_lead_status(conn, pid, "NEEDS_CALL", "email bounced (bad or full mailbox); reach the agent by phone")
        return {"classification": "BOUNCE", "action": "email bounced: address marked bad, lead set to NEEDS_CALL", "state": "BOUNCED"}

    # Agent gave us an email in a text → store it (high confidence: they told us)
    if channel == "sms" and cls.email and agent.get("id"):
        conn.execute("UPDATE agents SET email=?, email_confidence='high', email_source='sms_reply', updated_at=? WHERE id=?", (cls.email, iso(), agent["id"]))
        agent["email"], agent["email_confidence"] = cls.email, "high"

    with db.tx(conn):
        log_message(conn, thread["id"], "in", text, external_id=external_id, classification=cls.label, confidence=cls.confidence)
        conn.execute("UPDATE threads SET last_class=?, updated_at=? WHERE id=?", (cls.label, iso(), thread["id"]))

    if cls.label != "STOP" and thread.get("state") in ("HOT_LEAD", "ESCALATED", "CLOSED", "STOPPED"):
        # Rodrigo/Charles own the conversation now; refresh the review so the new message is in front of them
        if thread.get("state") in ("HOT_LEAD", "ESCALATED"):
            from ..review import build as build_review
            build_review(conn, cfg, thread["id"], (ctx.get("property") or {}).get("id"), "new message on a lead under review")
        return {"classification": cls.label, "action": "logged only (auto-reply off)", "state": thread["state"]}

    action = decide(cls, ctx, cfg, channel, thread, text)
    return apply_action(conn, cfg, thread, ctx, cls, action, channel, gmail, twilio, in_reply_to)


def apply_action(conn, cfg: Any, thread: dict[str, Any], ctx: dict[str, Any], cls: Classification, action: Action, channel: str,
                 gmail, twilio, in_reply_to: str | None = None) -> dict[str, Any]:
    agent = ctx["agent"]
    with db.tx(conn):
        if action.dnc:
            db.add_dnc(conn, agent.get("id"), agent.get("phone"), agent.get("email"), f"{cls.label} via {channel}")
            conn.execute("UPDATE outreach SET status='cancelled', error='dnc' WHERE agent_id=? AND status IN ('queued','draft')", (agent.get("id"),))
        conn.execute("UPDATE threads SET state=?, bridge_sent=CASE WHEN ? THEN 1 ELSE bridge_sent END, updated_at=? WHERE id=?",
                     (action.thread_state, 1 if action.set_bridge_sent else 0, iso(), thread["id"]))
        if action.lead_status:
            for pid in json.loads(thread["property_ids"] or "[]"):
                db.set_lead_status(conn, pid, action.lead_status, action.note)
    result = {"classification": cls.label, "number": cls.number, "action": action.note, "state": action.thread_state, "reply": None}
    if action.signal:
        result["signal"] = action.signal
    if action.review:
        from ..review import build as build_review
        rv = build_review(conn, cfg, thread["id"], (ctx.get("property") or {}).get("id"), action.note)
        result["review"] = rv["path"]
    if action.reply:
        status, ids = _send_or_draft(conn, cfg, channel, thread, ctx, action.reply, None, gmail, twilio, kind=f"reply:{cls.label}", in_reply_to=in_reply_to)
        result["reply"] = action.reply
        result["reply_status"] = status
    return result


# --- awaiting an agent's email address (older SMS threads) ----------------------------------------

def check_handoff_timeouts(conn, cfg: Any, now: datetime | None = None) -> int:
    wait = cfg["outreach"].get("handoff_email_wait_business_days", 2)
    n = 0
    for t in conn.execute("SELECT * FROM threads WHERE awaiting_email=1").fetchall():
        since = parse_iso(t["awaiting_email_since"])
        if since and business_days_since(since, now or now_utc()) >= wait:
            with db.tx(conn):
                conn.execute("UPDATE threads SET awaiting_email=0, state='HOT_LEAD', updated_at=? WHERE id=?", (iso(), t["id"]))
                for pid in json.loads(t["property_ids"] or "[]"):
                    db.set_lead_status(conn, pid, "NEEDS_CALL", "agent interested by text but never gave an email; call directly")
            n += 1
    return n


# --- the 15-minute job -----------------------------------------------------------------------------

def check_replies(conn, cfg: Any, gmail=None, llm: LLM | None = None, twilio=None, now: datetime | None = None) -> dict[str, Any]:
    run_id = db.start_run(conn, "check_replies", today_et())
    counts = {"threads_checked": 0, "new_replies": 0, "errors": 0, "sms_flushed": 0, "handoff_timeouts": 0}
    sender = (cfg.secret("GMAIL_SENDER") or "").lower()
    if gmail is not None:
        rows = conn.execute("SELECT * FROM threads WHERE channel='email' AND external_id IS NOT NULL AND external_id NOT LIKE 'local-%' "
                            "AND state NOT IN ('CLOSED','STOPPED')").fetchall()
        for t in rows:
            counts["threads_checked"] += 1
            try:
                msgs = gmail.thread_messages(t["external_id"])
            except Exception as e:  # noqa: BLE001
                counts["errors"] += 1
                log.error("gmail thread %s: %s", t["external_id"], e)
                continue
            for m in sorted(msgs, key=lambda x: x.get("internal_ts", 0)):
                if sender and sender in (m.get("from") or "").lower():
                    continue
                if "DRAFT" in (m.get("label_ids") or []):
                    continue
                if conn.execute("SELECT 1 FROM messages WHERE external_id=?", (m["id"],)).fetchone():
                    continue
                try:
                    handle_inbound(conn, cfg, db.row_to_dict(t), m.get("body", ""), m["id"], "email", llm, gmail, twilio, now,
                                   in_reply_to=m.get("message_id_header"))
                    counts["new_replies"] += 1
                except Exception as e:  # noqa: BLE001
                    counts["errors"] += 1
                    log.exception("handle_inbound failed: %s", e)
    counts["sms_flushed"] = flush_queued_sms(conn, cfg, twilio, now)
    counts["handoff_timeouts"] = check_handoff_timeouts(conn, cfg, now)
    db.finish_run(conn, run_id, "ok" if not counts["errors"] else "partial", counts)
    log.info("check_replies: %s", counts)
    return counts


def inbound_sms(conn, cfg: Any, from_phone: str, body: str, sid: str | None, llm=None, gmail=None, twilio=None, now=None) -> dict[str, Any]:
    """Entry point for the Twilio webhook."""
    t = conn.execute("SELECT * FROM threads WHERE channel='sms' AND external_id=?", (from_phone,)).fetchone()
    if t is None:
        a = conn.execute("SELECT id FROM agents WHERE phone=? ORDER BY updated_at DESC LIMIT 1", (from_phone,)).fetchone()
        if a is None:
            # unknown number: honor STOP, otherwise record and ignore
            if re.search(r"\b(stop|unsubscribe)\b", body or "", re.I):
                db.add_dnc(conn, None, from_phone, None, "STOP from unknown number")
            return {"skipped": "unknown sender"}
        pids = [r["id"] for r in conn.execute("SELECT id FROM properties WHERE agent_id=?", (a["id"],)).fetchall()]
        tid = ensure_thread(conn, a["id"], "sms", from_phone, pids)
        t = conn.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()
    return handle_inbound(conn, cfg, db.row_to_dict(t), body or "", sid, "sms", llm, gmail, twilio, now)
