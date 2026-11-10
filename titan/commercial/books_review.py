"""Read every broker answer and every document for Rodrigo (2026-11-09: "I need everything to be read for me").

After `poll_om_replies` mirrors an answer on the 1031 card and files the attachments, this module reads them: the broker's
words, every file in data/listings/<listing_id>/ (OM PDFs, income-statement spreadsheets, rent rolls), the listing's own
numbers, and writes ONE plain comment on the card:

    🧠 READ FOR YOU · verdict
    What they said · What the books say · Numbers · Red flags · Next step (+ Charles's reply when one is called for)

Claude writes it on the paid API like the other card work; a rules-only summary when Claude is unreachable (retried next round).
One review per inbound message batch (`commercial_reviews`); `review_pending` runs on every check-replies / approvals round and
catches the answers that came in before this existed. Books on file that the poll missed are regraded here too and the card is
moved to "Books received". A broker who says leased / sold / under contract moves the card to "Not a fit".

Rodrigo, 2026-11-09 evening ("has Charles actually answered any of the agents that were simple answers? … forward me the
emails that need me … the ones that do not fit, archive them … agents sent us deals on pocket listings"):
- `send_approved_replies`: with `commercial.review.auto_reply` the simple replies (thanks + what we still need, a polite pass)
  go out on their own at the next round; a review marked NEEDS YOU still waits for his "send it"; "hold" under a review stops one.
- `triage_inbox`: a review that needs him, a FITS / LEANS FIT, or a broker who pitched other (pocket / off-market) deals is
  FORWARDED to REPORT_TO with the broker's words, the read and the files; NOT A FIT / DOES NOT FIT threads are ARCHIVED in
  Charles's inbox once Charles's reply (if any) has gone. Both once per review, recorded in `commercial_reviews`.

Rodrigo, 2026-11-10 ("I want us to be faster with our communication … so I don't have to handle that work load in the email and
focus just on analyzing deals"): three fixed replies in his words go out on their own (`template_kind`, once each per thread):
- books_ack: the broker sent the books (attached or linked) → thanks, the underwriting team is on it, answer within 24 hours;
- ask_books: the broker answered without the books and without a question → please send the books, we analyze off the books;
- pass: NOT A FIT / DOES NOT FIT with nothing else pitched → this one won't work, we are always buying, send us pocket listings,
  distressed property, apartments; capital ready, other partners looking.
Questions, NDAs and pocket deals still get Claude's draft (and NEEDS YOU waits for him). A thread Rodrigo wrote in himself
(`commercial_threads.rodrigo_at`, set by `poll_om_replies`) is his: nothing auto-sends there and it is never archived.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .. import db
from ..util import iso, log, money, now_utc
from . import documents
from .board import NEEDS_ME, STAYS_PUT, ensure_board
from .outreach import BOOK_KINDS, regrade, verdict_text

LINK_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
DOC_HOST_RE = re.compile(r"dropbox|drive\.google|docs\.google|box\.com|sharefile|wetransfer|onedrive|sharepoint|dealroom|buildout|crexi|loopnet\.com/.*(?:document|brochure|om)|\.pdf", re.I)
DEAD_RE = re.compile(r"(?:just |already |recently )?(?:signed a lease|leased|under contract|sold|off (?:the )?market|no longer (?:available|for sale)|"
                     r"accepted an offer|in escrow|withdrawn)", re.I)
DISCLAIMER_RE = re.compile(r"(?:confidentiality notice|disclaimer|this (?:e-?mail|message) (?:message )?(?:is intended|and any attachments|may contain))[\s\S]*", re.I)


def _no_disclaimer(text: str) -> str:
    """The broker's words without the legal footer ("CONFIDENTIALITY NOTICE and DISCLAIMER: ..."), which is not an NDA request."""
    return DISCLAIMER_RE.sub("", text or "")


NDA_RE = re.compile(r"\b(?:nda|ca/nda|confidentiality agreement|confidentiality|non-?disclosure)\b", re.I)
POF_RE = re.compile(r"proof of funds|\bpof\b", re.I)
LATER_RE = re.compile(r"later today|tomorrow|when i(?:'m| am) back|next week|give me a day|will send|i'll send|i’ll send|shortly", re.I)
OFFER_RE = re.compile(r"ready to review offers|open to offers|bring (?:an|your) offer|make an offer|owner is negotiable|negotiable|motivated", re.I)
PRICE_RE = re.compile(r"\$\s?([\d,]{7,}(?:\.\d+)?)")
CAP_RE = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s?%?\s?cap", re.I)
POCKET_RE = re.compile(r"pocket listing|off[- ]market|not (?:yet )?(?:listed|on the market)|i (?:also )?have (?:another|other|a few|some|two|three)|"
                       r"other (?:propert|listing|deal|package|site|asset)|another (?:propert|listing|deal|asset)|instead,? (?:take a look|consider)", re.I)
HOLD_RE = re.compile(r"\b(?:hold(?: it| this| off)?|wait|don'?t send|do not send|not yet|stop|let me (?:look|think|review))\b", re.I)
QUESTION_RE = re.compile(r"\?|\b(?:can you|could you|would you|do you|are you|is (?:your|the) (?:buyer|partner|client)|what (?:is|are|kind)|"
                         r"when (?:do|can|will|would)|how (?:soon|much|fast|quickly)|let me know (?:if|what|when)|please (?:confirm|advise))\b", re.I)
AUTO_KINDS = ("books_ack", "ask_books", "pass")
# Rodrigo, 2026-11-10 (Adriana): "let's not have us answer or say anything that has dates involved, or committing us to do anything
# like sending anything signed or POF." Any drafted reply that does goes nowhere on its own: it waits for his "send it".
COMMIT_RE = re.compile(
    r"\b(?:\d+\s*(?:business\s+|calendar\s+)?(?:days?|weeks?|months?)|within|deadline|timeline|timeframe|time frame|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|tonight|next week|this week|end of (?:the )?(?:day|week|month)|"
    r"january|february|april|june|july|august|september|october|november|december|"
    r"identif(?:y|ication)|clos(?:e|ing) (?:date|by|in|on|within)|"
    r"proof of funds|\bpof\b|sign(?:ed|ing)?\b|nda|confidentiality|non-?disclosure|\bloi\b|letter of intent|contract|"
    r"deposit|escrow|earnest|we(?:'ll| will| can| would) (?:send|provide|sign|get you|have)|i(?:'ll| will) (?:send|provide|sign|get you)|"
    # Charles never attaches anything: a draft that says it did is false (St Petersburg, 11-10: "The signed NDA is attached", twice, with nothing attached)
    r"attach(?:ed|ing)?|enclosed|see below|reviewing (?:the|your) (?:p&ls?|books|numbers|financials) now|"
    # Charles only sees the broker's thread, not the rest of the inbox: Box / Dropbox / DocuSign invitations arrive as their own
    # emails, so he can never say something did not arrive (Julio, 11-10: "We haven't received the Box invitation yet" when it had)
    r"(?:have(?:n't| not)|has(?:n't| not)|didn't|did not|never|not yet|yet to) (?:been )?(?:receiv\w*|get|got|gotten|come through|arriv\w*|show\w* up|see\w*)|"
    r"(?:didn't|did not|doesn't|does not) (?:come|go) through|not received|re-?send|missing on our end|on our end)", re.I)


