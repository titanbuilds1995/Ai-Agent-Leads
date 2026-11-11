"""Email approvals on Asana (Rodrigo, 2026-10-24): every first-touch email becomes a card with a picture of the email.
Rodrigo comments feedback (the engine rewrites the card) and drags good ones to Approved; approved emails go out from
Charles's Gmail during the send window, a couple at a time, at varied times. Nothing is sent from To Review.

Card notes carry the email between markers so edits made directly on the card are what gets sent."""
from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import db
from .asana import BOT, Asana, AsanaError
from .learning import learned_block
from .outreach.compose import EMAIL_SCHEMA, compose_email, may_ask_how_much_work, photo_rule_problems, protocol_text, seed_for, signature, tone_problems, validate_email, voice_text
from .util import iso, log, now_utc, today_et

START, END = "-----EMAIL-----", "-----END EMAIL-----"


def _cfg(cfg: Any) -> dict[str, Any]:
    return cfg.get("outreach.asana", {}) or {}


NEEDS_REVISION = "Needs Revision"
NEEDS_ME = "Needs Me"
FRESH_LEADS = "Fresh Leads"     # Rodrigo, 2026-10-27: the old "To Review" column, fresh cards he has not touched
NUMBERS_HELD = "Numbers are above"
PROMPT_PREFIX = "Tell me what to change"


def needs_revision_section(cfg: Any, asana: Asana) -> str:
    """Rodrigo, 2026-10-26: a third column. To Review = ready for his decision; Needs Revision = he commented and the
    engine is rewriting (or could not yet); Approved = sends. The section is created on the board the first time."""
    a = _cfg(cfg)
    if a.get("needs_revision"):
        return a["needs_revision"]
    cached = getattr(asana, "_needs_revision_gid", None)
    if cached:
        return cached
    gid = asana.ensure_section(a["project"], NEEDS_REVISION, insert_before=a.get("approved"))
    try:
        asana._needs_revision_gid = gid
    except Exception:  # noqa: BLE001
        pass
    return gid


def ensure_fresh_leads_name(cfg: Any, asana: Asana) -> None:
    """Rodrigo, 2026-10-27: "To Review is only properties that I've not touched yet, and they're fresh … name it fresh leads".
    The section keeps its gid (config `to_review`); only its name changes, once. Never raises."""
    if getattr(asana, "_fresh_named", False):
        return
    a = _cfg(cfg)
    try:
        for sec in asana.sections(a["project"]):
            if sec.get("gid") == a.get("to_review") and (sec.get("name") or "").strip().lower() != FRESH_LEADS.lower():
                asana.rename_section(sec["gid"], FRESH_LEADS)
        asana._fresh_named = True
    except Exception as e:  # noqa: BLE001
        log.warning("renaming To Review to Fresh Leads: %s", e)


def needs_me_section(cfg: Any, asana: Asana) -> str:
    """Rodrigo, 2026-10-27: "we need one more column, it should say needs me". Cards where the work is done and the next
    move is his: numbers posted and waiting on his call, an agent waiting on his number, a drafted answer or offer to
    approve. "Answered" keeps only the leads where we replied and the ball is with the agent. Created the first time."""
    a = _cfg(cfg)
    if a.get("needs_me"):
        return a["needs_me"]
    cached = getattr(asana, "_needs_me_gid", None)
    if cached:
        return cached
    gid = asana.ensure_section(a["project"], NEEDS_ME, insert_before=a.get("to_review"))
    try:
        asana._needs_me_gid = gid
    except Exception:  # noqa: BLE001
        pass
    return gid


def agent_line(agent: dict[str, Any]) -> str:
    """Rodrigo, 2026-10-26: the agent's phone on every card so he can call the ones he likes."""
    from .util import pretty_phone
    phone = pretty_phone(agent.get("phone")) if agent.get("phone") else ""
    return f"Agent: {agent.get('name')} · {phone + ' · ' if phone else ''}{agent.get('email')} · {agent.get('brokerage') or ''}"


def card_notes(to: str, subject: str, body: str, outreach_id: int, meta: str) -> str:
    return (f"To: {to}\nSubject: {subject}\n{START}\n{body}\n{END}\n\n{meta}\n\n"
            f"Comment to change it · drag to Approved to send (business hours) · or edit the text between the EMAIL lines yourself.\n(outreach #{outreach_id})")


def parse_card(notes: str) -> dict[str, Any] | None:
    """A first-touch card ends with "(outreach #N)"; a reply card (an answer in an existing conversation) ends with
    "(reply to outreach #N)" and is sent inside that email's Gmail thread."""
    to = re.search(r"^To:\s*(\S+@\S+)\s*$", notes or "", re.M)
    subj = re.search(r"^Subject:\s*(.+)$", notes or "", re.M)
    body = re.search(re.escape(START) + r"\n(.*?)\n" + re.escape(END), notes or "", re.S)
    oid = re.search(r"\((reply to )?outreach #(\d+)\)", notes or "")
    if not (to and subj and body and oid):
        return None
    cc = re.search(r"^Cc:\s*(.+)$", (notes or "").split(START)[0], re.M | re.I)   # optional, above the email (Rodrigo, 2026-10-26)
    return {"to": to.group(1), "subject": subj.group(1).strip(), "body": body.group(1).strip(), "outreach_id": int(oid.group(2)),
            "reply_to": bool(oid.group(1)), "cc": [x.strip() for x in re.split(r"[,;\s]+", cc.group(1)) if "@" in x] if cc else []}


def reply_card_notes(to: str, subject: str, body: str, outreach_id: int, meta: str) -> str:
    return (f"To: {to}\nSubject: {subject}\n{START}\n{body}\n{END}\n\n{meta}\n\n"
            f"Charles's answer, sent in the same email thread. Comment to change it · drag to Approved to send (business hours) · or edit the text "
            f"between the EMAIL lines yourself.\n(reply to outreach #{outreach_id})")


def post_reply_card(conn, cfg: Any, asana: Asana, outreach_id: int, body: str, section: str | None = None, note: str = "") -> str | None:
    """Put an answer to an agent on the approvals board as a reply card (Needs Me by default: it is his to approve;
    Fresh Leads holds only first emails he has not touched). Returns the card gid."""
    a = _cfg(cfg)
    row = db.row_to_dict(conn.execute("SELECT * FROM outreach WHERE id=?", (outreach_id,)).fetchone())
    if not row:
        return None
    agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (row["agent_id"],)).fetchone()) or {}
    pids = json.loads(row["property_ids"] or "[]")
    props = [db.row_to_dict(r) for r in conn.execute(f"SELECT * FROM properties WHERE id IN ({','.join('?' * len(pids)) or 'NULL'})", pids).fetchall()]
    street = ", ".join(x["street"] for x in props) or "your listing"
    subject = row.get("subject") or f"Quick question on {street}"
    subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    meta = f"Answering {agent.get('name')} about {street}.\n{agent_line(agent)}" + (f"\n{note}" if note else "")
    task = asana.create_task(a["project"], section or needs_me_section(cfg, asana), f"Reply · {agent.get('name')} · {street}",
                             reply_card_notes(row["to_address"] or agent.get("email"), subject, body, outreach_id, meta))
    return task.get("gid")


def analysis_text(analysis: dict[str, Any] | None) -> str:
    """The think-first analysis behind the email, for Rodrigo's review (protocol §21)."""
    if not analysis:
        return ""
    lines = ["Why this house (engine's read, not sent):"]
    if analysis.get("hypothesis"):
        lines.append(f"• Hypothesis: {analysis['hypothesis']}")
    if analysis.get("rehab_level"):
        lines.append(f"• Likely rehab level: {analysis['rehab_level']} of 5")
    if analysis.get("key_unknown"):
        lines.append(f"• Biggest unknown: {analysis['key_unknown']}")
    for k, label in (("positive_signals", "Positives"), ("negative_signals", "Negatives"), ("unknown_big_ticket", "Unknown big-ticket")):
        if analysis.get(k):
            lines.append(f"• {label}: " + "; ".join(str(x) for x in analysis[k][:5]))
    return "\n".join(lines)


