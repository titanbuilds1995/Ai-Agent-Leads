"""What Rodrigo's comment on an approval card asks for (Rodrigo, 2026-10-26: "why would it not give me what I am asking?").

Before this, every comment was treated as wording for a rewrite, so "comp this and tell me the ARV" produced a rewritten
email and no numbers, and "this is a really good email, send it" rewrote an email he liked. Now each comment is sorted:

- numbers:  comps / ARV / exit / land value / new construction / cash flow / "where we need to be"
            → numbers snapshot (titan/comps_report.py) as a comment ON THE CARD HE ASKED ON; the email is held until he decides.
            Never a new card: a NUMBERS card on the Claude Updates board was a second place to look (Rodrigo, 2026-10-27,
            Richard Dipietro: "we are still creating a second card when I ask a question ... it's confusing").
- send:     "send it", "I approve this", "really good email" (with nothing to change) → straight to Approved, text untouched.
- call:     "mark him hot", "call tomorrow", "follow up" → lead marked, a call card for tomorrow in Needs Rodrigo.
- check:    "double check that X is their listing" → the agent's listings we know are put in front of the rewrite and on the card.
- rewrite:  everything else (wording), as before. "...and send it" after a wording ask (send_after) = rewrite, then Approved.
Numbers always win over send: "send it ... actually comp it before we send" holds the email."""
from __future__ import annotations

import re
from datetime import timedelta
from zoneinfo import ZoneInfo
from typing import Any

from . import db
from .util import log, now_utc

NUMBERS_RE = re.compile(
    r"\bcomps?\b|\bcomp(?:ed|ing)?\s+(?:it|this|that)|comparab|\barv\b|after[- ]?re?pair|resale value|what (?:it|this|we|they|homes?|"
    r"houses?|properties|products?)[\w ]{0,20}(?:could|would|can|are)\s+(?:re)?sell|sell (?:it )?for\b|land value|for (?:the )?land\b|"
    r"new construction|new build|run (?:the )?numbers|numbers (?:look|on)|what the numbers|cash ?flow|cash cow|subfloor|"
    r"(?:good|an?) offer (?:number|for)|offer or a range|where we (?:would |need to )?(?:need to )?be|how (?:far|close) off|\bexit\b", re.I)
SEND_RE = re.compile(
    r"\b(?:send (?:it|this|him|her|them)(?: the email)?|send(?:ing)? the email|go ahead and send|i approve(?: this| it)?|"
    r"(?:really |very )?good email|looks good|good to go|ready to send|ship it)\b|^\s*(?:ok(?:ay)?,? )?approved?[.! ]*$", re.I)
# Allowlist (reviewer round 2): a comment sends only when, after removing the go-ahead phrases and his usual hot / call /
# follow-up phrases, NOTHING but filler words is left. Any other word ("to Lisa", "minus the last line", "scrap that",
# "via text", "wrong agent") means it is not a plain go-ahead, so the card is held. Never default to send.
_GO_AHEAD = [
    r"send (?:it|this|him|her|them)(?: the email| the emails?| out)?(?: now| asap| as is| as written| please)*",
    r"send(?:ing)? the email", r"go ahead(?: and send(?: it)?)?", r"(?:approve and )?send(?= *[.!]* *$)", r"i approve(?: this| it)?(?: email)?", r"approved?",
    r"(?:this is |it'?s |that'?s )?(?:a )?(?:really |very |super )?good email", r"(?:this |it )?looks? (?:really )?(?:good|great)",
    r"good to go", r"ready to send", r"ship it", r"i (?:actually )?(?:really )?(?:like|love) (?:it|this(?: one)?)",
]
_CALL_PHRASES = [
    r"(?:i want (?:us|you) to )?stay on top of (?:this|the) lead", r"mark (?:him|her|them|it|this)? ?(?:as )?hot",
    r"(?:cuz|cause|because) i think (?:this|it|he|she) (?:could|might) be a hot (?:lead|potential|one)",
    r"follow ?up with (?:him|her|them) if (?:you|we) don'?t hear (?:back )?from (?:him|her|them)(?: by (?:the )?end of (?:the )?day| today| by tomorrow)?",
    r"(?:and )?(?:then )?put (?:it|him|her|them) on my (?:on my )?boards? to call(?: (?:him|her|them))? tomorrow",
    r"call (?:him|her|them) tomorrow", r"after you send (?:them|him|her) the emails?",
]
_FILLER = {"hey", "so", "and", "then", "please", "now", "asap", "great", "thanks", "perfect", "nice", "cool", "ok",
           "okay", "yes", "yep", "sure", "it", "this", "one", "i", "really", "very", "a", "the", "just", "go", "ahead",
           "as", "is", "written", "rodrigo"}