def commits_us(draft: str) -> list[str]:
    """The dates / commitments in a drafted reply (signature lines ignored)."""
    body = "\n".join(ln for ln in (draft or "").splitlines() if not re.match(r"\s*(?:Charles Grant|Junior Acquisitions|Titan Realty|Office Line)", ln))
    return sorted({m.group(0).lower() for m in COMMIT_RE.finditer(body)})
FORWARD_VERDICTS = ("FITS", "LEANS FIT")
ARCHIVE_VERDICTS = ("NOT A FIT", "DOES NOT FIT")

VERDICTS = ("FITS", "LEANS FIT", "CAN'T TELL YET", "DOES NOT FIT", "NOT A FIT")

SYSTEM = """You are the acquisitions analyst at Titan Realty Investments, a Florida real estate investment company. Rodrigo
Gayoso (principal) is placing a 1031 exchange with a partner: the box is $3.5M to $8M, an EIGHT PERCENT CAP OR BETTER on real,
trailing numbers (not pro forma), existing cash-flowing commercial property in Florida (NNN / retail / hotel / industrial /
office / multifamily 5+ / medical). Charles Grant, junior acquisitions agent, emailed the broker asking for the trailing twelve,
the rent roll and the offering memorandum. You read what the broker answered and every document they sent, and you write it up
for Rodrigo, who is busy and not an analyst: plain words, short, numbers only when they matter, no hedging, no fluff.

Rules:
- Verdict is one of: FITS (real NOI at ask clears 8%, docs back it), LEANS FIT (close, one thing to confirm), CAN'T TELL YET
  (no books yet / gated), DOES NOT FIT (the real numbers miss the 8 cap or the price is out of the box), NOT A FIT (leased, sold,
  under contract, off market, wrong asset).
- Pro forma, "projected", "potential" or "upon stabilization" numbers are NOT real numbers: say so.
- Back-solve: NOI from the documents ÷ asking price = the real cap. A broker's stated cap that the documents don't support is a red flag.
- If the broker mentions a price cut, a new cap or seller motivation, that goes in "numbers" / "what they said".
- Red flags: expenses missing (taxes, insurance, management, reserves), reassessment risk on taxes after a sale, short remaining
  lease term, single tenant with no credit, occupancy gaps, numbers that don't tie between documents, marketing OM older than the
  current rent, anything that reads as cooked.
- Next step is ONE concrete thing. If it is Rodrigo's call (sign an NDA/CA, send proof of funds, decide on a number, call the
  broker), say "NEEDS YOU:" first. Otherwise it is Charles's move.
- Where it has to be for a TRUE 8 cap (Rodrigo, 2026-11-10): state your assumptions and the result, one short line each: the NOI you
  used and where it came from (T-12 / OM / listing; note what you added back or took out), the property tax after a sale (Florida
  reassesses at the sale: taxable value about 85% of the price times the county's all-in millage, about 17 to 22 mills; say the
  number you used), any management fee or reserve a passive owner would carry that the books leave out, and the price that makes
  it an 8.0% cap after all of that: price = (NOI before property tax - added costs) / (0.08 + 0.85 x millage). Compare that price
  with the ask.
- Pros and cons: the real ones for a 1031 owner, short.
- What you do not believe: what in their numbers or story looks inflated, left out or untrue (pro forma dressed as actual,
  missing expenses, a cap that does not tie to the books, a trend they leave out), and why.
- What checks out: what ties across the documents and we can rely on.
- Other deals: when the broker pitches ANOTHER property instead of or besides the one we asked about (a pocket listing, an
  off-market deal, "I also have…", a package of sites), list each one in other_deals as one short line (what · where · price ·
  cap if stated · one fact). Empty when none. Rodrigo wants to see these.
- Charles's reply: write it ONLY when the broker is waiting on something Charles can answer without Rodrigo (thanks + what we
  still need that they have NOT already sent; read the attachments list before asking for anything). Never a price, never
  "our buyer"/"client"/"investor" (say "a partner of ours"), never promise to waive anything.
- Charles sees ONLY this thread. Never say something did not arrive, was not received or did not come through, and never ask
  them to resend: invitations (Box, Dropbox, DocuSign) come as separate emails he cannot see, and the files may be on the card.
- NEVER put a date, deadline or timeframe in Charles's reply (no "45 days", no closing or identification timing, no "this week",
  no "by Friday"), and NEVER commit us to do or send anything (no proof of funds, no signing an NDA / CA / LOI / contract, no
  deposit, no "we will send", "we can provide"). Rodrigo, 2026-11-10: those are his call. When the broker asks for any of that
  (our deadlines, proof of funds, a signed NDA, an LOI), the next step is "NEEDS YOU:" and the reply is either empty or only says
  thanks and that we will come back to them, with no date. 30 to 80 words, starts "Hey <first name>,", ends with the signature lines exactly:
  Charles Grant / Junior Acquisitions Agent / Titan Realty Investments / Office Line: +1 (561) 405 7379 (each on its own line).
  Empty string when no reply is due (broker will send later, said no, or Rodrigo must act first)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "what_they_said": {"type": "string", "description": "1-2 sentences, the broker's answer in plain words"},
        "books_say": {"type": "string", "description": "2-4 sentences on what the documents show; 'No documents yet.' when none"},
        "numbers": {"type": "array", "items": {"type": "string"}, "description": "short lines like 'NOI (T-12) $314,638' or 'Real cap at ask 8.25%'"},
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "next_step": {"type": "string"},
        "needs_rodrigo": {"type": "boolean"},
        "reply_draft": {"type": "string"},
        "other_deals": {"type": "array", "items": {"type": "string"}, "description": "other / pocket / off-market deals the broker offered, one line each; [] when none"},
        "eight_cap": {"type": "array", "items": {"type": "string"}, "description": "assumption lines then the result: 'NOI used $X (T-12)', 'Tax after a sale ~$Y (85% x Z mills)', 'Added: 15% manager $W', 'True 8 cap at $P (ask $A)'"},
        "pros": {"type": "array", "items": {"type": "string"}},
        "cons": {"type": "array", "items": {"type": "string"}},
        "doubts": {"type": "array", "items": {"type": "string"}, "description": "what looks inflated, left out or untrue, and why"},
        "believe": {"type": "array", "items": {"type": "string"}, "description": "what ties across the documents and we can rely on"},
    },
    "required": ["verdict", "what_they_said", "books_say", "numbers", "red_flags", "next_step", "needs_rodrigo", "reply_draft", "other_deals",
                 "eight_cap", "pros", "cons", "doubts", "believe"],
    "additionalProperties": False,
}


def pending(conn, limit: int = 20) -> list[dict[str, Any]]:
    """Threads whose latest inbound message has no review yet (oldest first)."""
    rows = conn.execute("""
        SELECT t.*, m.gmail_message_id AS last_msg_id, m.created_at AS last_msg_at
        FROM commercial_threads t
        JOIN commercial_messages m ON m.id = (SELECT id FROM commercial_messages WHERE thread_id=t.id ORDER BY created_at DESC, id DESC LIMIT 1)
        WHERE t.bounced=0 AND t.state NOT IN ('CLOSED','BOUNCED') AND t.reply_count > 0
          AND NOT EXISTS (SELECT 1 FROM commercial_reviews r WHERE r.thread_id=t.id AND r.gmail_message_id=m.gmail_message_id)
        ORDER BY m.created_at ASC LIMIT ?""", (limit,)).fetchall()
    return [db.row_to_dict(r) for r in rows]


def _listing(conn, listing_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM commercial_listings WHERE listing_id=?", (listing_id,)).fetchone()
    if not row:
        return {"listing_id": listing_id}
    row = db.row_to_dict(row)
    L = json.loads(row.get("data") or "{}")
    L.update({k: row.get(k) for k in ("board_column", "tag", "evidence_grade", "stated_noi", "back_solved_cap", "offer_anchor", "score") if row.get(k) is not None})
    L["listing_id"] = listing_id
    return L


def _messages(conn, thread_id: int) -> list[dict[str, Any]]:
    return [db.row_to_dict(r) for r in conn.execute("SELECT * FROM commercial_messages WHERE thread_id=? ORDER BY created_at, id", (thread_id,)).fetchall()]


def _files(cfg: Any, listing_id: str) -> list[dict[str, Any]]:
    folder = cfg.root / cfg.get("commercial.documents.dir", "data/listings") / str(listing_id)
    out = []
    for d in documents.local_files(folder):
        text = documents.doc_text(Path(d["path"]))
        out.append({**d, "text": text})
    return out


def doc_links(text: str) -> list[str]:
    """Documents the broker linked instead of attaching (Dropbox, Drive, a PDF URL…). Recorded, never fetched from here."""
    return [u for u in LINK_RE.findall(text or "") if DOC_HOST_RE.search(u)][:6]


def _listing_block(L: dict[str, Any]) -> str:
    cap = L.get("stated_cap") or L.get("cap_rate")
    lines = [f"Listing {L.get('listing_id')}: {L.get('street') or L.get('address') or ''}, {L.get('city') or ''} · {L.get('asset_class') or L.get('property_type') or L.get('asset_type') or ''}",
             f"Asking {money(L.get('price')) if L.get('price') else 'not disclosed'}" + (f" · stated cap {cap}%" if cap else "")
             + (f" · stated NOI {money(L['stated_noi'])}" if L.get("stated_noi") else "")]
    for k, lab in (("building_sf", "SF"), ("year_built", "built"), ("occupancy", "occupancy %"), ("lease_type", "lease"), ("tenant_count", "tenants"),
                   ("days_on_market", "days on market"), ("tag", "engine tag"), ("evidence_grade", "evidence grade"), ("offer_anchor", "offer anchor")):
        if L.get(k) not in (None, "", []):
            v = money(L[k]) if k == "offer_anchor" else L[k]
            lines.append(f"{lab}: {v}")
    if L.get("description"):
        lines.append("Listing text: " + str(L["description"])[:800])
    return "\n".join(lines)


def _prompt(L: dict[str, Any], t: dict[str, Any], msgs: list[dict[str, Any]], files: list[dict[str, Any]]) -> str:
    parts = [_listing_block(L), "", f"Broker: {t.get('broker_name') or ''} <{t.get('broker_email') or ''}> · answered {t.get('hours_to_reply')} h after our email.", ""]
    parts.append("THE CONVERSATION (broker's messages, oldest first):")
    for m in msgs:
        parts.append(f"--- {m.get('created_at')} from {m.get('from_addr')}\n{(m.get('body') or '').strip()[:3000]}")
    links = [u for m in msgs for u in doc_links(m.get("body") or "")]
    if links:
        parts.append("\nDocuments LINKED (not attached, not opened): " + ", ".join(links))
    parts.append("")
    if files:
        parts.append(f"DOCUMENTS ON FILE ({len(files)}):")
        budget = 60_000
        for f in files:
            text = (f.get("text") or "").strip()
            take = text[: max(2000, min(len(text), budget // max(1, len(files))))]
            parts.append(f"=== {f['name']} (read as {f.get('kind')}) ===\n{take or '(no readable text: scanned or image-only)'}")
    else:
        parts.append("DOCUMENTS ON FILE: none yet.")
    parts.append("\nWrite the review for Rodrigo now.")
    return "\n".join(parts)


def rules_review(L: dict[str, Any], t: dict[str, Any], msgs: list[dict[str, Any]], files: list[dict[str, Any]]) -> dict[str, Any]:
    """The no-Claude read: what the regexes can tell. Marked as such on the card and replaced when Claude is back."""
    body = "\n".join((m.get("body") or "") for m in msgs)
    last = (msgs[-1].get("body") or "").strip() if msgs else ""
    dead = DEAD_RE.search(body)
    books = [f for f in files if f.get("kind") in BOOK_KINDS]
    numbers: list[str] = []
    price = L.get("price")
    for f in books:
        facts = documents.facts_from_text(f.get("text") or "")
        if facts.get("noi"):
            numbers.append(f"NOI in {f['name']}: {money(facts['noi'])}" + (f" · real cap at ask {facts['noi'] / price * 100:.2f}%" if price else ""))
        if facts.get("occupancy"):
            numbers.append(f"Occupancy in {f['name']}: {facts['occupancy']}%")
    m = PRICE_RE.search(last)
    if m:
        numbers.append(f"Price mentioned by the broker: ${m.group(1)}")
    c = CAP_RE.search(last)
    if c:
        numbers.append(f"Cap mentioned by the broker: {c.group(1)}%")
    flags: list[str] = []
    if dead:
        verdict, step, needs = "NOT A FIT", "Mark it Not a fit; nothing to do.", False
    elif NDA_RE.search(_no_disclaimer(body)):
        verdict, step, needs = "CAN'T TELL YET", "NEEDS YOU: the broker wants the CA/NDA signed before releasing the books.", True
    elif books:
        verdict, step, needs = ("LEANS FIT" if numbers else "CAN'T TELL YET"), "Read the numbers above against the 8 cap; Claude's full read follows when it is reachable.", False
        if not any("NOI" in n for n in numbers):
            flags.append("Books on file but no NOI line could be read out of them (scanned, or a format the reader does not parse).")
    elif LATER_RE.search(body):
        verdict, step, needs = "CAN'T TELL YET", "Broker says the books are coming; wait for the next message.", False
    elif doc_links(body):
        verdict, step, needs = "CAN'T TELL YET", "NEEDS YOU: the documents are behind a link (not attached); open it from the Mac and drop the files in data/listings/" + str(L.get("listing_id")) + "/.", True
    elif len(re.sub(r"\W", "", last)) < 40:
        verdict, step, needs = "CAN'T TELL YET", "Empty or signature-only reply; Charles asks again for the books.", False
    else:
        verdict, step, needs = "CAN'T TELL YET", "Charles thanks them and asks for the trailing twelve and rent roll again.", False
    if POF_RE.search(body):
        flags.append("Broker asks for proof of funds.")
    if OFFER_RE.search(body):
        numbers.append("Broker signals the owner is open to offers / negotiable.")
    other = ["Broker mentions other / off-market property (read the email; Claude's read names them when it is back)."] if POCKET_RE.search(body) else []
    eight = []
    noi = next((documents.facts_from_text(f.get("text") or "").get("noi") for f in books if documents.facts_from_text(f.get("text") or "").get("noi")), None) or L.get("stated_noi")
    if noi:
        eight = [f"NOI used {money(noi)} (as stated; tax reset and management not taken out yet)",
                 f"8 cap before the tax reset at {money(noi / 0.08)}" + (f" (ask {money(price)})" if price else ""),
                 "Claude's full read adds the tax reset and a manager/reserve when it is reachable."]
    return {"verdict": verdict, "what_they_said": (last[:300] or "(no text)"), "books_say": (f"{len(books)} document(s) on file: " + ", ".join(f["name"] for f in books)) if books else "No documents yet.",
            "numbers": numbers, "red_flags": flags, "next_step": step, "needs_rodrigo": needs, "reply_draft": "", "other_deals": other,
            "eight_cap": eight, "pros": [], "cons": [], "doubts": [], "believe": [], "_source": "rules"}


GENERIC_LOCAL = {"info", "sales", "admin", "office", "team", "contact", "listings", "leasing", "hello", "mail", "inquiries", "deals", "broker", "agent"}


def _first_name(name: str | None, email: str | None = None) -> str:
    """The broker's first name: from their name, else from a name-like mailbox (luca@… → Luca), else "there"."""
    n = re.sub(r"[^A-Za-z' -]", " ", (name or "")).split()
    if n and len(n[0]) > 1 and n[0].lower() not in GENERIC_LOCAL:
        return n[0].capitalize()
    local = (email or "").split("@")[0].lower()
    first = re.split(r"[._-]", local)[0] if local else ""
    if first.isalpha() and 3 <= len(first) <= 12 and first not in GENERIC_LOCAL:
        return first.capitalize()
    return "there"


