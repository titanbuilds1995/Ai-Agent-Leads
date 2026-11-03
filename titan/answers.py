"""ONE CARD PER LEAD (Rodrigo, 2026-11-03, Thais Guevara: "I don't want other cards. I just want all the cards to be on the same
one ... I said the email that I wanted and then I approved it ... it's giving it back to me and saying that it needs me instead of
drafting the email and sending it").

Before this, Charles's answer to an agent was a second card ("Reply · Agent · street"). Rodrigo dictated the email on the LEAD
card and dragged the lead card to Approved; the engine forwarded his words to the reply card, rewrote them there, and bounced
the lead card back to Needs Me with "that card is the one that sends". Now the answer lives ON the lead card:

- `set_answer` puts Charles's next email (an answer, a follow-up, an offer) on the lead card as a NEXT EMAIL block (To / Subject /
  EMAIL markers, the same block the sender reads), keeps a copy in `outreach.answer_draft`, and moves the card to Needs Me,
  or to Approved when Rodrigo already said to send it.
- Dragging the LEAD card to Approved sends that block in the agent's Gmail thread (`approvals.send_approved`), the card gets a
  "📤 Sent" comment, the block is cleared and the first email comes back as the card's history.
- A separate reply card still on the board is folded into its lead card (`fold_reply_cards`) and completed, except one already
  in Approved, which sends as it is.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import db
from .util import log

START, END = "-----EMAIL-----", "-----END EMAIL-----"
NEXT_EMAIL = "NEXT EMAIL"
PENDING, APPROVED = "pending", "approved"


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def pending(conn, outreach_id: int) -> dict[str, Any] | None:
    """The answer waiting on this lead's card (body / note / state), or None."""
    r = conn.execute("SELECT answer_draft, answer_note, answer_state FROM outreach WHERE id=?", (outreach_id,)).fetchone()
    if not r or not (r["answer_draft"] or "").strip():
        return None
    return {"body": r["answer_draft"], "note": r["answer_note"] or "", "state": r["answer_state"] or PENDING}


def hand_written(conn, row: dict[str, Any], card: dict[str, Any]) -> bool:
    """Rodrigo wrote the answer straight into the EMAIL lines of a sent lead card (no engine draft): the text differs from the
    first email that went out. A card that still shows the sent email is not an answer."""
    if not row or row.get("status") != "sent" or not (card.get("body") or "").strip():
        return False
    body = _squash(card["body"])
    return body != _squash(row.get("body") or "") and body != _squash(row.get("draft_body") or "")


def answer_subject(row: dict[str, Any], props: list[dict[str, Any]]) -> str:
    street = ", ".join(x.get("street") or "" for x in props) or "your listing"
    subject = row.get("subject") or f"Quick question on {street}"
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def lead_of(conn, outreach_id: int) -> tuple[dict[str, Any] | None, dict[str, Any], list[dict[str, Any]]]:
    row = db.row_to_dict(conn.execute("SELECT * FROM outreach WHERE id=?", (outreach_id,)).fetchone())
    if not row:
        return None, {}, []
    agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (row["agent_id"],)).fetchone()) or {}
    pids = json.loads(row.get("property_ids") or "[]")
    props = [db.row_to_dict(r) for r in conn.execute(
        f"SELECT * FROM properties WHERE id IN ({','.join('?' * len(pids)) or 'NULL'})", pids).fetchall()]
    return row, agent, props


def _warm_aware(cfg: Any, asana):
    """A warm-board card must stay on the warm board (its Needs Me column), never jump to the approvals board."""
    from .warm_leads import wrap
    if hasattr(asana, "is_warm"):
        if not getattr(asana, "loaded", True):
            asana.lead_cards()
        return asana
    if not (cfg.get("outreach.asana", {}) or {}).get("warm_leads", {}).get("project"):
        return asana
    try:
        from .approvals import _cfg, needs_me_section, needs_revision_section
        a = _cfg(cfg)
        sent, answered = getattr(asana, "_lead_board", None) or (a.get("sent"), a.get("answered"))
        w = wrap(cfg, asana, needs_me_section(cfg, asana), needs_revision_section(cfg, asana), sent, answered)
        w.lead_cards()
        return w
    except Exception as e:  # noqa: BLE001
        log.warning("warm board not read: %s", e)
        return asana


