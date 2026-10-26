"""CONVERSATIONS MIRROR (Rodrigo, journal 2026-10-25: "every inbound reply as a card comment with the engine's answer").

Every reply an agent sends (email or text) is posted as a comment on that agent's card on the Asana approvals board,
together with how the engine read it and the answer it drafted (drafts live in Charles's Gmail under Titan/Review and
are never sent on their own). So Rodrigo, and a cloud session without access to Charles's inbox, can read and answer
conversations from Asana alone. Runs after every check-replies and approvals tick; idempotent (messages.mirrored_at).
"""
from __future__ import annotations

import json
from typing import Any

from . import db
from .util import iso, log, money, parse_iso, pretty_phone

PREFIX = "📩 Reply from"
NO_CARD = "no_card"


def card_for_thread(conn, thread: dict[str, Any]) -> str | None:
    """The agent's most recent card, preferring one about the same house."""
    rows = conn.execute("SELECT asana_task_id, property_ids FROM outreach WHERE agent_id=? AND asana_task_id IS NOT NULL "
                        "ORDER BY COALESCE(sent_at, created_at) DESC, id DESC", (thread["agent_id"],)).fetchall()
    want = set(json.loads(thread["property_ids"] or "[]"))
    for r in rows:
        try:
            if want & set(json.loads(r["property_ids"] or "[]")):
                return r["asana_task_id"]
        except (TypeError, ValueError):
            continue
    return rows[0]["asana_task_id"] if rows else None


def engine_answer(conn, thread: dict[str, Any], msg: dict[str, Any]) -> dict[str, Any] | None:
    """The reply the engine wrote after this inbound message: a sent message, or a draft waiting in Gmail."""
    sent = conn.execute("SELECT body, created_at FROM messages WHERE thread_id=? AND direction='out' AND id>? AND action LIKE 'reply:%' ORDER BY id LIMIT 1",
                        (thread["id"], msg["id"])).fetchone()
    if sent:
        return {"status": "sent", "body": sent["body"]}
    draft = conn.execute("SELECT body, status, error FROM outreach WHERE agent_id=? AND kind LIKE 'reply:%' AND created_at>=? ORDER BY id DESC LIMIT 1",
                         (thread["agent_id"], msg["created_at"])).fetchone()
    if draft:
        return {"status": draft["status"], "body": draft["body"], "error": draft["error"]}
    return None


def _when(s: str | None) -> str:
    dt = parse_iso(s)
    if not dt:
        return ""
    from zoneinfo import ZoneInfo
    return dt.astimezone(ZoneInfo("America/New_York")).strftime("%a %b %d, %I:%M %p ET")


def mirror_text(conn, thread: dict[str, Any], msg: dict[str, Any], agent: dict[str, Any]) -> str:
    pids = json.loads(thread["property_ids"] or "[]")
    props = [db.row_to_dict(r) for r in conn.execute(
        f"SELECT p.street, p.city, p.list_price, p.lead_status, v.mao FROM properties p LEFT JOIN valuations v ON v.property_id=p.id "
        f"WHERE p.id IN ({','.join('?' * len(pids)) or 'NULL'})", pids).fetchall()]
    who = agent.get("name") or agent.get("email") or pretty_phone(agent.get("phone")) or "the agent"
    via = "text" if thread.get("channel") == "sms" else "email"
    body = (msg.get("body") or "").strip()
    if len(body) > 1500:
        body = body[:1500].rstrip() + " […]"
    conf = msg.get("confidence")
    read = f"{msg.get('classification') or 'unclassified'}" + (f" ({conf:.0%} sure)" if isinstance(conf, (int, float)) else "")
    phone = pretty_phone(agent.get("phone")) if agent.get("phone") else ""
    lines = [f"{PREFIX} {who} ({via}, {_when(msg.get('created_at'))}):" + (f"  ·  call {phone}" if phone else ""), "", body or "(empty message)", "",
             f"Engine read: {read}. Conversation state: {thread.get('state') or 'OPEN'}."]
    for p in props:
        lines.append(f"House: {p['street']}, {p['city']} · list {money(p['list_price'])} · lead {p.get('lead_status') or 'NONE'}"
                     + (f" · internal screening MAO {money(p['mao'])} (never quoted to the agent)" if p.get("mao") else ""))
    ans = engine_answer(conn, thread, msg)
    if ans and ans.get("status") == "sent":
        lines += ["", "Charles answered (sent):", ans["body"] or ""]
    elif ans and ans.get("body"):
        lines += ["", "Engine's draft answer (NOT sent; it sits in Charles's Gmail drafts under Titan/Review):", ans["body"]]
    else:
        lines += ["", "Nothing has been sent back yet."]
    lines += ["", "Comment here what Charles should answer, or tell Claude."]
    return "\n".join(lines)


def mirror_replies(conn, cfg: Any, asana, limit: int = 50) -> dict[str, int]:
    """Post every not-yet-mirrored inbound message on its agent's card. Never raises into the caller."""
    counts = {"posted": 0, "no_card": 0, "failed": 0}
    try:
        pending = conn.execute("SELECT m.*, t.agent_id, t.property_ids, t.state, t.channel FROM messages m JOIN threads t ON t.id=m.thread_id "
                               "WHERE m.direction='in' AND m.mirrored_at IS NULL ORDER BY m.id LIMIT ?", (limit,)).fetchall()
    except Exception as e:  # noqa: BLE001
        log.warning("conversations: cannot read messages: %s", e)
        return counts
    for row in pending:
        msg = db.row_to_dict(row)
        thread = {"id": msg["thread_id"], "agent_id": msg["agent_id"], "property_ids": msg["property_ids"], "state": msg["state"], "channel": msg["channel"]}
        try:
            gid = card_for_thread(conn, thread)
            if not gid:
                conn.execute("UPDATE messages SET mirrored_at=? WHERE id=?", (NO_CARD, msg["id"]))
                counts["no_card"] += 1
                continue
            agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (thread["agent_id"],)).fetchone()) or {}
            asana.comment(gid, mirror_text(conn, thread, msg, agent))
            conn.execute("UPDATE messages SET mirrored_at=? WHERE id=?", (iso(), msg["id"]))
            counts["posted"] += 1
        except Exception as e:  # noqa: BLE001 — a dead card or Asana hiccup must not stop the tick; retried next time
            log.warning("conversations: could not mirror message %s: %s", msg["id"], e)
            counts["failed"] += 1
    return counts