def _signature(cfg: Any) -> str:
    lines = cfg.get("operator.signature_lines") if cfg is not None else None
    return "\n".join(lines or ["Charles Grant", "Junior Acquisitions Agent", "Titan Realty Investments", "Office Line: +1 (561) 405 7379"])


# Rodrigo, 2026-11-10: the first books thank-you "seems too robotic, make it sound more human, use their first name". Three ways of
# saying the same thing (picked by thread, so two brokers in one office do not get the identical email), each naming the property.
BOOKS_ACK = [
    "Got everything on {street}, thank you for turning it around so fast. That makes our job a lot easier.\n\n"
    "Our underwriting team is going through every package that came in today, and yours is in the stack. Bear with us: "
    "you'll hear back from me within 24 hours, sooner if the numbers line up.",
    "Thanks for sending the books on {street}, that's exactly what we needed.\n\n"
    "We're working through them today along with the other packages that came in. Give us a little time and I'll come back "
    "to you within 24 hours at the latest.",
    "Really appreciate you sending everything on {street} so quickly. Not everyone does.\n\n"
    "Our underwriting team is on it today. Bear with us, and I'll get back to you no later than 24 hours from now.",
]


def template_reply(kind: str, first: str, cfg: Any = None, *, sold: bool = False, later: bool = False, street: str | None = None, seed: int = 0) -> str:
    """Rodrigo's own words (2026-11-10), in Charles's voice. No price, no "buyer"/"client"/"investor"."""
    place = (street or "").strip() or "the property"
    if kind == "books_ack":
        body = BOOKS_ACK[seed % len(BOOKS_ACK)].format(street=place)
    elif kind == "ask_books":
        if later:
            body = (f"Thanks for getting back to me on {place}, I appreciate it. The books are what we're analyzing right now: the "
                    "trailing twelve P&L, the rent roll and the offering memorandum.\n\nOnce they're in, we'll go through them and get "
                    "back to you quickly.")
        else:
            body = (f"Thanks for getting back to me on {place}. Can you please send us the books? The trailing twelve P&L, the rent "
                    "roll and the offering memorandum.\n\nWe're analyzing everything off the books right now, and once we have them "
                    "we'll go through it and get back to you.")
    elif kind == "pass":
        opener = ("Thank you for letting us know, and congrats on getting it done. We are always buying." if sold
                  else "Thank you for getting back to us. This one won't work for us, but we are always buying.")
        body = (opener + "\n\nIf you ever come across pocket listings, distressed properties that need work, or apartment buildings, we have "
                "capital ready and other partners looking. Please send them our way.")
    else:
        raise ValueError(kind)
    return f"Hey {first},\n\n{body}\n\nBest regards,\n\n{_signature(cfg)}"


