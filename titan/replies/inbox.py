"""Inbound handling shared by the Gmail poller and the Twilio webhook, plus the 15-minute check-replies job."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from .. import db
from ..llm import LLM
from ..outreach.compose import compose_offer_email
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
    return {"agent": agent, "agent_name": agent.get("name"), "property": prop, "properties": props, "street": prop.get("street"),
            "city": prop.get("city"), "county": prop.get("county"), "list_price": prop.get("list_price"), "mao": prop.get("mao"),
            "offer_price": prop.get("offer_price")}


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

    # Agent gave us an email in a text → store it (high confidence: they told us)
    if channel == "sms" and cls.email and agent.get("id"):
        conn.execute("UPDATE agents SET email=?, email_confidence='high', email_source='sms_reply', updated_at=? WHERE id=?", (cls.email, iso(), agent["id"]))
        agent["email"], agent["email_confidence"] = cls.email, "high"

    with db.tx(conn):
        log_message(conn, thread["id"], "in", text, external_id=external_id, classification=cls.label, confidence=cls.confidence)
        conn.execute("UPDATE threads SET last_class=?, updated_at=? WHERE id=?", (cls.label, iso(), thread["id"]))

    # Awaiting an email address for the handoff: finish it as soon as we have one
    if channel == "sms" and thread.get("awaiting_email") and agent.get("email") and cls.label != "STOP":
        conn.execute("UPDATE threads SET awaiting_email=0 WHERE id=?", (thread["id"],))
        return run_handoff(conn, cfg, thread, ctx, gmail, twilio, cls)

    if cls.label != "STOP" and thread.get("state") in ("HOT_LEAD", "ESCALATED", "CLOSED", "STOPPED"):
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
    if action.handoff:
        return {**result, **run_handoff(conn, cfg, thread, ctx, gmail, twilio, cls)}
    if action.reply:
        status, ids = _send_or_draft(conn, cfg, channel, thread, ctx, action.reply, None, gmail, twilio, kind=f"reply:{cls.label}", in_reply_to=in_reply_to)
        result["reply"] = action.reply
        result["reply_status"] = status
    return result


# --- text-to-email handoff (7a) -------------------------------------------------------------------

def run_handoff(conn, cfg: Any, thread: dict[str, Any], ctx: dict[str, Any], gmail, twilio, cls: Classification) -> dict[str, Any]:
    agent, prop = ctx["agent"], ctx["property"]
    mode = cfg.mode
    email_ok = agent.get("email") and (agent.get("email_confidence") == "high")
    if not email_ok:
        ask = "Great - what's the best email to send the written offer to?"
        status, _ = _send_or_draft(conn, cfg, "sms", thread, ctx, ask, None, gmail, twilio, kind="handoff:ask_email")
        conn.execute("UPDATE threads SET awaiting_email=1, awaiting_email_since=COALESCE(awaiting_email_since, ?), state='HOT_LEAD', updated_at=? WHERE id=?",
                     (iso(), iso(), thread["id"]))
        for pid in json.loads(thread["property_ids"] or "[]"):
            db.set_lead_status(conn, pid, "HOT_LEAD", "positive text; asked agent for email")
        return {"handoff": "asked_for_email", "sms_status": status}
    offer_price = ctx.get("offer_price") or (int(ctx["mao"] * (1 - cfg["formula"].get("offer_below_mao_pct", 0) / 100)) if ctx.get("mao") else None)
    if not offer_price:
        for pid in json.loads(thread["property_ids"] or "[]"):
            db.set_lead_status(conn, pid, "NEEDS_CALL", "positive text but no valuation on file")
        return {"handoff": "no_valuation"}
    d = compose_offer_email(cfg, agent, prop, offer_price)
    # the written offer goes out automatically in AUTO modes; in DRAFT_FOR_APPROVAL it lands in Titan/Review
    email_thread_id = ensure_thread(conn, agent["id"], "email", None, json.loads(thread["property_ids"]))
    ethread = db.row_to_dict(conn.execute("SELECT * FROM threads WHERE id=?", (email_thread_id,)).fetchone())
    send_now = mode in ("AUTO_SEND_FIRST_TOUCH", "FULL_AUTO") and gmail is not None
    status, ids = "draft", {}
    try:
        if send_now:
            ids = gmail.send(agent["email"], d["subject"], d["body"])
            status = "sent"
        elif gmail is not None:
            ids = gmail.create_draft(agent["email"], d["subject"], d["body"], label=cfg["outreach"]["gmail_review_label"])
    except Exception as e:  # noqa: BLE001
        status = "failed"
        log.error("handoff email failed: %s", e)
    conn.execute("INSERT INTO outreach(agent_id,property_ids,channel,kind,status,subject,body,to_address,gmail_draft_id,gmail_thread_id,gmail_message_id,run_date,created_at,sent_at) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (agent["id"], thread["property_ids"], "email", "handoff:offer", status, d["subject"], d["body"], agent["email"], ids.get("draft_id"),
                  ids.get("thread_id"), ids.get("message_id"), today_et(), iso(), iso() if status == "sent" else None))
    if ids.get("thread_id"):
        conn.execute("UPDATE threads SET external_id=? WHERE id=?", (ids["thread_id"], email_thread_id))
    conn.execute("UPDATE threads SET linked_thread_id=?, state='HOT_LEAD', awaiting_email=0, updated_at=? WHERE id=?", (email_thread_id, iso(), thread["id"]))
    conn.execute("UPDATE threads SET linked_thread_id=?, state='HOT_LEAD', updated_at=? WHERE id=?", (thread["id"], iso(), email_thread_id))
    if status == "sent":
        log_message(conn, email_thread_id, "out", d["body"], external_id=ids.get("message_id"), action="handoff:offer")
        confirm = f"Just sent the written offer to {agent['email']}, let me know what the seller says. - {cfg['operator']['sender_name'].split()[0]}"
        sms_status, _ = _send_or_draft(conn, cfg, "sms", thread, ctx, confirm, None, gmail, twilio, kind="handoff:confirm")
    else:
        sms_status = "not_sent (offer email is a draft)"
    for pid in json.loads(thread["property_ids"] or "[]"):
        db.set_lead_status(conn, pid, "HOT_LEAD", f"{cls.label} by text; written offer {status} to {agent['email']}")
    return {"handoff": "offer_email", "offer_price": offer_price, "email_status": status, "sms_status": sms_status,
            "email_thread_id": email_thread_id, "gmail_thread_id": ids.get("thread_id")}


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