GO_RE = re.compile(r"\b(?:" + "|".join(_GO_AHEAD) + r")\b", re.I)
CALLP_RE = re.compile(r"\b(?:" + "|".join(_CALL_PHRASES) + r")\b", re.I)
PRAISE = {"great", "perfect", "love", "awesome", "excellent", "beautiful", "good", "go", "thank", "you", "nice", "solid"}
EMOJI_OK_RE = re.compile(r"^\s*(?:👍|✅|💯|🔥)+\s*$")


def plain_go_ahead(text: str) -> bool:
    """True only for 'send it' / 'looks good' / 'I approve this' (+ his hot/call phrases) with nothing else left."""
    if EMOJI_OK_RE.match(text):
        return True
    if not GO_RE.search(text):
        return False
    rest = GO_RE.sub(" ", CALLP_RE.sub(" ", text.lower()))      # his call phrases first: they contain 'send them'
    rest = re.sub(r"[👍✅💯🔥]|\bthank you\b", " ", rest)
    if re.search(r"[^a-z0-9'\s.,!]", rest):          # any other symbol or emoji left (❌ 🛑 👎 🤔 - ?) is not a go-ahead
        return False
    words = re.findall(r"[a-z0-9']+", rest)
    return not [w for w in words if w not in _FILLER]


CHANGE_RE = re.compile(r"\b(?:change|take out|remove|instead|don'?t say|do not say|reword|replace|add (?:a|that|the)|shorter|"
                       r"too (?:long|robotic|formal)|rewrite|should say|let'?s say|mention|cc|attach|translate|spanish|offer)\b", re.I)
CALL_RE = re.compile(r"\b(?:mark (?:him|her|them|it|this)?\s*(?:as )?hot|hot (?:lead|potential)|call (?:him|her|them)?\s*tomorrow|"
                     r"to call tomorrow|call list|follow[- ]?up with (?:him|her|them)|stay on top of)\b", re.I)
NEG_BEFORE_RE = re.compile(r"\b(?:don'?t|do not|not|never|no need to)\s+(?:\w+\s+){0,2}$", re.I)
CHECK_RE = re.compile(r"\b(?:double[- ]?check|confirm (?:that|on|it|this)|is (?:that|this) (?:their|his|her) listing|"
                      r"incongruen|make sure (?:that )?(?:it|this|that) is)\b", re.I)


# "Rewrite it like this and send it" (Rodrigo, 2026-10-27: "I tell it what to write, it rewrites it, it already has my tone,
# so it can just send"). The rewrite goes straight to Approved, but only when the comment both says what to write AND ends
# with a plain send. Anything that makes the send conditional, later, elsewhere, or about a number keeps the old flow
# (rewrite → To Review for his look).
SEND_AFTER_RE = re.compile(r"(?:^|[,.;!]|\band\b|\bthen\b)\s*(?:and\s+)?(?:then\s+)?(?:go ahead and\s+)?(?:send (?:it|this|him|her|them)(?: the email| out| over)?|"
                           r"send(?:ing)? the email)(?:\s+(?:now|please|asap|out|over|right away))*\s*[.!]*\s*$", re.I)
WRITE_RE = re.compile(r"\b(?:say|tell|ask|make|add|write|put|mention|use|change|take out|remove|keep|start|open|end|cut|drop|"
                      r"swap|switch|shorten|replace|reword|rewrite|instead|let (?:him|her|them) know)\b", re.I)