def template_kind(conn, t: dict[str, Any], msgs: list[dict[str, Any]], files: list[dict[str, Any]], r: dict[str, Any]) -> tuple[str | None, dict[str, bool]]:
    """Which of Rodrigo's fixed replies fits the broker's latest message, if any (each kind at most once per thread)."""
    from ..replies.clean import clean_reply
    if t.get("rodrigo_at") or not msgs:
        return None, {}
    done = {row[0] for row in conn.execute("SELECT reply_kind FROM commercial_reviews WHERE thread_id=? AND reply_kind IS NOT NULL", (t["id"],))}
    raw = msgs[-1].get("body") or ""
    last = clean_reply(raw)
    try:
        att = json.loads(t.get("attachments") or "[]")
    except json.JSONDecodeError:
        att = []
    is_book = lambda f: f.get("kind") in BOOK_KINDS or str(f.get("filename") or f.get("name") or "").lower().endswith((".xlsx", ".xls", ".csv", ".xlsm"))
    books_now = any(is_book(f) for f in att if f.get("message_id") == msgs[-1].get("gmail_message_id")) or bool(doc_links(last))
    books_ever = books_now or any(is_book(f) for f in att) or any(is_book(f) for f in files) or any(doc_links(m.get("body") or "") for m in msgs)
    dead = bool(DEAD_RE.search(last))
    verdict = (r.get("verdict") or "").upper()
    if books_now and "books_ack" not in done:
        return "books_ack", {}
    if (verdict in ARCHIVE_VERDICTS or dead) and not r.get("other_deals") and not POCKET_RE.search(last) and "pass" not in done:
        return "pass", {"sold": dead}
    if (not books_ever and not dead and verdict not in ARCHIVE_VERDICTS and not r.get("other_deals") and not POCKET_RE.search(last)
            and not NDA_RE.search(_no_disclaimer(last)) and not POF_RE.search(last) and not QUESTION_RE.search(last) and "ask_books" not in done):
        return "ask_books", {"later": bool(LATER_RE.search(last))}
    return None, {}