def listing_meta(p: dict[str, Any], agent: dict[str, Any], analysis: dict[str, Any] | None = None) -> str:
    lane = p.get("lane") or ""
    photos = f" · {p.get('photos_count')} photos" if p.get("photos_count") is not None else ""
    grade = f" · comps {p['comp_grade']}" if p.get("comp_grade") else ""
    meta = (f"Listing: {p.get('street')}, {p.get('city')} · ${int(p.get('list_price') or 0):,} · {p.get('days_on_market')} days on market · "
            f"{p.get('price_cuts') or 0} price cut(s){photos}{grade} · {lane}\n{p.get('listing_url') or ''}\n"
            f"{agent_line(agent)}")
    at = analysis_text(analysis)
    return f"{meta}\n\n{at}" if at else meta


def post_card(conn, cfg: Any, asana: Asana, agent: dict[str, Any], props: list[dict[str, Any]], d: dict[str, Any],
              outreach_id: int) -> str:
    a = _cfg(cfg)
    p = props[0]
    lane = p.get("lane") or ""
    meta = listing_meta(p, agent, d.get("analysis"))
    name = f"{agent.get('name') or 'Agent'} · {p.get('street')}, {p.get('city')}" + (" ⚡ fresh deal" if lane == "FRESH_DEAL" else "")
    task = asana.create_task(a["project"], a["to_review"], name, card_notes(agent["email"], d["subject"], d["body"], outreach_id, meta))
    _attach_picture(cfg, asana, task["gid"], agent["email"], d["subject"], d["body"], outreach_id)
    conn.execute("UPDATE outreach SET asana_task_id=?, draft_body=COALESCE(draft_body, body) WHERE id=?", (task["gid"], outreach_id))
    return task["gid"]


def _attach_picture(cfg: Any, asana: Asana, task: str, to: str, subject: str, body: str, outreach_id: int, rev: int = 0) -> None:
    try:
        from .email_card import render
        op = cfg["operator"]
        path = cfg.path("reports") / "email_cards" / f"{outreach_id}_{rev}.png"
        render(path, f"{op['sender_name']} <{op.get('sender_email') or cfg.secret('GMAIL_SENDER')}>", to, subject, body)
        asana.attach(task, path, f"email_{outreach_id}{'_v' + str(rev) if rev else ''}.png")
    except Exception as e:  # noqa: BLE001 — the card text is the source of truth; a missing picture is not fatal
        log.warning("email picture for outreach %s failed: %s", outreach_id, e)


REVISE_SYSTEM = """You revise one email from {sender} ({company}) to a listing agent, following Rodrigo's feedback exactly.
The outreach protocol below still applies in full (no price/offer/percentage/dollar figure, no listing echo, no manufactured observations,
one easy question, 40-90 words, plain text, signature block unchanged, and NO dashes of any kind: no em dash, en dash, ' - ' or '--'; use commas or periods).
The first paragraph must say who we are in a personable sentence or two ({sender} with {company}, this listing came up or stood out)
and the company name says it all: never "we're investors", "we buy here regularly" or "genuinely interested". We never state that we
are the buyer ("we're looking for properties in X", "what we look for", "our side"; never "we buy", "we'd buy", "serious buyers").
and name every listed street in the body. If the feedback asks for numbers or analysis rather than a change to the email, keep the email a
conversation (ask the agent for the fact instead of stating a figure).
DIRECT (Rodrigo, 2026-10-28): matter of fact and serious, never AI-ish. After the intro go straight to the question. Never tell the agent
what doesn't scare, bother or worry us or what is "fine on our side"; never recite the listing's upgrades back ("with the new electrical
panel in"); never mention the electrical panel; no "curious", "honestly", "potentially", "the bigger question is".
PHOTOS (Rodrigo, 2026-10-27): each property carries photos_count. With 4 or more photos the agent knows the condition is visible in the
listing, so never ask or imply that we need to find out how much work it needs ("how much work would you say it really needs", "how deep
is the project", "mostly cosmetic or something bigger", "what's your read on the condition"); ask about what photos can't show instead
(roof and AC age, plumbing, electrical, permits, occupancy, why it's sitting, seller flexibility). Only when may_ask_how_much_work is true
(fewer than 4 photos) may the email say the listing doesn't give much to go on and ask about the work. Never describe photos.

=== OUTREACH PROTOCOL ===
{protocol}
=== END PROTOCOL ===

=== VOICE PROTOCOL ("I" for what {sender} did, "we"/"our side" for criteria; never powerless, never overcommitting) ===
{voice}
=== END VOICE PROTOCOL ===
{learned}
Return JSON: subject and body (body includes the unchanged signature block)."""



REPLY_CARD_NOTE = """
THIS CARD IS A REPLY in an existing conversation, not a first email: no intro paragraph, ignore the 40-90 word rule (25-90 words),
keep the subject unchanged, never re-ask what the agent already answered, still one question and no number or offer language."""


def _reply_draft(conn, gid: str) -> str | None:
    r = conn.execute("SELECT reply_draft FROM messages WHERE reply_card=? ORDER BY id DESC LIMIT 1", (gid,)).fetchone()
    return r[0] if r else None


TEST_RE = re.compile(r"\btest\b", re.I)
EMAIL_IN_TEXT_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


FAILED_PREFIX = "I couldn't rewrite this one automatically"


