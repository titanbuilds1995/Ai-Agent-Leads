"""STAGE 3 OF THE TRUST LADDER (Rodrigo, 2026-10-26): Charles writes his own answer to every agent who replied, as a reply card
on the approvals board. Rodrigo edits it like a first email (comment → rewrite, or edit the text, drag to Approved), every decision
is recorded and his comments become lessons, so the writer learns how to answer people from real conversations.

Conversation first still holds: no price, range, percentage or offer language ever. When the agent asks for an offer or gives a
number, the card is a holding answer (one easy question, no number) and the WHERE WE STAND block tells Rodrigo it needs his number.
Nothing sends on its own: the card waits in To Review."""
from __future__ import annotations

import json
import re
from typing import Any

from .. import db
from ..util import first_name, log

ANSWERABLE = ("INTERESTED", "QUESTION", "ASK_FOR_OFFER", "COUNTER_WITH_NUMBER")
REPLY_SCHEMA = {"type": "object", "properties": {"read": {"type": "string"}, "body": {"type": "string"}},
                "required": ["read", "body"], "additionalProperties": False}

REPLY_SYSTEM = """You are {sender}, Junior Acquisitions Agent at {company}. A listing agent answered your email. Write {sender}'s NEXT reply
in the same email thread. Plain text, no subject line.

Rules (Rodrigo's, all hard):
- Answer what they said first, briefly and like a person ("Thanks Lise, that helps."). Use what they told you; never re-ask anything
  they already answered anywhere in the thread.
- Then ONE easy question that moves the deal forward (seller's timeline or flexibility, occupancy, condition of the big-ticket items,
  why they are selling). One question only.
- NO price, number, range, percentage, "offer", discount or dollar figure, ever. If they asked for our offer or named a number, say
  you're putting the numbers together on your side and will come back to them shortly, then ask the one question that most affects
  the number. Never promise a date or an amount.
- Terms you may state if asked: as-is, cash, {terms}. Always keep the inspection period; never offer to waive it.
- NOTHING IS EVER ATTACHED to Charles's emails and Charles never signs anything (Rodrigo, 2026-11-12, Eric Cederman). Never write "attached", "enclosed", "please find", "signed copy", "I've signed", "sent it over": the email is stopped at send if it does. If a signed document has to go, the email does not mention it; Rodrigo sends the file himself.
- Never say we are the buyer or investors ("we buy", "we're investors", "serious buyers", "genuinely interested"). Say "our side",
  "what we look for", "something we'd pursue".
- "I" only for what {sender} did or will do; "we"/"our side" for criteria. Never powerless ("ask my boss"), never overcommitting.
- DIRECT AND MATTER OF FACT (Rodrigo, 2026-10-28): serious, plain, never AI-ish. Never tell them what doesn't scare, bother or worry
  us or what is "fine on our side"; never repeat their own facts back as reassurance ("with the new panel in"); never mention the
  electrical panel; no "curious", "honestly", "potentially", "the bigger question is".
- 25 to 90 words before the signature. No dashes of any kind (no em dash, en dash, " - " or "--"). No emojis, no links.
- PHOTOS (Rodrigo, 2026-10-27): each property carries photos_count. With 4 or more photos the agent knows the condition is visible in the
  listing, so never ask or imply that we need to find out how much work it needs ("how much work does it need", "how deep is the project",
  "mostly cosmetic or something bigger"). Ask about what photos can't show: roof and AC age, plumbing, electrical, permits, occupancy,
  timeline. Only when may_ask_how_much_work is true (fewer than 4 photos) may you ask about the work.
- End with the signature block from the input, unchanged.
- If rodrigo_instruction is present, it is Rodrigo's instruction for THIS reply (what to ask or say, what to change): follow it
  exactly; it may ask several things and overrides the one-question rule. Never add a price or number he did not state.
- If second_reader_pushback is present, a second reader checked your previous draft (previous_draft) against the whole thread and
  found these logic problems. Fix every one of them in this version (drop what they already answered, answer what they actually
  asked, ask the question that moves it forward); keep everything else and every rule above.

=== VOICE PROTOCOL ===
{voice}
=== END VOICE PROTOCOL ===
{learned}
Return JSON: read (one line: what the agent told us and what we still need to learn) and body (the reply, signature included)."""