def _auto_sends(r: dict[str, Any], auto_reply: bool) -> bool:
    if r.get("reply_kind") == "note_send":        # his own note said send, or he dragged the card to Approved (card_notes.py)
        return True
    if r.get("reply_kind") == "note":             # written from his note: waits for his 'send it' or the drag
        return False
    return bool(auto_reply and not r.get("_rodrigo") and (not r.get("needs_rodrigo") or r.get("reply_kind") in AUTO_KINDS))


def format_review(r: dict[str, Any], books_note: str | None = None, auto_reply: bool = False) -> str:
    head = f"🧠 READ FOR YOU · {r.get('verdict')}" + ("  (rules only, Claude unreachable; retried next round)" if r.get("_source") == "rules" else "")
    out = [head, "", f"What they said: {r.get('what_they_said', '').strip()}", "", f"What the books say: {r.get('books_say', '').strip()}"]
    if r.get("numbers"):
        out += ["", "Numbers:"] + [f"• {n}" for n in r["numbers"][:8]]
    for key, title in (("eight_cap", "Where it has to be for a true 8 cap (our assumptions):"), ("pros", "Pros:"), ("cons", "Cons:"),
                       ("doubts", "What I don't believe (looks inflated, left out or untrue):"), ("believe", "What checks out:")):
        if r.get(key):
            out += ["", title] + [f"• {n}" for n in r[key][:8]]
    if r.get("red_flags"):
        out += ["", "Red flags:"] + [f"• {n}" for n in r["red_flags"][:8]]
    step = (r.get("next_step") or "").strip()
    if r.get("needs_rodrigo") and not step.upper().startswith("NEEDS YOU"):
        step = "NEEDS YOU: " + step
    if r.get("other_deals"):
        out += ["", "Other deals they pitched (pocket / off-market), forwarded to you:"] + [f"• {n}" for n in r["other_deals"][:6]]
    out += ["", f"Next step: {step}"]
    if (r.get("reply_draft") or "").strip():
        if r.get("_rodrigo"):
            head = "Charles's reply (not sent: you are writing to this broker yourself; comment 'send it' if you want Charles to send it):"
        elif _auto_sends(r, auto_reply):
            head = "Charles's reply (goes out at the next round on its own; comment 'hold' here to stop it):"
        else:
            head = "Charles's reply (not sent; comment 'send it' on this card and it goes at the next round):"
        out += ["", head, "", r["reply_draft"].strip()]
    if books_note:
        out += ["", books_note]
    return "\n".join(out)