def pending_feedback(asana: Asana, task_gid: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Rodrigo's comments since the engine's last comment on the card (the engine's own comments start with BOT).
    "Couldn't rewrite" and "Held" comments do not count: the feedback stays pending until a rewrite actually lands."""
    comments = asana.comments(task_gid)
    from .tidy import tidy_comments
    tidy_comments(asana, task_gid, comments)        # Rodrigo, 2026-11-02: superseded engine comments are removed as we pass
    last_bot = max((i for i, c in enumerate(comments) if (c.get("text") or "").startswith(BOT)
                    and not (c.get("text") or "").startswith((f"{BOT} {FAILED_PREFIX}", f"{BOT} Held:", f"{BOT} {PROMPT_PREFIX}"))), default=-1)
    return [c["text"] for c in comments[last_bot + 1:] if (c.get("text") or "").strip() and not c["text"].startswith(BOT)], comments


def test_request(feedback: list[str], cfg: Any) -> str | None:
    """'send me a test' (optionally with an address) → the address to send a [TEST] copy to."""
    text = " ".join(feedback)
    if not TEST_RE.search(text):
        return None
    m = EMAIL_IN_TEXT_RE.search(text)
    return (m.group(0) if m else None) or cfg.secret("REPORT_TO") or None


def send_test_copy(cfg: Any, asana: Asana, gmail, task_gid: str, card: dict[str, Any], to: str) -> bool:
    if gmail is None:
        asana.comment(task_gid, f"Can't send the test copy to {to}: Charles's Gmail isn't connected on this machine.")
        return False
    try:
        gmail.send(to, f"[TEST] {card['subject']}", f"(Test copy. The real email would go to {card['to']}.)\n\n{card['body']}")
    except Exception as e:  # noqa: BLE001
        asana.comment(task_gid, f"Test copy to {to} failed: {str(e)[:200]}")
        return False
    asana.comment(task_gid, f"Test copy sent to {to} from Charles's Gmail. Nothing went to the agent. Comment more feedback, or drag to Approved when it's good.")
    return True


AGENT_LINE_RE = re.compile(r"^Agent: .*$", re.M)
PHONE_IN_LINE_RE = re.compile(r"\(\d{3}\) \d{3}-\d{4}|\+1 ?\d")


def _add_phone(conn, asana: Asana, t: dict[str, Any], card: dict[str, Any]) -> bool:
    """Put the agent's phone on an older card that was written without it. Once per card; never raises."""
    try:
        notes = t.get("notes") or ""
        m = AGENT_LINE_RE.search(notes)
        if not m or PHONE_IN_LINE_RE.search(m.group(0)):
            return False
        row = conn.execute("SELECT a.* FROM agents a JOIN outreach o ON o.agent_id=a.id WHERE o.id=?", (card["outreach_id"],)).fetchone()
        if not row or not row["phone"]:
            return False
        new = notes[:m.start()] + agent_line(db.row_to_dict(row)) + notes[m.end():]
        asana.update_notes(t["gid"], new)
        t["notes"] = new
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("phone on card %s: %s", t.get("gid"), e)
        return False


def _comps_fn(cfg: Any, conn, cache: dict[str, Any]):
    """Sold comps for the numbers snapshot: free HomeHarvest pull, last 12 months (Rodrigo's comps rule). None off the Mac."""
    if "fn" not in cache:
        try:
            from .ingest.homeharvest_src import comps_fn
            import homeharvest  # noqa: F401  (only installed on the Mac)
            cache["fn"] = comps_fn(cfg, conn, months=12)
        except Exception:  # noqa: BLE001
            cache["fn"] = None
    return cache["fn"]


def route_comment(conn, cfg: Any, asana: Asana, t: dict[str, Any], where: str, card: dict[str, Any], feedback: list[str],
                  n: dict[str, int], nr: str, comps_cache: dict[str, Any], llm=None):
    """Do what the comment asks (titan/comment_router.py). True = handled, no rewrite. A list = the agent's listings,
    to put in front of the rewrite (he asked to double-check one). None = plain rewrite."""
    from . import comment_router as cr
    want = cr.classify(feedback)
    a = _cfg(cfg)
    nm = needs_me_section(cfg, asana)
    if "call" in want:
        n["calls"] += int(cr.call_card(conn, cfg, asana, card, feedback))
    if "numbers" in want:
        try:
            posted = cr.numbers_snapshot(conn, cfg, asana, t["gid"], card, feedback, _comps_fn(cfg, conn, comps_cache))
        except Exception as e:  # noqa: BLE001 — say it once, keep the feedback pending (a Held comment does not consume it)
            log.exception("numbers snapshot for card %s: %s", t["gid"], e)
            last = (asana.comments(t["gid"]) or [{}])[-1].get("text") or ""
            if not last.startswith(f"{BOT} Held: couldn't build the numbers"):
                asana.comment(t["gid"], f"Held: couldn't build the numbers yet ({str(e)[:120]}). Nothing sends; I'll try again next round.")
            if where != nr:
                asana.move(t["gid"], nr)
            n["revise_failed"] += 1
            return True
        n["numbers"] += posted
        from . import card_chat
        if card_chat.is_question(feedback):   # Rodrigo, 2026-10-27 (David Carlton): "give me an analysis and feedback", not just a table
            try:
                card_chat.answer(conn, cfg, asana, llm, t, card, feedback, asana.comments(t["gid"]))
            except Exception as e:  # noqa: BLE001
                log.warning("card chat after numbers on %s: %s", t["gid"], e)
        asana.comment(t["gid"], f"{NUMBERS_HELD}. Moved to Needs Me: nothing sends from there. "
                                "Comment what to send, or drag the card to Approved to send it as written."
                      + (" Lead marked hot and a call card is on your board for tomorrow." if "call" in want else ""))
        nm = needs_me_section(cfg, asana)
        if where != nm:
            asana.move(t["gid"], nm)
        if not cr.DICTATED_RE.search(" ".join(feedback)):
            return True
        # Rodrigo, 2026-10-29 (Juan Huice): "change the email … how are the subfloors doing" posted the numbers and stopped,
        # the email never changed. A comment that asks for numbers AND dictates a change gets both: fall through to the rewrite.
    if "send" in want and "rewrite" not in want:
        asana.move(t["gid"], a["approved"])
        asana.comment(t["gid"], "Sending it as written (you said send it): moved to Approved, it goes out at the next send round."
                      + (" Lead marked hot and a call card is on your board for tomorrow." if "call" in want else ""))
        n["sent_as_is"] += 1
        return True
    if "hold" in want and not want & {"rewrite", "check"}:
        from . import card_chat
        tail = ("Holding it: nothing sends from here until you drag the card to Approved. The text is unchanged."
                + (" Lead marked hot and a call card is on your board for tomorrow." if "call" in want else ""))
        if card_chat.is_question(feedback):      # Rodrigo, 2026-10-27: a question gets an answer, not a "held"
            _, comments = pending_feedback(asana, t["gid"])
            card_chat.answer(conn, cfg, asana, llm, t, card, feedback, comments, tail=tail)
        else:
            asana.comment(t["gid"], tail)
        if where == a["to_review"]:              # he touched it: it is not a fresh lead any more (Rodrigo, 2026-10-27)
            asana.move(t["gid"], nm)
        return True
    if want == {"call"}:
        asana.comment(t["gid"], "Lead marked hot and a call card is on your board for tomorrow. The email is unchanged"
                                + (": moved to Needs Me." if where != nm else "."))
        if where != nm:
            asana.move(t["gid"], nm)
        return True
    from . import card_chat
    if card_chat.is_question(feedback):
        # Rodrigo, 2026-10-27 (David Carlton, Alex Castro): "tell me what you're seeing" on a card that also gets rewritten
        # was answered with a new email and no chat. A question gets its 💬 answer whatever else the comment triggers.
        try:
            card_chat.answer(conn, cfg, asana, llm, t, card, feedback, asana.comments(t["gid"]))
        except Exception as e:  # noqa: BLE001
            log.warning("card chat on %s: %s", t["gid"], e)
    if "check" in want:
        return cr.agent_listings(conn, card["outreach_id"]) or ["(none besides this card's)"]
    return None


def dictated_answer(feedback: list[str], cfg: Any) -> str | None:
    """Rodrigo dictating the reply on a lead card ("Answer the email with the following: Hey Richard, ...", or the email as one
    comment and "put that into an email and send it to her" as the next) → the email body in his words, formatted, with
    Charles's signature. None when the comment is not a dictated answer. (`answers.dictation` finds it; this adds the layout.)"""
    from .answers import dictation
    from .outreach.compose import format_paragraphs
    d = dictation(feedback)
    if not d:
        return None
    body = d[0]
    body = re.sub(r"^(hey|hi|hello)\s+(\w+)[,:]?\s*(\w)", lambda g: f"{g.group(1).capitalize()} {g.group(2).capitalize()},\n\n{g.group(3).upper()}",
                  body, count=1, flags=re.I)
    body = format_paragraphs(body)
    if "best regards" not in body.lower() and "charles" not in body.lower()[-200:]:
        body = f"{body.rstrip()}\n\n{signature(cfg)}"
    return body


def asana_name(asana: Asana, gid: str | None) -> str:
    try:
        return (asana.task(gid) or {}).get("name") or (gid or "")
    except Exception:  # noqa: BLE001
        return gid or ""


def revise(conn, cfg: Any, llm, card: dict[str, Any], feedback: list[str], props: list[dict[str, Any]], is_reply: bool,
           routed: list[str] | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """Rewrite the email on a card from Rodrigo's feedback. (out, problems): out is None when Claude is unreachable."""
    op = cfg["operator"]
    facts = [{k: p.get(k) for k in ("street", "city", "zip", "list_price", "original_list_price", "price_cuts", "days_on_market", "beds", "baths",
                                    "sqft", "lot_sqft", "year_built", "property_type", "vacant", "photos_count")} | {"listing_remarks_verbatim": (p.get("remarks") or "")[:2000]}
             for p in props]
    out = llm.structured("revise_email", REVISE_SYSTEM.format(sender=op["sender_name"].split()[0], company=op["company"], protocol=protocol_text(cfg), voice=voice_text(cfg),
                                            learned=learned_block(cfg)) + (REPLY_CARD_NOTE if is_reply else ""),
                         json.dumps({"feedback": feedback, "subject": card["subject"], "body": card["body"], "properties": facts,
                                     "may_ask_how_much_work": may_ask_how_much_work(props),
                                     **({"agent_listings_we_know": routed} if isinstance(routed, list) else {}),
                                     "rodrigo_notes_on_this_lead": _notes(conn, card["outreach_id"]),
                                     "signature_block": signature(cfg)}, ensure_ascii=False), EMAIL_SCHEMA) if llm and getattr(llm, "available", False) else None
    if out is None:
        from .llm import login_problem
        err = getattr(llm, "last_error", None) if llm is not None else None
        why = login_problem(cfg, err) or ("Claude isn't reachable from this job" + (f": {err[:160]}" if err else
                                                                                   " (not logged in, or CLAUDE_CODE_OAUTH_TOKEN missing in .env)"))
        return None, [why]
    from .replies.writer import validate_reply
    from .outreach.compose import format_paragraphs
    out["body"] = format_paragraphs(out["body"])
    return out, (validate_reply(out["body"], props) if is_reply else validate_email(out["subject"], out["body"], props))


def _held_once(asana: Asana, comments: list[dict[str, Any]], gid: str, text: str) -> None:
    last = (comments[-1].get("text") or "") if comments else ""
    if not last.startswith(f"{BOT} {FAILED_PREFIX}") and not last.startswith(f"{BOT} Held:"):
        asana.comment(gid, text)


def _hold_reply(asana: Asana, reply: tuple[dict[str, Any], str], a: dict[str, Any], nr: str, why: str) -> None:
    """A legacy reply card sitting in Approved is not sent while his comment on the lead card is being applied."""
    rt, rwhere = reply
    if rwhere == a["approved"]:
        asana.move(rt["gid"], nr)
        asana.comment(rt["gid"], f"Held: {why}. Nothing sends from Needs Revision; drag it to Approved again when you're ready.")


HOLD_WORDS_RE = re.compile(r"\b(?:don'?t send|do not send|hold(?: it| this| off)?|wait|not yet|stop|pause|hold on)\b", re.I)


def lead_card_comment(conn, cfg: Any, asana: Asana, llm, t: dict[str, Any], where: str, card: dict[str, Any], feedback: list[str],
                      comments: list[dict[str, Any]], reply: tuple[dict[str, Any], str] | None, n: dict[str, int], nr: str, nm: str,
                      comps_cache: dict[str, Any]) -> None:
    """Rodrigo's comment on a LEAD card (its first email already went out). ONE CARD PER LEAD (Rodrigo, 2026-10-27 and again
    2026-11-03, Thais: "I don't want other cards"): everything lands on this card and the answer to the agent IS this card's
    NEXT EMAIL block. What his comment can do here:
      numbers → snapshot comment here;  a question → 💬 answer here;
      the email in his words ("Answer with: Hey Thais, …" or the text + "put that into an email and send it") → it becomes the
        NEXT EMAIL on this card, straight to Approved when he said send;
      "send it" → the answer on this card goes to Approved;  "hold" → back to Needs Me;
      a change ("ask her about the roof", "shorter") → the answer on this card is rewritten, or written from the conversation
        when none is drafted yet; then Needs Me (Approved when he also said send it)."""
    from . import answers, card_chat, comment_router as cr
    a = _cfg(cfg)
    gid = t["gid"]
    row = db.row_to_dict(conn.execute("SELECT * FROM outreach WHERE id=?", (card["outreach_id"],)).fetchone()) or {}
    ans = answers.pending(conn, card["outreach_id"])
    if not ans and answers.hand_written(conn, row, card):
        ans = {"body": card["body"], "note": "", "state": answers.APPROVED if where == a["approved"] else answers.PENDING}
        conn.execute("UPDATE outreach SET answer_draft=?, answer_state=? WHERE id=?", (card["body"], ans["state"], card["outreach_id"]))
    if ans and card.get("body") and _squash_text(card["body"]) != _squash_text(row.get("body") or ""):
        ans["body"] = card["body"]            # what the card says is what sends (he may have edited the lines himself)
    text = " ".join(feedback)
    want = cr.classify(feedback)
    question = card_chat.is_question(feedback)
    if "call" in want:
        n["calls"] += int(cr.call_card(conn, cfg, asana, card, feedback))
    if "numbers" in want:
        try:
            posted = cr.numbers_snapshot(conn, cfg, asana, gid, card, feedback, _comps_fn(cfg, conn, comps_cache),
                                         tail="Internal numbers: nothing reaches the agent until you decide. "
                                              + ("The NEXT EMAIL on this card is untouched." if ans else
                                                 "Comment what Charles should answer and it becomes the NEXT EMAIL on this card."))
        except Exception as e:  # noqa: BLE001 — say it once, keep the comment pending (a Held comment does not consume it)
            log.exception("numbers snapshot for lead card %s: %s", gid, e)
            _held_once(asana, comments, gid, f"Held: couldn't build the numbers yet ({str(e)[:120]}). I'll try again next round.")
            n["revise_failed"] += 1
            return
        n["numbers"] += posted
        if question:      # "re-check these comps and give me a range": the table AND the read, here
            try:
                if card_chat.answer(conn, cfg, asana, llm, t, card, feedback, asana.comments(gid), lead_card=True, say_failed=False):
                    n["chat"] += 1
            except Exception as e:  # noqa: BLE001
                log.warning("card chat after numbers on %s: %s", gid, e)
        if reply:
            _hold_reply(asana, reply, a, nr, "you asked for numbers on the lead card, so this answer waits for your decision")
        if not (answers.dictation(feedback) or cr.DICTATED_RE.search(text)):
            asana.comment(gid, f"{NUMBERS_HELD}. Waiting here in Needs Me" + ("; the NEXT EMAIL on this card is held until you decide: "
                                    "comment what Charles should say, or drag the card to Approved to send it as written." if ans else
                                    ". Comment what Charles should answer and it becomes the NEXT EMAIL on this card.")
                          + (" Lead marked hot and a call card is on your board for tomorrow." if "call" in want else ""))
            answers.hold(conn, cfg, asana, card["outreach_id"], gid) if ans else (asana.move(gid, nm) if where != nm else None)
            return
    # the email in his own words → it IS the next email on this card
    d = answers.dictation(feedback)
    if d:
        body_words, instruction = d
        agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (row.get("agent_id"),)).fetchone()) or {}
        body = answers.tidy_dictation(llm, cfg, body_words, agent)
        go = where == a["approved"] or answers.wants_send(instruction) or answers.wants_send(text)
        answers.set_answer(conn, cfg, asana, card["outreach_id"], body, note="Your words from this card" + (", sent because you said send it" if go else ""),
                           approved=go, quiet=True)
        from .learning import record_lesson
        try:
            record_lesson(conn, "reply", text, card["outreach_id"], gid, (ans or {}).get("body") or "", body)
        except Exception as e:  # noqa: BLE001
            log.warning("lesson not recorded: %s", e)
        if reply:                      # a legacy reply card for this agent is now a duplicate
            asana.comment(reply[0]["gid"], "Closed: the answer now lives on the lead card (one card per lead).")
            answers.close_card(cfg, asana, reply[0]["gid"])
        asana.comment(gid, "Your email is the NEXT EMAIL on this card now (your words, cleaned up, picture attached). "
                           + ("It is in Approved and goes out at the next send round (8:30am–5:30pm ET). Drag it to Needs Me to stop it." if go else
                              "Moved to Needs Me: drag this card to Approved to send it, or comment what to change.")
                           + (" Lead marked hot and a call card is on your board for tomorrow." if "call" in want else ""))
        n["reply_cards"] = n.get("reply_cards", 0) + 1
        n["dictated"] = n.get("dictated", 0) + 1
        return
    send_ask = ("send" in want or "send_after" in want or (re.search(r"\bsend\b", text, re.I) and not HOLD_WORDS_RE.search(text)))
    change = bool(cr.CHANGE_RE.search(text) or cr.DICTATED_RE.search(text) or "rewrite" in want and not question)
    if "send" in want or (send_ask and not change and not question):
        if ans:
            answers.approve(conn, cfg, asana, card["outreach_id"], gid)
            asana.comment(gid, "Sending the NEXT EMAIL on this card as written (you said send it): it goes out at the next send round."
                          + (" Lead marked hot and a call card is on your board for tomorrow." if "call" in want else ""))
            n["sent_as_is"] += 1
            if reply:
                asana.comment(reply[0]["gid"], "Closed: the answer on the lead card is the one that sends (one card per lead).")
                answers.close_card(cfg, asana, reply[0]["gid"])
            return
        if reply:                      # legacy: the answer still sits on its own card
            asana.move(reply[0]["gid"], a["approved"])
            asana.comment(gid, f"Sending the drafted answer \"{reply[0].get('name')}\" as written: it goes out at the next send round.")
            n["sent_as_is"] += 1
            if where != nm:
                asana.move(gid, nm)
            return
        asana.comment(gid, "Nothing is drafted for this agent yet, so there is nothing to send. Write the email here in a comment "
                           "(\"Answer with: Hey …\" or the text followed by \"send it\"), or tell me what to ask and I'll write it.")
        if where != nm:
            asana.move(gid, nm)
        return
    if HOLD_WORDS_RE.search(text) and not change and ans:
        answers.hold(conn, cfg, asana, card["outreach_id"], gid)
        asana.comment(gid, "Held: the NEXT EMAIL on this card stays in Needs Me until you drag it to Approved. The text is unchanged.")
        return
    # Claude reads his comment first: a question gets its 💬 answer here, what he tells us about the lead is kept (lead note),
    # and the part that changes the answer to the agent (reply_change) is applied to the NEXT EMAIL below
    out = None
    try:
        out = card_chat.answer(conn, cfg, asana, llm, t, card, feedback, comments, lead_card=True, forward_change=False, say_failed=question)
    except Exception as e:  # noqa: BLE001
        log.warning("card chat on lead card %s: %s", gid, e)
    if out:
        n["chat"] += 1
    instruction = ((out or {}).get("reply_change") or "").strip()
    if question:
        if out is None:
            n["chat_failed"] = n.get("chat_failed", 0) + 1       # "Held: couldn't answer yet" is on the card; retried next round
            return
        if not instruction and not change:
            if where == a["approved"] and ans:
                return                   # he asked something while it waits to send: the send stands
            if where != nm:
                asana.move(gid, nm)
            return
    if not ans and not instruction and not change and not cr.WRITE_RE.search(text):
        # input about the lead with nothing to write yet ("called him, left a voicemail"): kept as a note, nothing drafted
        if where != nm:
            asana.move(gid, nm)
        return
    instruction = instruction or text
    if want == {"call"}:
        asana.comment(gid, "Lead marked hot and a call card is on your board for tomorrow. " + ("The NEXT EMAIL on this card is unchanged." if ans else "The email is unchanged."))
        if where != nm and not (ans and where == a["approved"]):
            asana.move(gid, nm)
        return
    # a change to the answer: rewrite the one on this card, or write it from the conversation when none is drafted yet
    go = "send_after" in want or (answers.wants_send(text) and not question)
    agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (row.get("agent_id"),)).fetchone()) or {}
    props = _props_for(conn, row)
    if ans:
        if where == a["approved"]:
            answers.hold(conn, cfg, asana, card["outreach_id"], gid)     # never send what he is still changing
        draft_card = {**card, "body": ans["body"], "subject": card["subject"] if card["subject"].lower().startswith("re:") else answers.answer_subject(row, props)}
        out, problems = revise(conn, cfg, llm, draft_card, [instruction] if instruction != text else feedback, props, is_reply=True)
        body = out["body"] if out and not problems else None
    else:
        from .replies.writer import write_answer
        body, problems = write_answer(conn, cfg, llm, row, agent, props, instruction=instruction)
    if not body:
        _held_once(asana, comments, gid, f"{FAILED_PREFIX} ({', '.join(problems)}). I'll retry on the next round; you can also write the "
                                         "email in a comment (\"Answer with: …\") or edit the EMAIL lines on the card yourself.")
        n["revise_failed"] += 1
        if where != nm:
            asana.move(gid, nm)
        return
    answers.set_answer(conn, cfg, asana, card["outreach_id"], body, note="From your comment on this card", approved=go, quiet=True)
    from .learning import record_lesson
    try:
        record_lesson(conn, "reply", text, card["outreach_id"], gid, (ans or {}).get("body") or "", body)
    except Exception as e:  # noqa: BLE001
        log.warning("lesson not recorded: %s", e)
    if reply:
        asana.comment(reply[0]["gid"], "Closed: the answer now lives on the lead card (one card per lead).")
        answers.close_card(cfg, asana, reply[0]["gid"])
    asana.comment(gid, ("Revised based on your comment: the NEXT EMAIL on this card (picture attached). " if ans else
                        "Wrote Charles's answer from your comment: it is the NEXT EMAIL on this card (picture attached). ")
                  + ("You said send it: it is in Approved and goes out at the next send round." if go else
                     "Moved to Needs Me: drag this card to Approved to send it, or comment more changes."))
    n["revised"] += 1


