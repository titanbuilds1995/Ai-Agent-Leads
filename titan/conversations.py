"""CONVERSATIONS MIRROR (Rodrigo, journal 2026-10-25: "every inbound reply as a card comment with the engine's answer").

Every reply an agent sends (email or text) is posted as a comment on that agent's card on the Asana approvals board,
together with how the engine read it and the answer it drafted (drafts live in Charles's Gmail under Titan/Review and
are never sent on their own). So Rodrigo, and a cloud session without access to Charles's inbox, can read and answer
conversations from Asana alone. Runs after every check-replies and approvals tick; idempotent (messages.mirrored_at).
"""
from __future__ import annotations

import json
import re
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


# ---------- the lead board (Rodrigo, 2026-10-26: "see exactly who answered and where we stand with each lead, and a whole
# tab with the unanswered") ----------
ANSWERED = "Answered"
NO_ANSWER = "Sent · no answer yet"
STAND_START, STAND_END = "-----WHERE WE STAND-----", "-----END WHERE WE STAND-----"
NOT_AN_ANSWER = ("BOUNCE", "OUT_OF_OFFICE")
# Rodrigo, 2026-10-27: a lead whose next move is his goes to "Needs Me"; "Answered" = we replied, the ball is with the agent
NEEDS_ME_STATUS = ("NEEDS YOU", "OUR TURN", "ANSWER DRAFTED", "OFFER DRAFTED")
PLAIN = {"INTERESTED": "talked about the house", "ASK_FOR_OFFER": "asked for our offer", "COUNTER_WITH_NUMBER": "gave a number",
         "QUESTION": "asked a question", "NOT_INTERESTED": "not interested", "SOLD_OR_PENDING": "already under contract or sold",
         "STOP": "asked us to stop"}


def board_sections(cfg: Any, asana) -> tuple[str, str]:
    """(no-answer section, answered section). The old Sent column becomes "Sent · no answer yet"; "Answered" is created
    right after it the first time."""
    a = cfg.get("outreach.asana", {}) or {}
    cached = getattr(asana, "_lead_board", None)
    if cached:
        return cached
    sent = a["sent"]
    secs = {x["gid"]: (x.get("name") or "") for x in asana.sections(a["project"])}
    if secs.get(sent, NO_ANSWER) != NO_ANSWER:
        asana.rename_section(sent, NO_ANSWER)
    answered = a.get("answered") or asana.ensure_section(a["project"], ANSWERED, insert_before=a.get("skip"))
    try:
        asana._lead_board = (sent, answered)
    except Exception:  # noqa: BLE001
        pass
    return sent, answered


def where_we_stand(conn, agent_id: int, pending_reply: bool = False) -> list[str] | None:
    """Plain lines for the top of a card, or None when the agent has not really answered (bounces and auto-replies don't count)."""
    msgs = [db.row_to_dict(r) for r in conn.execute(
        "SELECT m.direction, m.classification, m.created_at, m.body, t.state FROM messages m JOIN threads t ON t.id=m.thread_id "
        "WHERE t.agent_id=? ORDER BY m.created_at, m.id", (agent_id,)).fetchall()]
    real_in = [m for m in msgs if m["direction"] == "in" and (m.get("classification") or "") not in NOT_AN_ANSWER]
    if not real_in:
        return None
    last_in = real_in[-1]
    last = [m for m in msgs if not (m["direction"] == "in" and (m.get("classification") or "") in NOT_AN_ANSWER)][-1]
    cls = last_in.get("classification") or ""
    outs = [m for m in msgs if m["direction"] == "out"]
    if cls in ("NOT_INTERESTED", "SOLD_OR_PENDING", "STOP") or (last.get("state") or "") in ("CLOSED", "STOPPED"):
        turn = f"CLOSED: {PLAIN.get(cls, 'conversation ended')}."
    elif last["direction"] == "out":
        turn = f"WAITING ON THE AGENT since {_when(last['created_at'])}."
    elif cls in ("ASK_FOR_OFFER", "COUNTER_WITH_NUMBER"):
        turn = ("NEEDS YOU: decide a number (comment \"offer N\" on this card) or tell Charles what to ask."
                + (" A holding reply card is drafted." if pending_reply else ""))
    elif pending_reply:
        turn = "ANSWER DRAFTED: Charles's reply card is on the board waiting for you."
    else:
        turn = "OUR TURN: Charles owes an answer."
    snippet = " ".join((last_in.get("body") or "").split())[:180]
    return [f"Status: {turn}",
            f"Agent answered {len(real_in)} time(s); last {_when(last_in['created_at'])}: {PLAIN.get(cls, cls.lower() or 'replied')}.",
            f"Their last words: \"{snippet}{'…' if len(last_in.get('body') or '') > 180 else ''}\"",
            f"We wrote {len(outs)} time(s)" + (f"; last {_when(outs[-1]['created_at'])}." if outs else ".")]


def _with_stand(notes: str, lines: list[str]) -> str:
    import re
    block = f"{STAND_START}\n" + "\n".join(lines) + f"\n{STAND_END}\n\n"
    rest = re.sub(re.escape(STAND_START) + r".*?" + re.escape(STAND_END) + r"\n*", "", notes or "", count=1, flags=re.S)
    return block + rest