def render(conn, cfg: Any, asana, gid: str, notes: str, outreach_id: int, answer: dict[str, Any] | None) -> None:
    """Write the lead card's description: WHERE WE STAND (when the agent answered), the NEXT EMAIL block when an answer is
    waiting, the listing, the first email as history. The same layout `conversations.lead_board` keeps up every round."""
    from .conversations import lead_card_html, plain_from_html, where_we_stand, _when
    row, agent, props = lead_of(conn, outreach_id)
    if not row:
        return
    st = where_we_stand(conn, row["agent_id"], bool(answer)) if row.get("status") == "sent" else None
    html = lead_card_html(st, notes, _when(row["sent_at"]) if row.get("sent_at") else "", answer, row, agent, props)
    fn = getattr(asana, "update_html_notes", None)
    if fn is not None:
        fn(gid, html)
    else:
        asana.update_notes(gid, plain_from_html(html))


def set_answer(conn, cfg: Any, asana, outreach_id: int, body: str, note: str = "", approved: bool = False,
               quiet: bool = False, picture: bool = True) -> str | None:
    """Put Charles's next email to this agent on the lead's own card (NEXT EMAIL block) and in `outreach.answer_draft`;
    the card goes to Needs Me (his to approve) or to Approved when Rodrigo said in words to send it. Returns the card gid.
    With no lead card at all, a reply card is the only place left (never on a normal lead)."""
    from .approvals import _cfg, _attach_picture, needs_me_section, post_reply_card
    row, agent, props = lead_of(conn, outreach_id)
    if not row:
        return None
    gid = row.get("asana_task_id")
    if not gid:
        return post_reply_card(conn, cfg, asana, outreach_id, body, section=_cfg(cfg)["approved"] if approved else None, note=note)
    asana = _warm_aware(cfg, asana)
    to = row.get("to_address") or agent.get("email") or ""
    answer = {"to": to, "subject": answer_subject(row, props), "body": body, "note": note}
    conn.execute("UPDATE outreach SET answer_draft=?, answer_note=?, answer_state=? WHERE id=?",
                 (body, note, APPROVED if approved else PENDING, outreach_id))
    try:
        task = asana.task(gid)
    except Exception as e:  # noqa: BLE001 — the lead card is gone: nothing to put it on
        log.warning("lead card %s for outreach %s unreadable (%s); answer kept in the database only", gid, outreach_id, e)
        return None
    render(conn, cfg, asana, gid, task.get("notes") or "", outreach_id, answer)
    if picture:
        rev = len([1 for c in (asana.comments(gid) or []) if "NEXT EMAIL" in (c.get("text") or "")]) + 1
        _attach_picture(cfg, asana, gid, to, answer["subject"], body, outreach_id, rev=100 + rev)
    a = _cfg(cfg)
    warm = hasattr(asana, "is_warm") and asana.is_warm(gid)
    if approved and not warm:
        asana.move(gid, a["approved"])
    elif not approved:
        asana.move(gid, needs_me_section(cfg, asana))
    if not quiet:
        asana.comment(gid, (f"{NEXT_EMAIL} is on this card (below WHERE WE STAND). " + (note + " " if note else "")
                            + ("It goes out at the next send round (you said to send it)." + (" Comment \"hold\" to stop it." if warm else
                               " Drag the card out of Approved to stop it.") if approved else
                               "Nothing sends until you drag this card to Approved" + (" (or comment \"send it\")" if warm else "")
                               + ". Comment to change it, or edit the text between the EMAIL lines yourself.")))
    return gid


def approve(conn, cfg: Any, asana, outreach_id: int, gid: str) -> None:
    """Rodrigo said send it: the answer on the lead card goes out at the next send round."""
    from .approvals import _cfg
    conn.execute("UPDATE outreach SET answer_state=? WHERE id=?", (APPROVED, outreach_id))
    asana = _warm_aware(cfg, asana)
    if not (hasattr(asana, "is_warm") and asana.is_warm(gid)):
        asana.move(gid, _cfg(cfg)["approved"])


def hold(conn, cfg: Any, asana, outreach_id: int, gid: str) -> None:
    from .approvals import needs_me_section
    conn.execute("UPDATE outreach SET answer_state=? WHERE id=? AND answer_draft IS NOT NULL", (PENDING, outreach_id))
    asana = _warm_aware(cfg, asana)
    asana.move(gid, needs_me_section(cfg, asana))