def _squash_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _notes(conn, outreach_id: int) -> list[str]:
    from .card_chat import lead_notes
    r = conn.execute("SELECT agent_id FROM outreach WHERE id=?", (outreach_id,)).fetchone()
    return lead_notes(conn, r[0] if r else None)


def sync_feedback(conn, cfg: Any, asana: Asana, llm, gmail=None) -> dict[str, int]:
    """Rewrite To Review cards that got new feedback comments (or send a test copy when asked); mark Skip cards as skipped."""
    a = _cfg(cfg)
    nr = needs_revision_section(cfg, asana)
    n = {"revised": 0, "skipped": 0, "revise_failed": 0, "tests_sent": 0, "moved_to_revision": 0, "phones_added": 0,
         "numbers": 0, "sent_as_is": 0, "calls": 0}
    comps_cache: dict[str, Any] = {}
    nm = needs_me_section(cfg, asana)
    ensure_fresh_leads_name(cfg, asana)
    n["chat"] = 0
    from .conversations import board_sections
    sent_sec, answered = board_sections(cfg, asana) if a.get("project") else (None, None)
    from .warm_leads import wrap
    asana = wrap(cfg, asana, nm, nr, sent_sec, answered)     # Rodrigo's Titan Warm Leads board is read too (2026-10-29)
    def _board():
        in_approved = [(t, a["approved"]) for t in asana.section_tasks(a["approved"])]
        tasks = ([(t, a["to_review"]) for t in asana.section_tasks(a["to_review"])] + [(t, nr) for t in asana.section_tasks(nr)]
                 + [(t, nm) for t in asana.section_tasks(nm)] + [(t, answered) for t in (asana.section_tasks(answered) if answered else [])])
        return tasks, in_approved

    from .answers import fold_reply_cards
    try:
        n["folded"] = fold_reply_cards(conn, cfg, asana)["folded"]     # one card per lead (Rodrigo, 2026-11-03)
    except Exception as e:  # noqa: BLE001
        log.warning("folding reply cards: %s", e)
    tasks, in_approved = _board()
    replies: dict[int, tuple[dict[str, Any], str]] = {}     # outreach id -> (card, section) of the answer waiting to go out to that agent
    for t, where in tasks + in_approved:
        pc = parse_card(t.get("notes") or "")
        if pc and pc.get("reply_to") and where != answered:
            replies.setdefault(pc["outreach_id"], (t, where))

    def _sent_lead(card):
        return not card.get("reply_to") and (conn.execute(
            "SELECT status FROM outreach WHERE id=?", (card["outreach_id"],)).fetchone() or ["sent"])[0] == "sent"

    # Lead cards first (first email already sent; Sent · no answer yet / Answered / Needs Me / Needs Revision / To Review /
    # Approved): his comment is answered on that card, and what changes the drafted answer is passed to the reply card before
    # the loop below rewrites it (Rodrigo, 2026-10-27, Lise and Cheryl: comments on the lead card never reached the answer).
    warm = asana.lead_cards() if hasattr(asana, "lead_cards") else []
    for t, where in ([(t, sent_sec) for t in (asana.section_tasks(sent_sec) if sent_sec else [])] + tasks + in_approved + warm):
        card = parse_card(t.get("notes") or "")
        if not card or not _sent_lead(card):
            continue
        feedback, comments = pending_feedback(asana, t["gid"])
        if not feedback:
            continue                      # lead_board keeps it in Needs Me / Answered
        try:
            lead_card_comment(conn, cfg, asana, llm, t, where, card, feedback, comments, replies.get(card["outreach_id"]), n, nr, nm, comps_cache)
        except Exception as e:  # noqa: BLE001 — the comment stays pending; next round tries again
            log.exception("handling the comment on lead card %s: %s", t["gid"], e)
            n["revise_failed"] += 1
    tasks, in_approved = _board()         # re-read: a reply card held from Approved by a lead-card comment is rewritten this round
    for t, where in tasks + in_approved:
        card = parse_card(t.get("notes") or "")
        if not card or _sent_lead(card):
            continue
        if where == a["approved"]:
            continue                      # cards in Approved are send_approved's job
        if _add_phone(conn, asana, t, card):
            n["phones_added"] += 1
        feedback, comments = pending_feedback(asana, t["gid"])
        if not feedback:
            if where == nr:
                last = (comments[-1].get("text") or "") if comments else ""
                if any((c.get("text") or "").startswith(f"{BOT} {NUMBERS_HELD}") for c in comments):   # numbers done before Needs Me existed
                    asana.move(t["gid"], nm)
                    asana.comment(t["gid"], "Moved to Needs Me: the numbers are on this card and it waits on your decision.")
                    n["to_needs_me"] = n.get("to_needs_me", 0) + 1
                    continue
                if not last.startswith(f"{BOT} {FAILED_PREFIX}") and not last.startswith(f"{BOT} Held:"):
                    # Rodrigo, 2026-11-02: "if it doesn't have a comment, it needs to get out of Needs Revision". That column holds
                    # only cards he just commented on while the rewrite lands; a bare drag goes straight back to Needs Me.
                    asana.move(t["gid"], nm)
                    asana.comment(t["gid"], "No comment on this card, so it's back in Needs Me (Needs Revision is only for cards with "
                                            "a comment waiting on a rewrite). Comment what to change, or drag to Approved to send.")
                    n["back_to_needs_me"] = n.get("back_to_needs_me", 0) + 1
            continue
        test_to = test_request(feedback, cfg)
        if test_to and not [f for f in feedback if not TEST_RE.search(f)]:
            n["tests_sent"] += int(send_test_copy(cfg, asana, gmail, t["gid"], card, test_to))
            continue
        try:
            routed = route_comment(conn, cfg, asana, t, where, card, feedback, n, nr, comps_cache, llm)
        except Exception as e:  # noqa: BLE001 — leave the feedback pending; the next round tries again
            log.exception("handling the comment on card %s: %s", t["gid"], e)
            n["revise_failed"] += 1
            continue
        if routed is True:
            continue
        row = db.row_to_dict(conn.execute("SELECT property_ids FROM outreach WHERE id=?", (card["outreach_id"],)).fetchone()) or {}
        props = [db.row_to_dict(r) for r in conn.execute(
            f"SELECT * FROM properties WHERE id IN ({','.join('?' * len(json.loads(row.get('property_ids') or '[]'))) or 'NULL'})",
            json.loads(row.get("property_ids") or "[]"))]
        out, problems = revise(conn, cfg, llm, card, feedback, props, is_reply=bool(card.get("reply_to")), routed=routed if isinstance(routed, list) else None)
        if problems:
            last = (comments[-1].get("text") or "") if comments else ""
            if not last.startswith(f"{BOT} {FAILED_PREFIX}"):      # say it once; the feedback stays pending and is retried next tick
                asana.comment(t["gid"], f"{FAILED_PREFIX} ({', '.join(problems)}). I'll retry on the next round; you can also edit the "
                                        f"text on the card yourself or comment again with different wording.")
            if where != nr:                                         # it is not ready for a decision: park it in Needs Revision
                asana.move(t["gid"], nr)
                n["moved_to_revision"] += 1
            n["revise_failed"] += 1
            continue
        notes = (t["notes"] or "")
        notes = re.sub(r"^Subject:.*$", f"Subject: {out['subject']}", notes, count=1, flags=re.M)
        notes = re.sub(re.escape(START) + r"\n.*?\n" + re.escape(END), lambda _m: f"{START}\n{out['body']}\n{END}", notes, count=1, flags=re.S)
        asana.update_notes(t["gid"], notes)
        rev = sum(1 for c in comments if (c.get("text") or "").startswith(f"{BOT} Revised")) + 1
        _attach_picture(cfg, asana, t["gid"], card["to"], out["subject"], out["body"], card["outreach_id"], rev)
        if card.get("reply_to"):      # an answer card: its text lives with the message it answers, not on the first email
            conn.execute("UPDATE messages SET reply_draft=? WHERE reply_card=?", (out["body"], t["gid"]))
        else:
            conn.execute("UPDATE outreach SET subject=?, body=? WHERE id=?", (out["subject"], out["body"], card["outreach_id"]))
        from .learning import card_kind, record_lesson
        for f in feedback:
            if not (TEST_RE.search(f) and len(f.split()) <= 8):
                record_lesson(conn, card_kind(t.get("notes") or ""), f, card["outreach_id"], t["gid"], card["body"], out["body"])
        from .comment_router import classify as _classify
        go = "send_after" in _classify(feedback)          # he said what to write AND "send it": his go-ahead for this rewrite
        asana.comment(t["gid"], (f"Revised based on your comment (picture v{rev} attached). You said send it: moved to Approved, it goes "
                                 f"out at the next send round (8:30am–5:30pm). Drag it to Needs Me to stop it." if go else
                                 f"Revised based on your comment (picture v{rev} attached). Moved to Needs Me: drag to Approved when it's good.")
                      + (f" Listings we have for this agent: {'; '.join(routed)}." if isinstance(routed, list) else ""))
        if go:
            asana.move(t["gid"], a["approved"])
            n["rewritten_and_sent"] = n.get("rewritten_and_sent", 0) + 1
        elif where != nm:           # Rodrigo, 2026-10-27: once he touched it and we worked on it, it is his: Needs Me, never Fresh Leads
            asana.move(t["gid"], nm)
        n["revised"] += 1
        if test_to:
            n["tests_sent"] += int(send_test_copy(cfg, asana, gmail, t["gid"], {**card, "subject": out["subject"], "body": out["body"]}, test_to))
    for t in asana.section_tasks(a["skip"]):
        card = parse_card(t.get("notes") or "")
        if card and conn.execute("UPDATE outreach SET status='skipped' WHERE id=? AND status='pending_approval'", (card["outreach_id"],)).rowcount:
            n["skipped"] += 1
    return n