def review_thread(conn, cfg: Any, llm, t: dict[str, Any], asana=None, secs: dict[str, str] | None = None, no_reply: bool = False) -> dict[str, Any]:
    """Read one thread's latest state, post the review on the card, record it. Returns {verdict, source, moved}."""
    L = _listing(conn, t["listing_id"])
    msgs = _messages(conn, t["id"])
    files = _files(cfg, t["listing_id"])
    books = [f for f in files if f.get("kind") in BOOK_KINDS]
    books_note = None
    if books:   # the poll may have missed the regrade (or the files were dropped in by hand): grade them here
        try:
            v = regrade(conn, cfg, t["listing_id"])
            if v:
                books_note = verdict_text(v)
                L = _listing(conn, t["listing_id"])
        except Exception as e:  # noqa: BLE001
            log.exception("regrade in review %s: %s", t["listing_id"], e)
            books_note = f"Regrade failed: {type(e).__name__}: {e}"[:200]
    r = None
    if llm is not None and getattr(llm, "available", False):
        try:
            r = llm.structured("commercial_books_review", SYSTEM, _prompt(L, t, msgs, files), SCHEMA, max_tokens=1200)
        except Exception as e:  # noqa: BLE001
            log.warning("books review %s: %s", t["listing_id"], e)
            r = None
        if r is not None:
            r["_source"] = "claude"
            if r.get("verdict") not in VERDICTS:
                r["verdict"] = "CAN'T TELL YET"
    if r is None:
        r = rules_review(L, t, msgs, files)
    r["other_deals"] = [str(x).strip() for x in (r.get("other_deals") or []) if str(x).strip()]
    r["_rodrigo"] = bool(t.get("rodrigo_at"))
    if no_reply:      # a read of files HE dropped on the card: nothing is drafted to the broker
        r["reply_draft"] = ""
    hits = commits_us(r.get("reply_draft") or "")
    if hits:      # a date or a commitment in Claude's draft: it waits for Rodrigo, whatever else the read says
        r["needs_rodrigo"] = True
        r["red_flags"] = list(r.get("red_flags") or []) + [f"Charles's draft mentions {', '.join(hits[:4])}: held for you (no dates or commitments go out on their own)."]
    kind, opts = (None, {}) if no_reply else template_kind(conn, t, msgs, files, r)
    if kind:     # Rodrigo's own words beat the drafted reply for these three cases (2026-11-10)
        r["reply_draft"], r["reply_kind"] = template_reply(kind, _first_name(t.get("broker_name"), t.get("broker_email")), cfg, **opts,
                                                           street=L.get("street"), seed=int(t.get("id") or 0)), kind
    text = format_review(r, books_note, auto_reply=bool(cfg.get("commercial.review.auto_reply", False)))
    body = "\n".join((m.get("body") or "") for m in msgs)
    dead = r["verdict"] == "NOT A FIT" or bool(DEAD_RE.search((msgs[-1].get("body") or "") if msgs else ""))
    column = "Not a fit" if dead else ("Books received" if books else None)
    moved = None
    where = _column_now(conn, asana, t["listing_id"], t["card_gid"]) if (column and asana is not None and t.get("card_gid")) else None
    if column:
        conn.execute("UPDATE commercial_listings SET board_column=? WHERE listing_id=? AND board_column NOT IN ('Offer out','Closed')", (column, t["listing_id"]))
        if dead:
            conn.execute("UPDATE commercial_threads SET state='CLOSED', updated_at=? WHERE id=?", (iso(now_utc()), t["id"]))
    if asana is not None and t.get("card_gid"):
        asana.comment(t["card_gid"], text)
        cur = conn.execute("SELECT board_column FROM commercial_listings WHERE listing_id=?", (t["listing_id"],)).fetchone()
        from .board import REVIEWED
        if (column and secs and secs.get(column) and (not cur or cur[0] in (None, column))
                and where not in STAYS_PUT + (REVIEWED,)):   # a card he put in Needs Me / Needs Revision / Reviewed / Offer out … stays put
            asana.move(t["card_gid"], secs[column])
            moved = column
    conn.execute("INSERT INTO commercial_reviews (thread_id, listing_id, gmail_message_id, verdict, needs_rodrigo, text, source, reply_draft, other_deals, "
                 "reply_kind, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (t["id"], t["listing_id"], t.get("last_msg_id"), r["verdict"], 1 if r.get("needs_rodrigo") else 0, text, r.get("_source"),
                  (r.get("reply_draft") or "").strip() or None, json.dumps(r["other_deals"]) if r["other_deals"] else None, r.get("reply_kind"), iso(now_utc())))
    return {"verdict": r["verdict"], "source": r.get("_source"), "moved": moved, "needs_rodrigo": bool(r.get("needs_rodrigo")), "other_deals": r["other_deals"],
            "reply_kind": r.get("reply_kind")}


def review_pending(conn, cfg: Any, asana=None, llm=None, limit: int | None = None) -> dict[str, Any]:
    """Every answered thread without a review of its latest message: read it, post it, move the card. Runs every round."""
    limit = limit or int(cfg.get("commercial.review.per_round", 20))
    res: dict[str, Any] = {"reviewed": 0, "claude": 0, "rules": 0, "needs_rodrigo": 0, "other_deals": 0, "moved": {}, "errors": 0}
    todo = pending(conn, limit)
    if not todo:
        return res
    project = cfg.get("commercial.asana.project")
    secs = ensure_board(asana, project) if (asana is not None and project) else {}
    for t in todo:
        try:
            out = review_thread(conn, cfg, llm, t, asana, secs)
        except Exception as e:  # noqa: BLE001
            res["errors"] += 1
            log.exception("books review %s failed: %s", t.get("listing_id"), e)
            continue
        res["reviewed"] += 1
        res[out["source"] or "rules"] = res.get(out["source"] or "rules", 0) + 1
        res["needs_rodrigo"] += 1 if out["needs_rodrigo"] else 0
        res["other_deals"] += 1 if out.get("other_deals") else 0
        if out["moved"]:
            res["moved"][out["moved"]] = res["moved"].get(out["moved"], 0) + 1
    return res