def validate_reply(body: str, props: list[dict[str, Any]] | None = None) -> list[str]:
    from ..outreach.compose import BUYER_RE, DASH_RE, HTML_RE, NO_NUMBERS_RE, PERSONAL_RE, URL_RE, photo_rule_problems, tone_problems
    problems = []
    text = body.split("\n\nBest")[0]
    if props:
        problems += photo_rule_problems(body, props)      # Rodrigo, 2026-10-27: the photos show the work; don't ask about it
    problems += tone_problems(body)
    words = len(text.split())
    if words < 15 or words > 140:
        problems.append(f"length {words}")
    if re.search(r"\$\s?\d", body) or NO_NUMBERS_RE.search(text) or re.search(r"\b(our|an|my) offer\b|\boffer (of|at)\b", text, re.I):
        problems.append("price or offer language")
    if BUYER_RE.search(body):
        problems.append("says we are the buyer / investors")
    if PERSONAL_RE.search(body):
        problems.append("personal framing of criteria")
    if DASH_RE.search(text):
        problems.append("dash")
    if HTML_RE.search(body) or URL_RE.search(text):
        problems.append("html or link")
    if text.count("?") > 2:
        problems.append("more than one question")
    return problems


def _thread_text(conn, agent_id: int) -> list[dict[str, str]]:
    rows = conn.execute("SELECT m.direction, m.body, m.created_at, m.classification FROM messages m JOIN threads t ON t.id=m.thread_id "
                        "WHERE t.agent_id=? AND COALESCE(m.classification,'') NOT IN ('BOUNCE','OUT_OF_OFFICE') ORDER BY m.created_at, m.id",
                        (agent_id,)).fetchall()
    from .clean import clean_reply
    return [{"from": "agent" if r["direction"] == "in" else "Charles", "when": r["created_at"],
             "text": (clean_reply(r["body"] or "") if r["direction"] == "in" else (r["body"] or ""))[:2500]} for r in rows]


def waiting_threads(conn) -> list[dict[str, Any]]:
    """Agents whose last real message is theirs (we owe an answer) and whose last message got no reply card yet."""
    out = []
    for t in conn.execute("SELECT DISTINCT agent_id FROM threads WHERE agent_id IS NOT NULL").fetchall():
        last = conn.execute("SELECT m.* FROM messages m JOIN threads t ON t.id=m.thread_id WHERE t.agent_id=? "
                            "AND COALESCE(m.classification,'') NOT IN ('BOUNCE','OUT_OF_OFFICE') ORDER BY m.created_at DESC, m.id DESC LIMIT 1",
                            (t["agent_id"],)).fetchone()
        if last and last["direction"] == "in" and (last["classification"] or "") in ANSWERABLE and not last["reply_card"]:
            out.append({"agent_id": t["agent_id"], "message": db.row_to_dict(last)})
    return out


def write_answer(conn, cfg: Any, llm, row: dict[str, Any], agent: dict[str, Any], props: list[dict[str, Any]],
                 cls: str | None = None, instruction: str | None = None, pushback: list[str] | None = None,
                 previous: str | None = None) -> tuple[str | None, list[str]]:
    """Charles's next email to this agent from the whole thread (and Rodrigo's instruction when he gave one).
    (body, problems): body is None when Claude is unreachable or the text failed the checks twice."""
    from ..learning import learned_block
    from ..outreach.compose import format_paragraphs, may_ask_how_much_work, signature, voice_text
    from ..card_chat import lead_notes
    from ..util import inspection_phrase
    if llm is None or not getattr(llm, "available", False):
        return None, ["Claude not available"]
    op, f, terms = cfg["operator"], cfg["formula"], cfg["outreach"]["terms"]
    system = REPLY_SYSTEM.format(sender=op["sender_name"].split()[0], company=op["company"], voice=voice_text(cfg), learned=learned_block(cfg),
                                 terms=f"{f['close_days']}-day close, {inspection_phrase(terms)}, ${f['emd']:,.0f} EMD")
    user = json.dumps({"agent_first_name": first_name(agent.get("name")), "their_last_message_class": cls,
                       "thread": _thread_text(conn, row["agent_id"]),
                       "properties": [{k: p.get(k) for k in ("street", "city", "list_price", "days_on_market", "price_cuts", "beds", "baths", "sqft",
                                                             "year_built", "property_type", "remarks", "photos_count")} for p in props],
                       "signature_block": signature(cfg), "may_ask_how_much_work": may_ask_how_much_work(props),
                       "rodrigo_notes_on_this_lead": lead_notes(conn, row["agent_id"]),
                       **({"rodrigo_instruction": instruction} if instruction else {}),
                       **({"second_reader_pushback": pushback, "previous_draft": previous or ""} if pushback else {})},
                      ensure_ascii=False, default=str)
    body, problems = None, ["no answer from Claude"]
    for _ in range(2):
        out = llm.structured("draft_reply", system, user, REPLY_SCHEMA, max_tokens=900)
        if not out:
            break
        body = format_paragraphs(str(out.get("body", "")).strip())
        problems = validate_reply(body, props)
        if instruction:          # his instruction may ask several questions: that rule is his to break
            problems = [p for p in problems if p != "more than one question"]
        if not problems:
            return body, []
    return None, problems