def clear(conn, cfg: Any, asana, outreach_id: int, gid: str | None, notes: str | None = None) -> None:
    """The answer went out (or was dropped): the card shows the first email again; the draft is gone."""
    conn.execute("UPDATE outreach SET answer_draft=NULL, answer_note=NULL, answer_state=NULL WHERE id=?", (outreach_id,))
    if gid:
        try:
            render(conn, cfg, asana, gid, notes if notes is not None else (asana.task(gid).get("notes") or ""), outreach_id, None)
        except Exception as e:  # noqa: BLE001
            log.warning("could not redraw lead card %s: %s", gid, e)


def fold_reply_cards(conn, cfg: Any, asana) -> dict[str, int]:
    """One card per lead: a separate "Reply ·" / "Follow-up ·" / "Offer ·" card still waiting (Fresh Leads / Needs Revision /
    Needs Me) becomes the NEXT EMAIL on its lead card and is completed. A reply card already in Approved sends as it is."""
    from .approvals import _cfg, needs_me_section, needs_revision_section, parse_card
    a = _cfg(cfg)
    n = {"folded": 0}
    for sec in (a.get("to_review"), needs_revision_section(cfg, asana), needs_me_section(cfg, asana)):
        for t in (asana.section_tasks(sec) if sec else []):
            pc = parse_card(t.get("notes") or "")
            if not pc or not pc.get("reply_to"):
                continue
            try:
                row = db.row_to_dict(conn.execute("SELECT * FROM outreach WHERE id=?", (pc["outreach_id"],)).fetchone())
                if not row or not row.get("asana_task_id") or row["asana_task_id"] == t["gid"]:
                    continue
                lead_pc = parse_card((asana.task(row["asana_task_id"]) or {}).get("notes") or "")
                if pending(conn, row["id"]) or (lead_pc and hand_written(conn, row, lead_pc)):
                    note = "The lead card already carries an answer; this duplicate card is closed."
                else:
                    set_answer(conn, cfg, asana, row["id"], pc["body"], note="Moved here from the separate reply card (one card per lead).",
                               picture=False, quiet=True)      # quiet: a bot comment here would swallow his pending comment
                    conn.execute("UPDATE messages SET reply_card=? WHERE reply_card=?", (row["asana_task_id"], t["gid"]))
                    conn.execute("UPDATE follow_ups SET card_gid=? WHERE card_gid=?", (row["asana_task_id"], t["gid"]))
                    note = "One card per lead (Rodrigo, 2026-11-03): this answer now sits on the lead's own card as NEXT EMAIL. This card is closed."
                asana.comment(t["gid"], note)
                close_card(cfg, asana, t["gid"])
                n["folded"] += 1
            except Exception as e:  # noqa: BLE001
                log.warning("folding reply card %s: %s", t.get("gid"), e)
    return n


def close_card(cfg: Any, asana, gid: str) -> None:
    """A duplicate card is taken off the working columns (completed cards still list in a section) and completed."""
    from .approvals import _cfg
    try:
        asana.move(gid, _cfg(cfg)["sent"])
    except Exception as e:  # noqa: BLE001
        log.warning("moving closed card %s: %s", gid, e)
    asana.complete(gid)


def pending_agents(conn) -> set[int]:
    """Agents with an answer already waiting on their lead card."""
    return {r[0] for r in conn.execute("SELECT agent_id FROM outreach WHERE answer_draft IS NOT NULL AND TRIM(answer_draft)!=''").fetchall()}


# ---------- Rodrigo dictating the email on the card ----------
DICTATE_RE = re.compile(r"^\s*(?:(?:please |hey |ok(?:ay)? |hi )*)(?:answer|reply|respond|say|write back|tell (?:him|her|them)|"
                        r"email (?:him|her|them)|send (?:him|her|them)|reply with|respond with|"
                        r"i want (?:us|you|charles) to (?:answer|reply|say|respond|write|send|email))\b", re.I)
# "I wrote a message up top … put that into an email and send it to her" (Thais, 2026-11-02): the email is an earlier comment
USE_ABOVE_RE = re.compile(r"\b(?:put (?:that|this|it|what i wrote|the message)[^.]{0,30}(?:in|into) an? email|"
                          r"(?:send|email) (?:her|him|them) (?:that|this|it|what i wrote|the message|exactly that)|"
                          r"(?:that|this|the message(?: up top| above)?) (?:is|'s) (?:the|what i want in the) email|"
                          r"use (?:that|this|it|the message) as the email|(?:message|email) (?:up top|above)|"
                          r"(?:what|the email) i wrote (?:above|up top|there))\b", re.I)