SEND_BLOCK_RE = re.compile(r"\b(?:don'?t|do not|never|not|no need|hold|wait|until|after|when|if|once|unless|tomorrow|later|next|"
                           r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|tonight|morning|afternoon|\d{1,2}\s?(?:am|pm)|"
                           r"actually|jk|lol|lmao|nope|scrap|never ?mind|skip|dnc|wrong|via|text|cc|attach|offer|price|counter|"
                           r"also send|send (?:it|this|him|her|them) to)\b|\$|\b\d+(?:[.,]\d+)?\s?k\b|\b\d{3},\d{3}\b|\?|[❌🛑🚫✋👎🤔]", re.I)


def send_after_rewrite(feedback: list[str]) -> bool:
    """True when the last comment says what to write and ends with a plain 'send it' (nothing conditional or about a number)."""
    if not feedback:
        return False
    last = feedback[-1].strip()
    m = SEND_AFTER_RE.search(last)
    if not m:
        return False
    ask = last[:m.start()]
    return bool(WRITE_RE.search(ask)) and not SEND_BLOCK_RE.search(" ".join(feedback))


def _positive(rx: re.Pattern, text: str) -> bool:
    """A match that is not negated just before it ("don't follow up", "do not mark him hot")."""
    return any(not NEG_BEFORE_RE.search(text[max(0, m.start() - 40):m.start()]) for m in rx.finditer(text))


def classify(feedback: list[str]) -> set[str]:
    """numbers / send / call / check / rewrite / hold. 'send' only for a plain go-ahead; a send with any condition, question,
    negation, number or extra ask becomes 'hold' (nothing moves) or 'rewrite' (when it also asks for a change)."""
    text = " ".join(feedback)
    out: set[str] = set()
    if NUMBERS_RE.search(text):
        out.add("numbers")
    if _positive(CALL_RE, text):
        out.add("call")
    if CHECK_RE.search(text):
        out.add("check")
    change = bool(CHANGE_RE.search(text))
    if plain_go_ahead(text) and not out & {"numbers", "check"}:
        out.add("send")
    elif (SEND_RE.search(text) or EMOJI_OK_RE.match(text) or re.search(r"\bsend\b|\b(?:don'?t|do not) (?:send|approve|follow|mark)|not approved|"
          r"disapprove|\bhold\b|\bwait\b|\bskip\b|[❌🛑🚫✋👎]", text, re.I) or GO_RE.search(text) or "?" in text) and not change \
            and not out & {"numbers", "check"}:
        out.add("hold")        # a go-ahead with anything else in it, a no, a wait or a bare question: nothing moves
    if not out and not change and set(re.findall(r"[a-z']+", text.lower())) <= _FILLER | PRAISE:
        out.add("hold")        # plain praise ("perfect", "great, thanks"): don't spend a rewrite on an email he liked
    if send_after_rewrite(feedback) and not out & {"numbers", "check"}:
        out.discard("hold")
        out |= {"rewrite", "send_after"}      # rewrite from his words, then straight to Approved
    if change or not out:
        out.add("rewrite")
    return out


def card_props(conn, outreach_id: int) -> list[dict[str, Any]]:
    import json
    row = conn.execute("SELECT property_ids FROM outreach WHERE id=?", (outreach_id,)).fetchone()
    ids = json.loads(row[0] or "[]") if row else []
    return [db.row_to_dict(r) for r in conn.execute(f"SELECT * FROM properties WHERE id IN ({','.join('?' * len(ids)) or 'NULL'})", ids)]