def reader_case(conn, row: dict[str, Any], agent: dict[str, Any], props: list[dict[str, Any]], cls: str | None = None,
                instruction: str | None = None) -> dict[str, Any]:
    """What the second reader gets for a house lead: the whole conversation, the listing facts, Rodrigo's notes and instruction."""
    from ..card_chat import lead_notes
    case = {"who": f"listing agent {agent.get('name') or ''} ({agent.get('brokerage') or 'brokerage unknown'}), residential listing",
            "house_rules": "The reply must not contain a price, range or offer (Rodrigo decides numbers per house); a holding answer "
                           "with one question is correct when they ask for an offer. With 4+ listing photos we never ask how much work it needs.",
            "their_last_message_class": cls, "thread": _thread_text(conn, row["agent_id"]),
            "properties": [{k: p.get(k) for k in ("street", "city", "list_price", "days_on_market", "price_cuts", "beds", "baths", "sqft",
                                                  "year_built", "property_type", "remarks", "photos_count")} for p in props],
            "rodrigo_notes_on_this_lead": lead_notes(conn, row["agent_id"])}
    if instruction:
        case["rodrigo_instruction"] = instruction
    return case


def write_checked(conn, cfg: Any, llm, row: dict[str, Any], agent: dict[str, Any], props: list[dict[str, Any]],
                  cls: str | None = None, instruction: str | None = None) -> tuple[str | None, list[str], dict[str, Any] | None]:
    """Two agents (Rodrigo, 2026-11-12): Charles writes, the second reader reads the whole thread and the draft and pushes back on
    the logic, Charles rewrites, it is read again. (body, problems, review); review as in `second_read.run` (None = switched off)."""
    from .. import second_read
    body, problems = write_answer(conn, cfg, llm, row, agent, props, cls=cls, instruction=instruction)
    if not body:
        return None, problems, None
    state = {"draft": body}

    def rewrite(pushback: list[str]) -> str | None:
        new, _ = write_answer(conn, cfg, llm, row, agent, props, cls=cls, instruction=instruction, pushback=pushback, previous=state["draft"])
        if new:
            state["draft"] = new
        return new

    body, review = second_read.run(llm, cfg, reader_case(conn, row, agent, props, cls, instruction), body, rewrite)
    return body, [], review


def draft_reply_cards(conn, cfg: Any, asana, llm, limit: int = 5) -> dict[str, Any]:
    """Write Charles's answer for each agent waiting on us and put it ON THE LEAD CARD as its NEXT EMAIL (Needs Me; one card
    per lead, Rodrigo 2026-11-03). Never raises."""
    from ..answers import pending_agents, set_answer
    n = {"drafted": 0, "skipped_pending_card": 0, "failed": 0}
    if llm is None or not getattr(llm, "available", False):
        return {**n, "note": "Claude not available; no answers written this tick"}
    try:
        pending = pending_agents(conn)
        for w in waiting_threads(conn)[:limit]:
            msg = w["message"]
            if w["agent_id"] in pending:          # an answer is already waiting on the lead card
                n["skipped_pending_card"] += 1
                continue
            row = db.row_to_dict(conn.execute("SELECT * FROM outreach WHERE agent_id=? AND kind='first_touch' AND status='sent' "
                                              "ORDER BY sent_at DESC, id DESC LIMIT 1", (w["agent_id"],)).fetchone())
            if not row:
                continue
            agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (w["agent_id"],)).fetchone()) or {}
            pids = json.loads(row["property_ids"] or "[]")
            props = [db.row_to_dict(r) for r in conn.execute(
                f"SELECT * FROM properties WHERE id IN ({','.join('?' * len(pids)) or 'NULL'})", pids).fetchall()]
            body, problems, second = write_checked(conn, cfg, llm, row, agent, props, cls=msg.get("classification"))
            if not body:
                log.warning("answer for agent %s not written: %s", w["agent_id"], problems)
                n["failed"] += 1
                continue
            needs_number = msg.get("classification") in ("ASK_FOR_OFFER", "COUNTER_WITH_NUMBER")
            note = ("They asked for a number: this is a holding answer only. Comment \"offer N\" here when you decide." if needs_number
                    else "Charles's draft from the conversation so far.")
            from .. import second_read
            note = (note + " " + second_read.line(second)).strip()
            if second is not None:
                n["second_read"] = n.get("second_read", 0) + 1
                if not second_read.passed(second):
                    n["second_read_objects"] = n.get("second_read_objects", 0) + 1
            gid = set_answer(conn, cfg, asana, row["id"], body, note=note)
            if gid:
                conn.execute("UPDATE messages SET reply_card=?, reply_draft=? WHERE id=?", (gid, body, msg["id"]))
                pending.add(w["agent_id"])
                n["drafted"] += 1
    except Exception as e:  # noqa: BLE001 — never break the tick
        log.warning("reply writer: %s", e)
        n["error"] = str(e)[:200]
    return n
