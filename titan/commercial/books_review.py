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
Nothing in here sends an email.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .. import db
from ..util import iso, log, money, now_utc
from . import documents
from .board import ensure_board
from .outreach import BOOK_KINDS, regrade, verdict_text

LINK_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
DOC_HOST_RE = re.compile(r"dropbox|drive\.google|docs\.google|box\.com|sharefile|wetransfer|onedrive|sharepoint|dealroom|buildout|crexi|loopnet\.com/.*(?:document|brochure|om)|\.pdf", re.I)
DEAD_RE = re.compile(r"(?:just |already |recently )?(?:signed a lease|leased|under contract|sold|off (?:the )?market|no longer (?:available|for sale)|"
                     r"accepted an offer|in escrow|withdrawn)", re.I)
NDA_RE = re.compile(r"\b(?:nda|ca/nda|confidentiality agreement|confidentiality|non-?disclosure)\b", re.I)
POF_RE = re.compile(r"proof of funds|\bpof\b", re.I)
LATER_RE = re.compile(r"later today|tomorrow|when i(?:'m| am) back|next week|give me a day|will send|i'll send|i’ll send|shortly", re.I)
OFFER_RE = re.compile(r"ready to review offers|open to offers|bring (?:an|your) offer|make an offer|owner is negotiable|negotiable|motivated", re.I)
PRICE_RE = re.compile(r"\$\s?([\d,]{7,}(?:\.\d+)?)")
CAP_RE = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s?%?\s?cap", re.I)

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
- Charles's reply: write it ONLY when the broker is waiting on something Charles can answer without Rodrigo (thanks + what we
  still need; the exchange timing when asked: "we need to identify within 45 days and are moving now"; proof of funds "we can
  provide on request"). Never a price, never "our buyer"/"client"/"investor" (say "a partner of ours"), never promise to waive
  anything. 30 to 80 words, starts "Hey <first name>,", ends with the signature lines exactly:
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
    },
    "required": ["verdict", "what_they_said", "books_say", "numbers", "red_flags", "next_step", "needs_rodrigo", "reply_draft"],
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
    lines = [f"Listing {L.get('listing_id')}: {L.get('address') or ''}, {L.get('city') or ''} · {L.get('asset_type') or ''}",
             f"Asking {money(L.get('price')) if L.get('price') else 'not disclosed'}" + (f" · stated cap {L['cap_rate']}%" if L.get("cap_rate") else "")
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
    elif NDA_RE.search(body):
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
    return {"verdict": verdict, "what_they_said": (last[:300] or "(no text)"), "books_say": (f"{len(books)} document(s) on file: " + ", ".join(f["name"] for f in books)) if books else "No documents yet.",
            "numbers": numbers, "red_flags": flags, "next_step": step, "needs_rodrigo": needs, "reply_draft": "", "_source": "rules"}


def format_review(r: dict[str, Any], books_note: str | None = None) -> str:
    head = f"🧠 READ FOR YOU · {r.get('verdict')}" + ("  (rules only, Claude unreachable; retried next round)" if r.get("_source") == "rules" else "")
    out = [head, "", f"What they said: {r.get('what_they_said', '').strip()}", "", f"What the books say: {r.get('books_say', '').strip()}"]
    if r.get("numbers"):
        out += ["", "Numbers:"] + [f"• {n}" for n in r["numbers"][:8]]
    if r.get("red_flags"):
        out += ["", "Red flags:"] + [f"• {n}" for n in r["red_flags"][:8]]
    step = (r.get("next_step") or "").strip()
    if r.get("needs_rodrigo") and not step.upper().startswith("NEEDS YOU"):
        step = "NEEDS YOU: " + step
    out += ["", f"Next step: {step}"]
    if (r.get("reply_draft") or "").strip():
        out += ["", "Charles's reply (not sent; comment 'send it' on this card and it goes at the next round):", "", r["reply_draft"].strip()]
    if books_note:
        out += ["", books_note]
    return "\n".join(out)


def review_thread(conn, cfg: Any, llm, t: dict[str, Any], asana=None, secs: dict[str, str] | None = None) -> dict[str, Any]:
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
    text = format_review(r, books_note)
    body = "\n".join((m.get("body") or "") for m in msgs)
    dead = r["verdict"] == "NOT A FIT" or bool(DEAD_RE.search((msgs[-1].get("body") or "") if msgs else ""))
    column = "Not a fit" if dead else ("Books received" if books else None)
    moved = None
    if column:
        conn.execute("UPDATE commercial_listings SET board_column=? WHERE listing_id=? AND board_column NOT IN ('Offer out','Closed')", (column, t["listing_id"]))
        if dead:
            conn.execute("UPDATE commercial_threads SET state='CLOSED', updated_at=? WHERE id=?", (iso(now_utc()), t["id"]))
    if asana is not None and t.get("card_gid"):
        asana.comment(t["card_gid"], text)
        if column and secs and secs.get(column):
            asana.move(t["card_gid"], secs[column])
            moved = column
    conn.execute("INSERT INTO commercial_reviews (thread_id, listing_id, gmail_message_id, verdict, needs_rodrigo, text, source, reply_draft, created_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?)",
                 (t["id"], t["listing_id"], t.get("last_msg_id"), r["verdict"], 1 if r.get("needs_rodrigo") else 0, text, r.get("_source"),
                  (r.get("reply_draft") or "").strip() or None, iso(now_utc())))
    return {"verdict": r["verdict"], "source": r.get("_source"), "moved": moved, "needs_rodrigo": bool(r.get("needs_rodrigo"))}


def review_pending(conn, cfg: Any, asana=None, llm=None, limit: int | None = None) -> dict[str, Any]:
    """Every answered thread without a review of its latest message: read it, post it, move the card. Runs every round."""
    limit = limit or int(cfg.get("commercial.review.per_round", 20))
    res: dict[str, Any] = {"reviewed": 0, "claude": 0, "rules": 0, "needs_rodrigo": 0, "moved": {}, "errors": 0}
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
        if out["moved"]:
            res["moved"][out["moved"]] = res["moved"].get(out["moved"], 0) + 1
    return res


def send_approved_replies(conn, cfg: Any, asana, gmail, now=None) -> dict[str, Any]:
    """Charles's drafted reply on a 1031 card goes out only when Rodrigo comments a plain go-ahead ("send it") under the review.
    Same Gmail thread, logged as kind om_reply (does not count against the OM-request pace), 📤 comment on the card."""
    from ..asana import BOT
    from ..comment_router import SEND_RE
    res: dict[str, Any] = {"sent": 0, "waiting": 0, "errors": 0}
    if asana is None:
        return res
    now = now or now_utc()
    rows = conn.execute("""SELECT r.*, t.card_gid, t.gmail_thread_id, t.broker_email, t.broker_name, t.subject AS thread_subject, t.listing_id AS lid
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
        go = False
        for c in comments:
            txt = (c.get("text") or "").strip()
            if txt.startswith(BOT) or (c.get("created_at") and c["created_at"] < (r.get("created_at") or "")):
                continue      # the engine's own comments, and anything he wrote before this review, are not a go-ahead
            if SEND_RE.search(txt):
                go = True
        if not go:
            res["waiting"] += 1
            continue
        if gmail is None:
            res["errors"] += 1
            asana.comment(r["card_gid"], "Could not send Charles's reply: Gmail is not connected on this machine. The next round retries.")
            continue
        subject = r.get("thread_subject") or ""
        subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        try:
            sent = gmail.send(r["broker_email"], subject, r["reply_draft"], thread_id=r.get("gmail_thread_id"))
        except Exception as e:  # noqa: BLE001
            res["errors"] += 1
            asana.comment(r["card_gid"], f"Could not send Charles's reply (Gmail: {str(e)[:160]}). The next round retries.")
            continue
        ts = iso(now)
        conn.execute("INSERT INTO outreach (agent_id, property_ids, channel, kind, status, subject, body, to_address, gmail_thread_id, gmail_message_id, "
                     "run_date, created_at, sent_at, asana_task_id) VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (json.dumps([f"loopnet:{r['lid']}"]), "email", "om_reply", "sent", subject, r["reply_draft"], r["broker_email"],
                      sent.get("thread_id") or r.get("gmail_thread_id"), sent.get("message_id"), ts[:10], ts, ts, r["card_gid"]))
        conn.execute("UPDATE commercial_reviews SET reply_sent_at=? WHERE id=?", (ts, r["id"]))
        conn.execute("UPDATE commercial_threads SET updated_at=? WHERE id=?", (ts, r["thread_id"]))
        asana.comment(r["card_gid"], f"📤 Charles's reply sent to {r['broker_email']} at {now.strftime('%Y-%m-%d %H:%M')} UTC:\n\n{r['reply_draft']}")
        res["sent"] += 1
    return res