SEND_WORD_RE = re.compile(r"\bsend (?:it|this|that|him|her|them)\b|\bsend the email\b|\bsend (?:her|him|them) the email\b", re.I)
NO_SEND_RE = re.compile(r"\b(?:don'?t|do not|never|not yet|hold|wait|before (?:you |we )?send|don'?t send)\b", re.I)


def dictation(feedback: list[str]) -> tuple[str, str] | None:
    """(the email in his words, the instruction around it) when his comment(s) dictate the email to the agent, else None.
    Forms: "Answer the email with the following: Hey Richard, …" (one comment) and the email as one comment followed by
    "put that into an email and send it to her" (two comments)."""
    cleaned = [f.strip() for f in feedback if f.strip()]
    if not cleaned:
        return None
    last = cleaned[-1]
    if len(cleaned) >= 2 and USE_ABOVE_RE.search(last):
        earlier = [c for c in cleaned[:-1] if len(c.split()) >= 12 and not USE_ABOVE_RE.search(c)]
        if earlier:
            return "\n\n".join(earlier), last
    text = "\n".join(cleaned)
    if DICTATE_RE.search(text):
        m = re.search(r"\b(?:following|say|write|answer|reply|respond|with)\b[^:]{0,40}:\s*(.+)$", text, re.I | re.S)
        body = (m.group(1) if m else DICTATE_RE.sub("", text, count=1)).strip().strip('"“”')
        if len(body.split()) >= 6:
            return body, text[: len(text) - len(body)] if m else DICTATE_RE.search(text).group(0)
    return None


def wants_send(instruction: str) -> bool:
    """His instruction around a dictated email says send it (and nothing says hold / wait / don't)."""
    return bool(SEND_WORD_RE.search(instruction or "")) and not NO_SEND_RE.search(instruction or "")


TIDY_SYSTEM = """Rodrigo (Principal, Titan Realty Investments) dictated, by voice, the email his acquisitions agent {sender} will send to a
listing agent. Turn the dictation into the email, keeping it HIS: every point, his order, his words and tone, nothing added,
nothing of substance removed, no new facts, no softening. Fix punctuation, spelling, capitalization and dictation slips (run-on
sentences, "what I what I wrote", repeated words); split into short paragraphs; the greeting alone on the first line
("Hey {first},", or his greeting if he gave one); keep every question he asked (several questions are fine here, they are his);
keep any number or term he stated exactly as he said it; no dashes of any kind; plain text; end with the signature block given.
Return JSON: body."""
TIDY_SCHEMA = {"type": "object", "properties": {"body": {"type": "string"}}, "required": ["body"], "additionalProperties": False}


def tidy_dictation(llm, cfg: Any, body: str, agent: dict[str, Any]) -> str:
    """His words as a clean email (Claude fixes the dictation only); his raw words formatted when Claude is not reachable."""
    from .outreach.compose import format_paragraphs, signature
    from .util import first_name
    sig = signature(cfg)
    first = first_name(agent.get("name")) or "there"
    out = None
    if llm is not None and getattr(llm, "available", False):
        try:
            op = cfg["operator"]
            out = llm.structured("tidy_dictation", TIDY_SYSTEM.format(sender=op["sender_name"].split()[0], first=first),
                                 json.dumps({"dictation": body, "agent_first_name": first, "signature_block": sig}, ensure_ascii=False),
                                 TIDY_SCHEMA, max_tokens=900)
        except Exception as e:  # noqa: BLE001
            log.warning("tidy dictation: %s", e)
    text = (out or {}).get("body") if isinstance(out, dict) else None
    if not text or len(text.split()) < max(6, len(body.split()) // 3):
        text = body
        if not re.match(r"^(hey|hi|hello|good (?:morning|afternoon|evening))\b", text, re.I):
            text = f"Hey {first},\n\n{text}"
        text = re.sub(r"^((?:hey|hi|hello)\s+\w+)[,:]?\s*(\w)", lambda g: f"{g.group(1)},\n\n{g.group(2).upper()}", text, count=1, flags=re.I)
    text = format_paragraphs(text)
    if "charles" not in text.lower()[-220:]:
        text = f"{text.rstrip()}\n\n{sig}"
    return text