def sending_paused(cfg: Any) -> bool:
    """Rodrigo, 2026-11-11: "pause everything right now, no more email sending out". One switch (`outreach.sending_paused`)
    stops every email to an agent or broker: approved cards, OM requests, the blast queue, Charles's replies, pass emails.
    Reports and forwards to Rodrigo himself still go. Cards stay where they are and go out when it is switched off."""
    try:
        return bool(cfg.get("outreach.sending_paused", False))
    except Exception:  # noqa: BLE001
        return False


def in_window(cfg: Any, now: datetime | None = None, even_if_paused: bool = False) -> bool:
    if not even_if_paused and sending_paused(cfg):
        return False                             # every sender already treats "outside the window" as "wait"
    w = _cfg(cfg).get("send_window", {})
    tz = ZoneInfo(w.get("timezone", "America/New_York"))
    t = (now or now_utc()).astimezone(tz)
    if t.weekday() not in w.get("days", [0, 1, 2, 3, 4, 5, 6]):
        return False
    start = tuple(int(x) for x in str(w.get("start", "08:30")).split(":"))
    end = tuple(int(x) for x in str(w.get("end", "17:30")).split(":"))
    return start <= (t.hour, t.minute) < end


def send_approved(conn, cfg: Any, asana: Asana, gmail, now: datetime | None = None, jitter_s: int = 0, llm=None) -> dict[str, Any]:
    """Send at most `per_tick` approved emails, only inside the send window and under the daily cap.
    A card in Approved that has new feedback (or a test request) is NOT sent: it goes back to To Review and the
    feedback is applied there, so a comment is never overtaken by a send."""
    from .outreach.send import ensure_thread, log_message
    a = _cfg(cfg)
    res = {"sent": 0, "waiting": 0, "errors": 0, "held_for_feedback": 0}
    from .learning import APPROVED_AS_IS, card_kind, is_control, record_decision
    from . import answers
    from .answers import _warm_aware
    asana = _warm_aware(cfg, asana) if a.get("project") else asana     # a warm-board lead card with "send it" sends from there
    nm = needs_me_section(cfg, asana)

    def _warm_approved():
        out = []
        for t, _ in (asana.lead_cards() if hasattr(asana, "lead_cards") else []):
            pc = parse_card(t.get("notes") or "")
            ans = answers.pending(conn, pc["outreach_id"]) if pc and not pc.get("reply_to") else None
            if ans and ans["state"] == answers.APPROVED:
                out.append(t)
        return out
    approved = [t for t in asana.section_tasks(a["approved"]) if not is_control(t)] + _warm_approved()   # the autopilot switch is not an email
    res["waiting"] = len(approved)
    held, lead_cards = [], []
    for t in list(approved):
        feedback, _ = pending_feedback(asana, t["gid"])
        if not feedback:
            continue
        pc = parse_card(t.get("notes") or "")
        if pc and not pc.get("reply_to") and (conn.execute("SELECT status FROM outreach WHERE id=?", (pc["outreach_id"],)).fetchone()
                                              or [""])[0] == "sent":
            lead_cards.append(t)          # Rodrigo, 2026-10-27 (Richard): a lead card with his answer, dragged here = send that answer
            approved.remove(t)
            continue
        asana.move(t["gid"], needs_revision_section(cfg, asana))
        approved.remove(t)
        held.append(t["gid"])
    if held:
        res["held_for_feedback"] = len(held)
        for gid in held:
            asana.comment(gid, "Held: you left a comment after approving, so this went to Needs Revision. Nothing sends from there. "
                               "Your feedback is being applied; the card moves to Needs Me when the rewrite lands.")
    if held or lead_cards:
        sync_feedback(conn, cfg, asana, llm, gmail)          # apply the comment now, not next tick
        approved = [t for t in asana.section_tasks(a["approved"]) + _warm_approved() if not is_control(t) and t["gid"] not in {h for h in held}]
        # a lead card whose comment was a dictated "send it" is back in Approved with its NEXT EMAIL: it sends now
    if not approved or not in_window(cfg, now):
        return res
    if gmail is None:
        res["errors"] += 1
        log.error("approved emails waiting but Gmail (Charles) is not connected")
        return res
    cap = int(cfg.get("outreach.daily_email_cap", 25))
    sent_today = conn.execute("SELECT COUNT(*) FROM outreach WHERE channel='email' AND status='sent' AND COALESCE(kind,'') NOT LIKE 'om\\_%' ESCAPE '\\' AND substr(sent_at,1,10)=?",
                              (now_utc().strftime("%Y-%m-%d"),)).fetchone()[0]
    if jitter_s:
        time.sleep(random.randint(0, jitter_s))       # vary the send times through the day
    for t in sorted(approved, key=lambda x: x.get("created_at") or "")[: max(0, min(int(a.get("per_tick", 2)), cap - sent_today))]:
        card = parse_card(t.get("notes") or "")
        if not card:
            asana.comment(t["gid"], "Couldn't read this card (the To/Subject/EMAIL lines were changed). Fix them and it will go out.")
            res["errors"] += 1
            continue
        row = db.row_to_dict(conn.execute("SELECT * FROM outreach WHERE id=?", (card["outreach_id"],)).fetchone())
        if not row:
            asana.move(t["gid"], a["sent"])
            continue
        lead_answer = None
        if row["status"] == "sent" and not card.get("reply_to"):
            # ONE CARD PER LEAD (Rodrigo, 2026-11-03): the lead card in Approved sends the NEXT EMAIL on it, in the agent's thread
            lead_answer = answers.pending(conn, row["id"])
            if not lead_answer and answers.hand_written(conn, row, card):
                lead_answer = {"body": card["body"], "note": "", "state": answers.APPROVED}
            if not lead_answer:
                asana.comment(t["gid"], "Nothing to send: this lead's first email already went out and no NEXT EMAIL is drafted on the card. "
                                        "Comment the email you want (\"Answer with: Hey …\" or the text followed by \"send it\"), or "
                                        "tell me what to ask and I'll write it. Moved to Needs Me.")
                asana.move(t["gid"], nm)
                continue
            if card["body"] and _squash_text(card["body"]) == _squash_text(row.get("body") or ""):
                card["body"] = lead_answer["body"]          # the card still shows the first email: send the stored answer
            if not card["subject"].lower().startswith("re:"):
                card["subject"] = answers.answer_subject(row, _props_for(conn, row))
            card["reply_to"] = True
        if db.is_dnc(conn, agent_id=row["agent_id"], email=card["to"]):
            asana.comment(t["gid"], "Not sent: this agent asked not to be contacted.")
            asana.move(t["gid"], a["skip"])
            continue
        try:
            if card.get("reply_to"):      # an answer: same Gmail thread as the email it answers
                ids = gmail.send(card["to"], card["subject"], card["body"], thread_id=row.get("gmail_thread_id"), **({"cc": card["cc"]} if card.get("cc") else {}))
            else:
                ids = gmail.send(card["to"], card["subject"], card["body"], **({"cc": card["cc"]} if card.get("cc") else {}))
        except Exception as e:  # noqa: BLE001
            res["errors"] += 1
            asana.comment(t["gid"], f"Send failed, will retry next round: {str(e)[:200]}")
            continue
        try:      # the trust ladder: was this approved as the engine wrote it, or after Rodrigo's comments/edits?
            if lead_answer:       # one row per decision on the lead card: the first email's row is already there
                cmts = asana.comments(t["gid"])
                fresh = (f"{BOT} NEXT EMAIL is on this card", f"{BOT} Wrote Charles's answer")     # the engine's own draft: what he decided on
                since = max((i for i, c in enumerate(cmts) if (c.get("text") or "").startswith(fresh)), default=-1)
                nth = conn.execute("SELECT COUNT(*) FROM outreach WHERE agent_id=? AND kind LIKE 'reply%' AND status='sent'", (row["agent_id"],)).fetchone()[0]
                record_decision(conn, f"{t['gid']}#answer{nth + 1}", row["id"], "reply", APPROVED_AS_IS, cmts[since + 1:], card["body"], lead_answer["body"])
            else:
                record_decision(conn, t["gid"], row["id"], card_kind(t.get("notes") or ""), APPROVED_AS_IS, asana.comments(t["gid"]),
                                card["body"], _reply_draft(conn, t["gid"]) if card.get("reply_to") else row.get("body"))
        except Exception as e:  # noqa: BLE001 — bookkeeping never blocks a send that already happened
            log.warning("learning: decision for %s not recorded: %s", t["gid"], e)
        with db.tx(conn):
            pids = json.loads(row["property_ids"])
            if card.get("reply_to"):
                conn.execute("INSERT INTO outreach(agent_id,property_ids,channel,kind,status,subject,body,to_address,gmail_thread_id,gmail_message_id,"
                             "run_date,created_at,sent_at,asana_task_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (row["agent_id"], row["property_ids"], "email", "reply:manual", "sent", card["subject"], card["body"], card["to"],
                              ids.get("thread_id") or row.get("gmail_thread_id"), ids.get("message_id"), today_et(), iso(), iso(), t["gid"]))
                tid = ensure_thread(conn, row["agent_id"], "email", ids.get("thread_id") or row.get("gmail_thread_id") or f"local-{row['id']}", pids)
                log_message(conn, tid, "out", card["body"], external_id=ids.get("message_id"), action="reply:manual")
                conn.execute("UPDATE threads SET state=CASE WHEN state IN ('OPEN','ESCALATED','HOT_LEAD') THEN state ELSE 'OPEN' END, updated_at=? WHERE id=?", (iso(), tid))
            else:
                conn.execute("UPDATE outreach SET status='sent', subject=?, body=?, gmail_thread_id=?, gmail_message_id=?, sent_at=? WHERE id=?",
                             (card["subject"], card["body"], ids.get("thread_id"), ids.get("message_id"), iso(), row["id"]))
                tid = ensure_thread(conn, row["agent_id"], "email", ids.get("thread_id") or f"local-{row['id']}", pids)
                log_message(conn, tid, "out", card["body"], external_id=ids.get("message_id"))
        local = (now or now_utc()).astimezone(ZoneInfo(_cfg(cfg).get("send_window", {}).get("timezone", "America/New_York")))
        if lead_answer:
            asana.comment(t["gid"], f"📤 Sent to {card['to']} {local.strftime('%a %b %d, %I:%M %p')} from Charles"
                                    f"{' (Cc ' + ', '.join(card['cc']) + ')' if card.get('cc') else ''}:\n\n{card['body']}")
            try:
                answers.clear(conn, cfg, asana, row["id"], t["gid"], t.get("notes") or "")
            except Exception as e:  # noqa: BLE001
                log.warning("clearing the answer on lead card %s: %s", t["gid"], e)
            asana.move(t["gid"], a["sent"])          # the lead board files it (waiting on the agent) next round
            res["sent"] += 1
            continue
        asana.comment(t["gid"], f"Sent {local.strftime('%a %b %d, %I:%M %p')} from {cfg.secret('GMAIL_SENDER') or 'Charles'}.")
        asana.move(t["gid"], a["sent"])
        if card.get("reply_to") and row.get("asana_task_id") and row["asana_task_id"] != t["gid"]:
            # Rodrigo, 2026-10-27: "there's two cards for Lise". Once an answer has gone out, the lead's own card carries it
            # and the answer card is closed (completed: off the board, still in search).
            try:
                asana.comment(row["asana_task_id"], f"📤 Sent to {card['to']} {local.strftime('%a %b %d, %I:%M %p')} from Charles"
                                                    f"{' (Cc ' + ', '.join(card['cc']) + ')' if card.get('cc') else ''}:\n\n{card['body']}")
                asana.complete(t["gid"])
            except Exception as e:  # noqa: BLE001
                log.warning("closing reply card %s: %s", t["gid"], e)
        res["sent"] += 1
    if res["sent"]:
        from .offers import sync_offers
        res["offers_logged"] = sync_offers(conn, cfg)      # an offer that just went out lands in the offer log
    return res