def send_approved_replies(conn, cfg: Any, asana, gmail, now=None) -> dict[str, Any]:
    """Charles's drafted reply on a 1031 card goes out in the same Gmail thread, logged as kind om_reply (does not count against
    the OM-request pace), 📤 comment on the card. With `commercial.review.auto_reply` (Rodrigo, 2026-11-09 evening) a reply that
    does not need him goes on its own at the next round unless he commented "hold" under the review; a NEEDS YOU review, and
    everything when auto_reply is off, waits for his plain go-ahead ("send it")."""
    from ..asana import BOT
    from ..comment_router import SEND_RE
    res: dict[str, Any] = {"sent": 0, "waiting": 0, "errors": 0}
    if asana is None:
        return res
    now = now or now_utc()
    auto = bool(cfg.get("commercial.review.auto_reply", False))
    rows = conn.execute("""SELECT r.*, t.card_gid, t.gmail_thread_id, t.broker_email, t.broker_name, t.subject AS thread_subject, t.listing_id AS lid,
                                  t.rodrigo_at
                           FROM commercial_reviews r JOIN commercial_threads t ON t.id=r.thread_id
                           WHERE r.reply_draft IS NOT NULL AND r.reply_sent_at IS NULL AND t.card_gid IS NOT NULL
                             AND r.id = (SELECT MAX(id) FROM commercial_reviews WHERE thread_id=r.thread_id)""").fetchall()
    for r in rows:
        r = db.row_to_dict(r)
        try:
            comments = asana.comments(r["card_gid"])
        except Exception as e:  # noqa: BLE001
            res["errors"] += 1
            log.warning("comments for %s: %s", r["card_gid"], e)
            continue
        # simple replies and Rodrigo's three fixed replies go on their own; NEEDS YOU, and any thread he is writing in himself, wait for him
        go = _auto_sends({**r, "_rodrigo": bool(r.get("rodrigo_at"))}, auto)
        if go and r.get("reply_kind") not in AUTO_KINDS + ("note_send",) and commits_us(r.get("reply_draft") or ""):
            go = False                                     # belt and braces for drafts stored before the rule: only his word sends these
        for c in comments:                                 # his latest word wins: "send it" sends, "hold" stops
            txt = (c.get("text") or "").strip()
            if txt.startswith(BOT) or (c.get("created_at") and c["created_at"] < (r.get("created_at") or "")):
                continue      # the engine's own comments, and anything he wrote before this review, are not a go-ahead
            if SEND_RE.search(txt):
                go = True
            elif HOLD_RE.search(txt):
                go = False
        if not go:
            res["waiting"] += 1
            continue
        from ..approvals import in_window
        if not in_window(cfg, now):                        # brokers hear from Charles inside the send window only (8:30-5:30 ET)
            res["waiting"] += 1
            res["reason"] = "outside the send window"
            continue
        if gmail is None:
            res["errors"] += 1
            asana.comment(r["card_gid"], "Could not send Charles's reply: Gmail is not connected on this machine. The next round retries.")
            continue
        subject = r.get("thread_subject") or ""
        subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        ts = iso(now)
        if not _claim(conn, "reply_sent_at", r["id"], ts):
            continue                        # another round (check-replies vs approvals) took this one: never email a broker twice
        try:
            sent = gmail.send(r["broker_email"], subject, r["reply_draft"], thread_id=r.get("gmail_thread_id"))
        except Exception as e:  # noqa: BLE001
            conn.execute("UPDATE commercial_reviews SET reply_sent_at=NULL WHERE id=?", (r["id"],))
            res["errors"] += 1
            asana.comment(r["card_gid"], f"Could not send Charles's reply (Gmail: {str(e)[:160]}). The next round retries.")
            continue
        conn.execute("INSERT INTO outreach (agent_id, property_ids, channel, kind, status, subject, body, to_address, gmail_thread_id, gmail_message_id, "
                     "run_date, created_at, sent_at, asana_task_id) VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (json.dumps([f"loopnet:{r['lid']}"]), "email", "om_reply", "sent", subject, r["reply_draft"], r["broker_email"],
                      sent.get("thread_id") or r.get("gmail_thread_id"), sent.get("message_id"), ts[:10], ts, ts, r["card_gid"]))
        conn.execute("UPDATE commercial_reviews SET reply_sent_at=? WHERE id=?", (ts, r["id"]))
        conn.execute("UPDATE commercial_threads SET updated_at=? WHERE id=?", (ts, r["thread_id"]))
        asana.comment(r["card_gid"], f"📤 Charles's reply sent to {r['broker_email']} at {now.strftime('%Y-%m-%d %H:%M')} UTC:\n\n{r['reply_draft']}")
        if r.get("needs_rodrigo") or (r.get("reply_kind") or "").startswith("note"):   # his move is done: the card leaves Needs Me
            _unpark(conn, cfg, asana, r["lid"], r["card_gid"])
        res["sent"] += 1
    return res


def _rodrigo_said(asana, card_gid: str, since: str) -> list[str]:
    from ..asana import BOT
    try:
        comments = asana.comments(card_gid) if (asana is not None and card_gid) else []
    except Exception:  # noqa: BLE001
        return []
    return [(c.get("text") or "").strip() for c in comments
            if not (c.get("text") or "").startswith(BOT) and not (c.get("created_at") and c["created_at"] < since)]


def triage_inbox(conn, cfg: Any, asana, gmail, now=None) -> dict[str, Any]:
    """Rodrigo, 2026-11-09 evening: "forward me the emails that need me … the ones that do not fit, archive them … agents sent us
    deals on pocket listings." For the latest review of every thread, once:
    - FORWARD to REPORT_TO (from Charles's mailbox, the broker's words + the read + the broker's files as attachments) when the
      review needs him, is FITS / LEANS FIT, or lists other deals the broker pitched;
    - ARCHIVE the thread in Charles's inbox when the verdict is NOT A FIT / DOES NOT FIT with nothing pitched and Charles's reply
      (if one was drafted) has already gone. A later answer in that thread is still read (threads are opened by id)."""
    res: dict[str, Any] = {"forwarded": 0, "archived": 0, "waiting": 0, "errors": 0}
    if gmail is None:
        return res
    now = now or now_utc()
    fwd_on = bool(cfg.get("commercial.review.forward_to_rodrigo", True))
    arch_on = bool(cfg.get("commercial.review.archive_not_fit", True))
    to = cfg.secret("REPORT_TO")
    max_bytes = int(float(cfg.get("commercial.review.forward_max_mb", 20)) * 1_000_000)
    rows = conn.execute("""SELECT r.*, t.card_gid, t.gmail_thread_id, t.broker_email, t.broker_name, t.subject AS thread_subject, t.listing_id AS lid,
                                  t.attachments AS thread_files, t.rodrigo_at
                           FROM commercial_reviews r JOIN commercial_threads t ON t.id=r.thread_id
                           WHERE r.id = (SELECT MAX(id) FROM commercial_reviews WHERE thread_id=r.thread_id)
                             AND (r.forwarded_at IS NULL OR r.archived_at IS NULL OR r.parked_at IS NULL)""").fetchall()
    secs = None
    for r in rows:
        r = db.row_to_dict(r)
        try:
            other = json.loads(r.get("other_deals") or "[]")
        except json.JSONDecodeError:
            other = []
        verdict = (r.get("verdict") or "").upper()
        wants_fwd = bool(r.get("needs_rodrigo")) or verdict in FORWARD_VERDICTS or bool(other)
        wants_arch = verdict in ARCHIVE_VERDICTS and not other and not r.get("rodrigo_at")
        ts = iso(now)
        if fwd_on and wants_fwd and not r.get("forwarded_at"):
            if not to:
                res["errors"] += 1
                log.warning("triage: REPORT_TO not set, cannot forward %s", r.get("lid"))
            elif _claim(conn, "forwarded_at", r["id"], ts):     # one forward per review, even when two rounds overlap
                try:
                    _forward(conn, cfg, gmail, to, r, other, max_bytes, now)
                    if asana is not None and r.get("card_gid"):
                        why = "needs you" if r.get("needs_rodrigo") else ("pitched other deals" if other else verdict.lower())
                        asana.comment(r["card_gid"], f"📬 Forwarded to your inbox ({why}).")
                    res["forwarded"] += 1
                except Exception as e:  # noqa: BLE001
                    conn.execute("UPDATE commercial_reviews SET forwarded_at=NULL WHERE id=?", (r["id"],))
                    res["errors"] += 1
                    log.warning("triage forward %s: %s", r.get("lid"), e)
        if wants_fwd and not r.get("parked_at") and asana is not None and r.get("card_gid") and _claim(conn, "parked_at", r["id"], ts):
            # Rodrigo, 2026-11-10: the cards behind the emails that need him belong in ONE column on the 1031 board
            if secs is None:
                project = cfg.get("commercial.asana.project")
                secs = ensure_board(asana, project) if project else {}
            try:
                done = _park(conn, asana, secs, r["lid"], r["card_gid"])
            except Exception as e:  # noqa: BLE001
                done = "failed"
                log.warning("park %s in Needs Me: %s", r.get("lid"), e)
            if done == "moved":
                res["parked"] = res.get("parked", 0) + 1
            elif done == "failed":      # never mark a card parked that did not move: the next round tries again
                conn.execute("UPDATE commercial_reviews SET parked_at=NULL WHERE id=?", (r["id"],))
                res["errors"] += 1
        if arch_on and wants_arch and not r.get("archived_at"):
            if r.get("reply_draft") and not r.get("reply_sent_at"):
                res["waiting"] += 1          # Charles's pass note goes first; archive at a later round
                continue
            if not r.get("gmail_thread_id"):
                continue
            if not _claim(conn, "archived_at", r["id"], ts):
                continue
            try:
                gmail.archive_thread(r["gmail_thread_id"])
                if asana is not None and r.get("card_gid"):
                    asana.comment(r["card_gid"], "🗂 Archived in Charles's inbox (does not fit). A new answer from the broker still gets read.")
                res["archived"] += 1
            except Exception as e:  # noqa: BLE001
                conn.execute("UPDATE commercial_reviews SET archived_at=NULL WHERE id=?", (r["id"],))
                res["errors"] += 1
                log.warning("triage archive %s: %s", r.get("lid"), e)
    return res