def lead_board(conn, cfg: Any, asana) -> dict[str, int]:
    """Lead cards whose agent really answered: "Needs Me" when the next move is Rodrigo's (NEEDS YOU / OUR TURN / ANSWER
    DRAFTED / OFFER DRAFTED), "Answered" when the agent wrote last or the conversation closed, back to "Sent · no answer
    yet" once we replied and wait on them (Rodrigo, 2026-10-27: we answering is not them answering). Keeps a WHERE WE
    STAND block at the top of every such first-email card. Never raises into the caller."""
    from .approvals import needs_me_section, parse_card
    n = {"moved": 0, "updated": 0, "to_needs_me": 0, "to_answered": 0}
    try:
        a = cfg.get("outreach.asana", {}) or {}
        sent, answered = board_sections(cfg, asana)
        nm = needs_me_section(cfg, asana)
        pending = set()
        drafts: dict[int, list[tuple[str, str, dict]]] = {}     # outreach id -> [(where, card name, parsed card)]
        names = {a.get("to_review"): "To Review", a.get("approved"): "Approved (sends next run)"}
        for sec in (a.get("to_review"), a.get("approved"), getattr(asana, "_needs_revision_gid", None) or a.get("needs_revision")):
            for t in (asana.section_tasks(sec) if sec else []):
                pc = parse_card(t.get("notes") or "")
                if pc and pc.get("reply_to"):
                    pending.add(pc["outreach_id"])
                    drafts.setdefault(pc["outreach_id"], []).append((names.get(sec, "Needs Revision"), t.get("name") or "", pc))
        agent_of: dict[int, int | None] = {}

        def agent_id(oid: int) -> int | None:
            if oid not in agent_of:
                r = conn.execute("SELECT agent_id FROM outreach WHERE id=?", (oid,)).fetchone()
                agent_of[oid] = r["agent_id"] if r else None
            return agent_of[oid]

        pending_agents = {agent_id(o) for o in pending}
        lead_cards = ([(t, sent) for t in asana.section_tasks(sent)] + [(t, answered) for t in asana.section_tasks(answered)]
                      + [(t, nm) for t in asana.section_tasks(nm)])
        for t, here in lead_cards:
            pc = parse_card(t.get("notes") or "")
            if not pc or pc.get("reply_to") or not agent_id(pc["outreach_id"]):
                continue
            if here == nm and (conn.execute("SELECT status FROM outreach WHERE id=?", (pc["outreach_id"],)).fetchone() or [""])[0] != "sent":
                continue          # an unsent first email parked with its numbers: not a lead card
            aid = agent_id(pc["outreach_id"])
            lines = where_we_stand(conn, aid, aid in pending_agents)
            if not lines:
                continue
            # Rodrigo, 2026-10-26: the status must move as soon as an offer is drafted, not stay at "decide a number"
            agent_drafts = [d for o in pending if agent_id(o) == aid for d in drafts.get(o, [])]
            offer = next((d for d in agent_drafts if re.search(r"\$\s?\d{2,3},\d{3}", d[2]["body"])), None)
            if offer:
                price = re.search(r"\$\s?(\d{2,3},\d{3})", offer[2]["body"]).group(1)
                lines[0] = (f"Status: OFFER DRAFTED (${price}): waiting for your approval in {offer[0]}. Drag the card "
                            f"\"{offer[1]}\" to Approved to send it." if "Approved" not in offer[0] else
                            f"Status: OFFER APPROVED (${price}): goes out from Charles on the next run.")
            # Rodrigo, 2026-10-26: "in the card I do not see the answer you drafted" — show the drafted reply right here
            for oid in [o for o in pending if agent_id(o) == aid]:
                for where, cname, pc2 in drafts.get(oid, []):
                    lines += ["", f"DRAFTED ANSWER (not sent yet) · card \"{cname}\" in {where}. Drag that card to Approved to send it.",
                              f"Draft subject: {pc2['subject']}", "", pc2["body"]]
            new = _with_stand(t.get("notes") or "", lines)
            if new != (t.get("notes") or ""):
                asana.update_notes(t["gid"], new)
                n["updated"] += 1
            # Rodrigo, 2026-10-27: "Answered" means the AGENT answered and it is on us; once we reply, the lead goes back to
            # "Sent · no answer yet" until the agent writes again. Needs Me = the next move is his.
            if any(lines[0].startswith(f"Status: {k}") for k in NEEDS_ME_STATUS):
                target = nm
            elif lines[0].startswith("Status: WAITING ON THE AGENT"):
                target = sent
            elif lines[0].startswith("Status: OFFER APPROVED"):
                target = here             # sends on the next run; it then reads WAITING ON THE AGENT and goes to Sent
            else:
                target = answered
            if target != here:
                asana.move(t["gid"], target)
                n["moved"] += 1
                n["to_needs_me" if target == nm else "to_sent" if target == sent else "to_answered"] = \
                    n.get("to_needs_me" if target == nm else "to_sent" if target == sent else "to_answered", 0) + 1
    except Exception as e:  # noqa: BLE001
        log.warning("lead board: %s", e)
        n["error"] = str(e)[:200]
    return n
