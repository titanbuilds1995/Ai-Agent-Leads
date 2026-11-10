"""Rodrigo's notes on a 1031 card become Charles's next email (Rodrigo, 2026-11-10: "can I just put a message on the comments and it
will read it, when I put it in Approved, like the other board?").

Same flow as the email-approvals board:
- He writes on the card what Charles should say ("tell him we'll sign the NDA and ask for the T-12"), or the email itself ("Hi Kate,
  …"). Claude writes it in Charles's voice in the broker's thread (a dictation keeps his words; punctuation only) and posts it on the
  card: "✍️ Charles's reply from your note (not sent)".
- It goes out when (a) the note ends with a plain "send it" (nothing conditional, no number: `comment_router.send_after_rewrite`), or
  (b) he drags the card to "Approved · send OM request" (send_om_requests approves the waiting draft for a card already in
  conversation), or (c) he comments "send it" under it later. "hold" stops it. Sent by books_review.send_approved_replies like every
  other reply to a broker (same thread, 📤 comment, card leaves Needs Me).
The draft is stored as a review row (reply_kind 'note', or 'note_send' when approved), so there is one path that emails brokers.
Plain "send it" / "hold" comments are not notes; they act on whatever draft is waiting.

The board also TALKS BACK (Rodrigo, 2026-11-10: "have this whole board have conversations with us, just like the MLS board … if I send
it to Needs Me, it can answer me back what it thinks"):
- a question on a card ("what do you think?", "is 3.2 too low?", "why does this not fit?") gets a "💬" answer on that card from the
  listing, the books review, the deal sheet and the broker thread; a note that is both a question and an instruction gets both;
- a card sitting in Needs Me gets ONE "💬 What I think" take per new development (new broker answer or new review): pursue or pass,
  the number to aim at, the next move. Nothing is sent to anyone by a 💬."""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from .. import db
from ..util import iso, log, now_utc
from .deal_sheet import SHEET_RE

GREETING_RE = re.compile(r"^\s*(?:hi|hey|hello|good (?:morning|afternoon|evening)|dear)\b[^\n]{0,40},?\s*$", re.I | re.M)
BANNED_RE = re.compile(r"\b(?:our (?:buyer|client|investor)s?|a buyer we|my (?:buyer|client|investor)s?|the buyer we)\b", re.I)
INSTRUCT_RE = re.compile(r"\b(?:tell|ask|say|let (?:him|her|them) know|reply|respond|answer (?:him|her|them)|email (?:him|her|them)|"
                         r"write (?:him|her|them|back)|send (?:him|her|them)|offer|counter|request|follow up)\b", re.I)
CHAT_SYSTEM = """You are the acquisitions analyst at Titan Realty Investments, talking with Rodrigo Gayoso (principal) on the card of a
commercial deal for a 1031 exchange: the box is $3.5M to $8M at an 8 percent cap or better on real trailing numbers after the tax
reassessment at our price. Answer him like a sharp colleague: plain words, short, your actual opinion, the numbers that matter, and
the next move. No hedging, no fluff, no headers. When he asks about the price or whether it works, say where it has to be for a true
8 cap after the tax reset at that price (taxable ~85% of price x the county's ~17-22 mills) with your assumptions. If the data cannot answer it, say what is missing and how to get it. Never send or
promise anything to the broker; this is between you and Rodrigo."""
CHAT_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"], "additionalProperties": False}
FIRST_LOOK = timedelta(hours=2)      # a thread seen for the first time: only his comments from the last two hours count as notes

SYSTEM = """You are Charles Grant, junior acquisitions agent at Titan Realty Investments. Rodrigo Gayoso (acquisitions manager) left
a note on the deal card telling you what to send to the commercial broker. Write that email as your next reply in the thread.

Rules:
- If Rodrigo's note IS the email (it starts with a greeting or reads as a finished message), keep his words: fix spelling and
  punctuation only, add nothing.
- Otherwise write it from his note: say exactly what he asked, nothing more. Short paragraphs, 40 to 120 words, plain and direct,
  no fluff, no "I hope this finds you well", no "honestly", no "curious".
- Titan speaks as the principal: "we", "our side", "a partner of ours" for the 1031 exchange. Never "our buyer", "our client" or
  "our investor".
- Use a dollar figure only if Rodrigo's note gives it. Never invent terms.
- No date, deadline or timeframe and no commitment (proof of funds, signing an NDA / LOI / contract, a deposit, "we will send")
  unless Rodrigo's note itself says it.
- Start "Hey <broker first name>," on its own line. End with these lines, each on its own line:
  Charles Grant / Junior Acquisitions Agent / Titan Realty Investments / Office Line: +1 (561) 405 7379"""