def pick_subjects(props: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    """The house(s) the comment is about: the ones whose street number / name it mentions, else all of the card's."""
    hit = []
    for p in props:
        street = (p.get("street") or "").lower()
        num = street.split(" ")[0] if street else ""
        words = [w for w in re.findall(r"[a-z]{4,}", street) if w not in ("street", "avenue", "drive", "court", "place", "terrace")]
        if (num and re.search(rf"\b{re.escape(num)}\b", text)) or any(re.search(rf"\b{re.escape(w)}\b", text, re.I) for w in words):
            hit.append(p)
    return hit or props


def agent_listings(conn, outreach_id: int) -> list[str]:
    row = conn.execute("SELECT agent_id FROM outreach WHERE id=?", (outreach_id,)).fetchone()
    if not row or not row[0]:
        return []
    return [f"{r['street']}, {r['city']} ({'$' + format(r['list_price'], ',') if r['list_price'] else '?'})"
            for r in conn.execute("SELECT street, city, list_price FROM properties WHERE agent_id=? AND lead_status != 'EXCLUDED' "
                                  "ORDER BY list_price DESC", (row[0],)).fetchall()]


def numbers_snapshot(conn, cfg: Any, asana, card_gid: str, card: dict[str, Any], feedback: list[str], comps_fn,
                     tail: str | None = None) -> int:
    """Build the snapshot for each house asked about and post it as a comment on the card he asked on (no new card anywhere);
    save the text under reports/comps/. Returns how many were posted."""
    from . import comps_report
    text = " ".join(feedback)
    props = card_props(conn, card["outreach_id"])
    subjects = pick_subjects(props, text)
    own = len(subjects) == 1          # a rent figure in his comment belongs to the house he named (or the card's only house)
    snaps = []
    for p in subjects:                # build every house first: post nothing half-done
        snaps.append((p, comps_report.build(conn, cfg, p["id"], comps_fn, asked=text, outreach_id=card["outreach_id"],
                                            rent_from_comment=own)))
    for p, s in snaps:
        out_dir = cfg.path("reports") / "comps"
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", f"{p['street']} {p['city']}".lower()).strip("-")
        (out_dir / f"{slug}.md").write_text(comps_report.to_text(s))
        asana.comment_html(card_gid, comps_report.to_comment(s, tail))
    return len(snaps)


CALL_NOTE = "HOT (Rodrigo): call tomorrow"


def call_card(conn, cfg: Any, asana, card: dict[str, Any], feedback: list[str]) -> bool:
    """Lead marked HOT for a call, one card in Needs Rodrigo for tomorrow with the agent's phone and his note."""
    a = cfg.get("outreach.asana", {}) or {}
    row = conn.execute("SELECT a.name, a.phone, a.email FROM outreach o LEFT JOIN agents a ON a.id=o.agent_id WHERE o.id=?",
                       (card["outreach_id"],)).fetchone()
    import hashlib
    props = card_props(conn, card["outreach_id"])
    asks = [f for f in feedback if _positive(CALL_RE, f)] or feedback      # only the call ask: a later unrelated comment doesn't re-book
    key = hashlib.sha1(f"{card.get('outreach_id')}|{' '.join(asks)}".encode()).hexdigest()
    if conn.execute("SELECT 1 FROM call_bookings WHERE key=?", (key,)).fetchone():
        return False          # this comment already booked its call (it is being retried after a failed snapshot): one card only
    for p in props:
        db.set_lead_status(conn, p["id"], "NEEDS_CALL", CALL_NOTE)
    if not (a.get("updates_project") and a.get("needs_rodrigo")):
        return False
    day = (now_utc().astimezone(ZoneInfo("America/New_York")) + timedelta(days=1)).strftime("%a %b %d")
    name, phone = (row["name"] if row else None) or "agent", (row["phone"] if row else None) or "no phone on file"
    houses = "; ".join(f"{p['street']}, {p['city']}" for p in props)
    try:
        asana.create_task(a["updates_project"], a["needs_rodrigo"], f"📞 Call {day}: {name} · {phone} · {houses}",
                          f"HOT lead (your comment): {' '.join(feedback)[:600]}\n\nAgent: {name} · {phone} · {(row['email'] if row else '') or ''}\n"
                          f"Houses: {houses}\n\nThe email follows the normal flow on the Email Approvals board. An automatic "
                          "same-day follow-up email isn't built yet: call, or comment 'follow up' on the card and Claude drafts one.")
        conn.execute("INSERT OR IGNORE INTO call_bookings(key, created_at) VALUES(?, ?)", (key, db.iso()))   # only once the card exists
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("call card: %s", e)
        return False