def _claim(conn, column: str, review_id: int, ts: str) -> bool:
    """Mark a review done BEFORE doing the thing, so two rounds running at once (check-replies and approvals) never both do it."""
    return conn.execute(f"UPDATE commercial_reviews SET {column}=? WHERE id=? AND {column} IS NULL", (ts, review_id)).rowcount == 1


def _column_now(conn, asana, listing_id: str, card_gid: str) -> str | None:
    """Where the card really sits (Rodrigo drags cards; the stored column can be behind)."""
    from .board import column_key
    try:
        for mem in (asana.task(card_gid, fields="name,memberships.section.name") or {}).get("memberships") or []:
            key = column_key((mem.get("section") or {}).get("name"))
            if key:
                return key
    except Exception:  # noqa: BLE001
        pass
    row = conn.execute("SELECT board_column FROM commercial_listings WHERE listing_id=?", (listing_id,)).fetchone()
    return row[0] if row else None


def _park(conn, asana, secs: dict[str, str], listing_id: str, card_gid: str) -> str:
    """Move the card to Needs Me unless he already put it somewhere that settles it (Offer out / Closed / Not a fit / Needs Me).
    Returns "moved", "kept" (it stays where he put it) or "failed" (no Needs Me column yet: try again next round)."""
    if not secs.get(NEEDS_ME):
        return "failed"
    if _column_now(conn, asana, listing_id, card_gid) in STAYS_PUT:
        return "kept"
    asana.move(card_gid, secs[NEEDS_ME])
    conn.execute("UPDATE commercial_listings SET board_column=? WHERE listing_id=?", (NEEDS_ME, listing_id))
    return "moved"


def _unpark(conn, cfg: Any, asana, listing_id: str, card_gid: str) -> None:
    """His move is done (reply sent / he wrote the broker): back to In touch with the agent, if the card still sits in Needs Me."""
    if _column_now(conn, asana, listing_id, card_gid) != NEEDS_ME:
        return
    project = cfg.get("commercial.asana.project")
    secs = ensure_board(asana, project) if project else {}
    if secs.get("In touch with the agent"):
        asana.move(card_gid, secs["In touch with the agent"])
        conn.execute("UPDATE commercial_listings SET board_column='In touch with the agent' WHERE listing_id=?", (listing_id,))


def _forward(conn, cfg: Any, gmail, to: str, r: dict[str, Any], other: list[str], max_bytes: int, now) -> None:
    L = _listing(conn, r["lid"])
    msgs = _messages(conn, r["thread_id"])
    tag = "NEEDS YOU" if r.get("needs_rodrigo") else ("POCKET / OTHER DEALS" if other else (r.get("verdict") or "").upper())
    addr = f"{L.get('street') or L.get('address') or ''}, {L.get('city') or ''}".strip(", ")
    subject = f"FW [{tag}] {addr or r.get('thread_subject') or ''} · {r.get('broker_name') or r.get('broker_email') or ''}"
    card = f"https://app.asana.com/0/{cfg.get('commercial.asana.project')}/{r['card_gid']}" if r.get("card_gid") else ""
    cap = L.get("stated_cap") or L.get("cap_rate")
    parts = [f"{tag}: {addr}" + (f" · asking {money(L['price'])}" if L.get("price") else "") + (f" · stated cap {cap}%" if cap else ""),
             f"Broker: {r.get('broker_name') or ''} <{r.get('broker_email') or ''}>" + (f"\nCard: {card}" if card else ""), ""]
    if other:
        parts += ["Other deals they pitched:"] + [f"• {o}" for o in other] + [""]
    parts += [r.get("text") or "", "", "---- The broker's email(s) ----"]
    for m in msgs[-3:]:
        parts.append(f"\nFrom {m.get('from_addr')} · {m.get('created_at')}\n{(m.get('body') or '').strip()[:4000]}")
    attachments: list[dict[str, Any]] = []
    total = 0
    try:
        files = json.loads(r.get("thread_files") or "[]")
    except json.JSONDecodeError:
        files = []
    for mid in dict.fromkeys(f.get("message_id") for f in files if f.get("message_id")):
        try:
            for a in gmail.attachments(mid):
                if total + len(a.get("bytes") or b"") > max_bytes:
                    parts.append(f"(file not attached, too big: {a.get('filename')})")
                    continue
                total += len(a.get("bytes") or b"")
                attachments.append(a)
        except Exception as e:  # noqa: BLE001
            parts.append(f"(could not fetch the files of message {mid}: {str(e)[:120]})")
    if files and not attachments:
        parts.append("Files on the Mac: " + ", ".join(f.get("filename") or "" for f in files))
    gmail.send(to, subject, "\n".join(parts), attachments=attachments or None)