def _props_for(conn, row: dict[str, Any]) -> list[dict[str, Any]]:
    pids = json.loads(row.get("property_ids") or "[]")
    return [db.row_to_dict(r) for r in conn.execute(
        f"SELECT p.*, v.lane, v.mao, v.arv, v.arv_method, v.comp_count, v.repair_cost, v.repair_tier, v.comp_grade FROM properties p "
        f"LEFT JOIN valuations v ON v.property_id=p.id WHERE p.id IN ({','.join('?' * len(pids)) or 'NULL'})", pids)]


def rewrite_pending(conn, cfg: Any, asana: Asana, llm, only_old_style: bool = True, violating_only: bool = False,
                    limit: int | None = None) -> dict[str, int]:
    """Regenerate emails still waiting for approval with the current writer and refresh their cards (text, picture, comment).
    By default only cards written before the protocol (no stored analysis); all=True redoes every To Review card.
    violating_only=True (every approvals tick, Rodrigo 2026-10-27): only cards whose text breaks the photo rule (asks how much work
    a listing with 4+ photos needs) or the direct-tone rule (2026-10-28), so the board fixes itself without his computer. Approved/sent cards are left alone."""
    a = _cfg(cfg)
    n = {"rewritten": 0, "failed": 0, "skipped_already_protocol": 0, "left_to_feedback": 0, "card_deleted": 0}
    rows = [db.row_to_dict(r) for r in conn.execute(
        "SELECT * FROM outreach WHERE status='pending_approval' AND asana_task_id IS NOT NULL ORDER BY id").fetchall()]
    for row in rows:
        if limit is not None and n["rewritten"] + n["failed"] >= limit:
            break
        if violating_only:
            body_now = row.get("body") or ""
            photo_bad = bool(photo_rule_problems(body_now, _props_for(conn, row)))
            if not photo_bad and not tone_problems(body_now):     # Rodrigo, 2026-10-28: AI-ish wording is rewritten too
                continue
        elif only_old_style and (row.get("analysis") or "").strip() not in ("", "{}"):
            n["skipped_already_protocol"] += 1
            continue
        try:
            feedback, comments = pending_feedback(asana, row["asana_task_id"])
        except AsanaError as e:                      # card deleted on the board: nothing to rewrite, don't send it either
            log.warning("card for outreach %s is gone (%s); marking skipped", row["id"], e)
            conn.execute("UPDATE outreach SET status='skipped' WHERE id=?", (row["id"],))
            from .learning import SKIPPED, record_decision
            record_decision(conn, row["asana_task_id"], row["id"], "first_touch", SKIPPED, [])
            n["card_deleted"] += 1
            continue
        if feedback or any((c.get("text") or "").startswith(f"{BOT} Revised based on your comment") for c in comments):
            n["left_to_feedback"] += 1              # Rodrigo's own feedback shapes this one; sync_feedback owns it
            continue
        agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (row["agent_id"],)).fetchone()) or {}
        pids = json.loads(row["property_ids"] or "[]")
        props = _props_for(conn, row)
        if not props:
            continue
        props = props[: int(cfg.get("outreach.max_properties_per_email", 2))]
        try:
            d = compose_email(llm, cfg, agent, props, seed_for(agent["id"], pids, today_et()))
        except Exception as e:  # noqa: BLE001
            n["failed"] += 1
            log.error("rewrite of outreach %s failed: %s", row["id"], e)
            continue
        conn.execute("UPDATE outreach SET subject=?, body=?, draft_body=?, variant=?, analysis=? WHERE id=?",
                     (d["subject"], d["body"], d["body"], d["variant"], json.dumps(d.get("analysis") or {}), row["id"]))
        asana.update_notes(row["asana_task_id"], card_notes(row["to_address"], d["subject"], d["body"], row["id"],
                                                             listing_meta(props[0], agent, d.get("analysis"))))
        _attach_picture(cfg, asana, row["asana_task_id"], row["to_address"], d["subject"], d["body"], row["id"], rev=99)
        if violating_only and not photo_bad:
            asana.comment(row["asana_task_id"], "Rewritten to be direct (Rodrigo, 2026-10-28): straight to the question, no \"doesn't scare "
                                                "us\", no reciting the listing's upgrades back. New picture attached.")
        elif violating_only:
            photos = ", ".join(str(p.get("photos_count")) if p.get("photos_count") is not None else "an unknown number of" for p in props)
            asana.comment(row["asana_task_id"], f"Rewritten: the listing has {photos} photos, so the email no longer asks how much work "
                                                "it needs (Rodrigo, 2026-10-27). It asks about what the pictures can't show instead. New picture attached.")
        else:
            asana.comment(row["asana_task_id"], "Rewritten under the new outreach protocol (think first, no listing echo). New picture attached.")
        n["rewritten"] += 1
    return n