SCHEMA = {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"], "additionalProperties": False}


def is_command_only(text: str) -> bool:
    """'send it', 'hold', '👍': acts on a waiting draft, not a note to write from."""
    from ..comment_router import plain_go_ahead
    from .books_review import HOLD_RE
    t = (text or "").strip()
    if not t or plain_go_ahead(t):
        return True
    return bool(HOLD_RE.search(t)) and len(re.sub(r"[^a-z]", "", t.lower())) < 40


def _signature(cfg: Any) -> str:
    lines = cfg.get("operator.signature_lines") or ["Charles Grant", "Junior Acquisitions Agent", "Titan Realty Investments", "Office Line: +1 (561) 405 7379"]
    return "\n".join(lines)


def _strip_send(text: str) -> str:
    from ..comment_router import SEND_AFTER_RE
    m = SEND_AFTER_RE.search(text or "")
    return (text[:m.start()] if m else text).strip().rstrip(",.;")


def write_email(cfg: Any, llm, t: dict[str, Any], listing: dict[str, Any], msgs: list[dict[str, Any]], note: str) -> str | None:
    """Charles's email from Rodrigo's note. A dictation is used as written when Claude is unreachable; anything else waits."""
    from ..replies.clean import clean_reply
    from .books_review import _first_name
    first = _first_name(t.get("broker_name"), t.get("broker_email"))
    note = _strip_send(note)
    if llm is not None and getattr(llm, "available", False):
        convo = "\n".join(f"--- {m.get('created_at')} broker:\n{clean_reply(m.get('body') or '')[:1500]}" for m in msgs[-3:])
        user = (f"Property: {listing.get('street') or ''}, {listing.get('city') or ''}. Broker: {t.get('broker_name') or ''} "
                f"<{t.get('broker_email') or ''}> (first name: {first}).\n\nThe broker's latest messages:\n{convo or '(none yet)'}\n\n"
                f"Rodrigo's note:\n{note}\n\nWrite the email now.")
        try:
            out = llm.structured("commercial_note_reply", SYSTEM, user, SCHEMA, max_tokens=700)
        except Exception as e:  # noqa: BLE001
            log.warning("note reply %s: %s", t.get("listing_id"), e)
            out = None
        email = ((out or {}).get("email") or "").strip()
        if email:
            if "Charles Grant" not in email:
                email += "\n\nBest regards,\n\n" + _signature(cfg)
            return email
    if GREETING_RE.search(note.split("\n", 1)[0] or ""):      # his own words: send them as written
        return note.rstrip() + "\n\nBest regards,\n\n" + _signature(cfg)
    return None


def is_question(text: str) -> bool:
    from ..card_chat import QUESTION_RE
    return bool(QUESTION_RE.search(text or ""))


def is_instruction(text: str) -> bool:
    t = (text or "").strip()
    return bool(GREETING_RE.search(t.split("\n", 1)[0] or "")) or (bool(INSTRUCT_RE.search(t)) and not t.rstrip().endswith("?"))


def _context(conn, cfg: Any, t: dict[str, Any]) -> str:
    """What Claude knows about the deal: listing, latest reads, deal sheet, the broker thread."""
    from ..replies.clean import clean_reply
    from .books_review import _listing, _listing_block, _messages
    from .deal_sheet import computed, load
    L = _listing(conn, t["listing_id"])
    parts = [_listing_block(L), f"Broker: {t.get('broker_name') or ''} <{t.get('broker_email') or ''}>"]
    entry = load(conn).get(t["listing_id"])
    if entry:
        d = computed(entry.get("sheet") or {}, L, entry.get("offer"))
        parts.append("Deal sheet: " + "; ".join(f"{k}={v}" for k, v in d.items() if v not in (None, [], "") and k not in ("investor",)))
    for (txt,) in conn.execute("SELECT text FROM commercial_reviews WHERE thread_id=? AND text IS NOT NULL ORDER BY id DESC LIMIT 2", (t["id"],)):
        parts.append("Earlier read on the card:\n" + (txt or "")[:2500])
    msgs = _messages(conn, t["id"])
    if msgs:
        parts.append("Broker's latest messages:\n" + "\n".join(f"--- {m.get('created_at')}\n{clean_reply(m.get('body') or '')[:1200]}" for m in msgs[-3:]))
    return "\n\n".join(p for p in parts if p)


def chat(conn, cfg: Any, llm, t: dict[str, Any], question: str) -> str | None:
    if llm is None or not getattr(llm, "available", False):
        return None
    try:
        out = llm.structured("commercial_card_chat", CHAT_SYSTEM, _context(conn, cfg, t) + f"\n\nRodrigo asks:\n{question}", CHAT_SCHEMA, max_tokens=700)
    except Exception as e:  # noqa: BLE001
        log.warning("1031 card chat %s: %s", t.get("listing_id"), e)
        return None
    return ((out or {}).get("answer") or "").strip() or None


def needs_me_takes(conn, cfg: Any, asana, llm, now=None) -> dict[str, Any]:
    """A card in Needs Me gets one "💬 What I think" per new development (Rodrigo: "if I send it to Needs Me, it can answer me back")."""
    from .board import NEEDS_ME
    from .books_review import _column_now
    res = {"takes": 0, "waiting": 0}
    if asana is None or llm is None or not getattr(llm, "available", False):
        return res
    for t in conn.execute("SELECT * FROM commercial_threads WHERE card_gid IS NOT NULL AND bounced=0").fetchall():
        t = db.row_to_dict(t)
        latest = conn.execute("SELECT MAX(id) FROM commercial_reviews WHERE thread_id=? AND source != 'note'", (t["id"],)).fetchone()[0] or 0
        if t.get("take_review_id") is not None and t["take_review_id"] == latest or _column_now(conn, asana, t["listing_id"], t["card_gid"]) != NEEDS_ME:
            continue
        answer = chat(conn, cfg, llm, t, "This card is in my Needs Me column. Give me, short and in this order: (1) where it has to be for a "
                                        "true 8 cap, with your assumptions (NOI used, tax after a sale, manager / reserve) and the price; "
                                        "(2) pros; (3) cons; (4) what you don't believe in what they gave us, and why; (5) what checks out; "
                                        "(6) pursue or pass and the one next move. If something blocks it (NDA, missing books), say so.")
        if not answer:
            res["waiting"] += 1
            continue
        asana.comment(t["card_gid"], "💬 What I think:\n\n" + answer)
        conn.execute("UPDATE commercial_threads SET take_review_id=? WHERE id=?", (latest, t["id"]))
        res["takes"] += 1
    return res


def handle_notes(conn, cfg: Any, asana, llm=None, now=None) -> dict[str, Any]:
    """Every 1031 card in conversation: his new notes → Charles's drafted reply on the card (sent per the rules above)."""
    from ..asana import BOT
    from ..comment_router import send_after_rewrite
    from .books_review import _listing, _messages
    res: dict[str, Any] = {"cards": 0, "notes": 0, "drafted": 0, "approved": 0, "waiting": 0, "errors": 0}
    if asana is None:
        return res
    now = now or now_utc()
    # Needs Revision (Rodrigo, 2026-11-10: "a section that says needs revision … so I can chat with the agent there", like the email
    # board): a card there with his comment is answered / rewritten, then moves to Needs Me; one dragged there with no comment goes
    # straight back to Needs Me with one line.
    from .board import NEEDS_ME, NEEDS_REVISION, ensure_board
    project = cfg.get("commercial.asana.project")
    secs = ensure_board(asana, project) if project else {}
    try:
        in_rev = {x["gid"] for x in asana.section_tasks(secs[NEEDS_REVISION])} if secs.get(NEEDS_REVISION) else set()
    except Exception as e:  # noqa: BLE001
        log.warning("Needs Revision column: %s", e)
        in_rev = set()

    def done_revising(gid: str, empty: bool = False) -> None:
        if gid in in_rev and secs.get(NEEDS_ME):
            if empty:
                asana.comment(gid, "Moved back to Needs Me: there is no comment on it to work on. Write what is unclear, then drag it to Needs Revision again.")
            asana.move(gid, secs[NEEDS_ME])
            res["revised"] = res.get("revised", 0) + 1

    rows = conn.execute("SELECT * FROM commercial_threads WHERE card_gid IS NOT NULL AND bounced=0").fetchall()
    for t in rows:
        t = db.row_to_dict(t)
        res["cards"] += 1
        seen = t.get("notes_seen_at") or iso(now - FIRST_LOOK)
        try:
            comments = asana.comments(t["card_gid"])
        except Exception as e:  # noqa: BLE001
            res["errors"] += 1
            log.warning("1031 notes %s: %s", t["card_gid"], e)
            continue
        new = [c for c in comments if not (c.get("text") or "").startswith(BOT) and (c.get("created_at") or "9") > seen
               and not SHEET_RE.search(c.get("text") or "")]       # DEAL SHEET updates feed the site, they are not notes to Charles
        notes = [(c.get("text") or "").strip() for c in new if not is_command_only(c.get("text") or "")]
        if not notes:       # nothing to act on: remember the newest comment actually seen (never "now", which could skip one)
            mark = max([seen] + [c.get("created_at") for c in comments if c.get("created_at")])
            if mark != t.get("notes_seen_at"):
                conn.execute("UPDATE commercial_threads SET notes_seen_at=? WHERE id=?", (mark, t["id"]))
            done_revising(t["card_gid"], empty=True)
            continue
        res["notes"] += 1
        questions = [n for n in notes if is_question(n)]
        orders = [n for n in notes if is_instruction(n) or not is_question(n)]
        if questions:
            answer = chat(conn, cfg, llm, t, "\n\n".join(questions))
            if not answer:
                res["waiting"] += 1      # keep the question unread: the next round tries again
                if not any("couldn't answer yet" in (c.get("text") or "") for c in comments[-3:]):
                    asana.comment(t["card_gid"], "💬 Got your question; I couldn't answer yet (Claude unreachable from the Mac). The next round tries again.")
                continue
            asana.comment(t["card_gid"], "💬 " + answer)
            res["answered"] = res.get("answered", 0) + 1
        if not orders:
            conn.execute("UPDATE commercial_threads SET notes_seen_at=? WHERE id=?", (max(c.get("created_at") or "" for c in new), t["id"]))
            done_revising(t["card_gid"])
            continue
        notes = orders
        note = "\n\n".join(notes)
        email = write_email(cfg, llm, t, _listing(conn, t["listing_id"]), _messages(conn, t["id"]), note)
        if not email:
            res["waiting"] += 1      # Claude unreachable and the note is not a dictation: try again next round, note kept
            if not any("couldn't write it yet" in (c.get("text") or "") for c in comments[-3:]):
                asana.comment(t["card_gid"], "✍️ Got your note; I couldn't write it yet (Claude unreachable from the Mac). The next round tries again.")
            continue
        if BANNED_RE.search(email) and not BANNED_RE.search(note):
            res["errors"] += 1
            asana.comment(t["card_gid"], "✍️ The draft called us someone's agent (\"our buyer / client / investor\"), so it was not saved. "
                                         "Rewrite the note or write the email yourself.")
            conn.execute("UPDATE commercial_threads SET notes_seen_at=? WHERE id=?", (max(c.get("created_at") or "" for c in new), t["id"]))
            continue
        go = send_after_rewrite(notes)
        kind = "note_send" if go else "note"
        last = conn.execute("SELECT verdict FROM commercial_reviews WHERE thread_id=? ORDER BY id DESC LIMIT 1", (t["id"],)).fetchone()
        text = "✍️ From your note" + (" (goes out at the next round)" if go else " (not sent: drag the card to \"Approved · send OM request\" or comment 'send it')")
        conn.execute("INSERT INTO commercial_reviews (thread_id, listing_id, gmail_message_id, verdict, needs_rodrigo, text, source, reply_draft, "
                     "reply_kind, created_at, forwarded_at, archived_at, parked_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (t["id"], t["listing_id"], t.get("gmail_message_id"), last[0] if last else None, 0, text, "note", email, kind, iso(now),
                      iso(now), iso(now), iso(now)))
        conn.execute("UPDATE commercial_threads SET notes_seen_at=? WHERE id=?", (max(c.get("created_at") or "" for c in new), t["id"]))
        asana.comment(t["card_gid"], f"{text}:\n\n{email}")
        res["drafted"] += 1
        done_revising(t["card_gid"])
    return res


def approve_waiting(conn, listing_id: str) -> dict[str, Any] | None:
    """The card was dragged to the Approved column while already in conversation: approve the drafted reply waiting on it."""
    row = conn.execute("""SELECT r.id, r.reply_draft, t.broker_email FROM commercial_reviews r JOIN commercial_threads t ON t.id=r.thread_id
                          WHERE t.listing_id=? AND r.id=(SELECT MAX(id) FROM commercial_reviews WHERE thread_id=t.id)
                            AND r.reply_draft IS NOT NULL AND r.reply_sent_at IS NULL ORDER BY r.id DESC LIMIT 1""", (listing_id,)).fetchone()
    if not row:
        return None
    conn.execute("UPDATE commercial_reviews SET reply_kind='note_send' WHERE id=?", (row[0],))
    return {"review_id": row[0], "to": row[2]}

