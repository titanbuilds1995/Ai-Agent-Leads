"""What Rodrigo's comment on an approval card asks for (Rodrigo, 2026-10-26: "why would it not give me what I am asking?").

Before this, every comment was treated as wording for a rewrite, so "comp this and tell me the ARV" produced a rewritten
email and no numbers, and "this is a really good email, send it" rewrote an email he liked. Now each comment is sorted:

- numbers:  comps / ARV / exit / land value / new construction / cash flow / "where we need to be"
            → numbers snapshot (titan/comps_report.py) on the card + Claude Updates; the email is held until he decides.
- send:     "send it", "I approve this", "really good email" (with nothing to change) → straight to Approved, text untouched.
- call:     "mark him hot", "call tomorrow", "follow up" → lead marked, a call card for tomorrow in Needs Rodrigo.
- check:    "double check that X is their listing" → the agent's listings we know are put in front of the rewrite and on the card.
- rewrite:  everything else (wording), as before.
Numbers always win over send: "send it ... actually comp it before we send" holds the email."""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from . import db
from .util import log, now_utc

NUMBERS_RE = re.compile(
    r"\bcomps?\b|\bcomp(?:ed|ing)?\s+(?:it|this|that)|comparab|\barv\b|after[- ]?re?pair|resale value|what (?:it|this|we|they|homes?|"
    r"houses?|properties|products?)[\w ]{0,20}(?:could|would|can|are)\s+(?:re)?sell|sell (?:it )?for\b|land value|for (?:the )?land|"
    r"new construction|new build|run (?:the )?numbers|numbers (?:look|on)|what the numbers|cash ?flow|cash cow|subfloor|"
    r"(?:good|an?) offer (?:number|for)|offer or a range|where we (?:would |need to )?(?:need to )?be|how (?:far|close) off|exit", re.I)
SEND_RE = re.compile(
    r"\b(?:send (?:it|this|him|her|them)(?: the email)?|send(?:ing)? the email|go ahead and send|i approve(?: this| it)?|approved?\b(?! the numbers)|"
    r"(?:really |very )?good email|looks good|good to go|ready to send|ship it)", re.I)
CHANGE_RE = re.compile(r"\b(?:change|take out|remove|instead|don'?t say|do not say|reword|replace|add (?:a|that|the)|shorter|"
                       r"too (?:long|robotic|formal)|rewrite|should say|let'?s say|mention)\b", re.I)
CALL_RE = re.compile(r"\b(?:mark (?:him|her|them|it|this)?\s*(?:as )?hot|hot (?:lead|potential)|call (?:him|her|them)?\s*tomorrow|"
                     r"to call tomorrow|call list|follow[- ]?up|stay on top of)\b", re.I)
CHECK_RE = re.compile(r"\b(?:double[- ]?check|confirm (?:that|on|it|this)|is (?:that|this) (?:their|his|her) listing|"
                      r"incongruen|make sure (?:that )?(?:it|this|that) is)\b", re.I)


def classify(feedback: list[str]) -> set[str]:
    text = " ".join(feedback)
    out: set[str] = set()
    if NUMBERS_RE.search(text):
        out.add("numbers")
    if CALL_RE.search(text):
        out.add("call")
    if CHECK_RE.search(text):
        out.add("check")
    if SEND_RE.search(text) and not CHANGE_RE.search(text):
        out.add("send")
    if not out & {"numbers", "send", "call"} or CHANGE_RE.search(text) and "numbers" not in out:
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


def numbers_snapshot(conn, cfg: Any, asana, card_gid: str, card: dict[str, Any], feedback: list[str], comps_fn) -> int:
    """Build the snapshot for each house asked about, post it (Claude Updates card + comment on the lead's card), save the
    text under reports/comps/. Returns how many were posted."""
    from . import comps_report
    a = cfg.get("outreach.asana", {}) or {}
    text = " ".join(feedback)
    n = 0
    for p in pick_subjects(card_props(conn, card["outreach_id"]), text):
        s = comps_report.build(conn, cfg, p["id"], comps_fn, asked=text, outreach_id=card["outreach_id"])
        out_dir = cfg.path("reports") / "comps"
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", f"{p['street']} {p['city']}".lower()).strip("-")
        (out_dir / f"{slug}.md").write_text(comps_report.to_text(s))
        url = None
        if a.get("updates_project") and a.get("needs_rodrigo"):
            try:
                t = asana.create_task_html(a["updates_project"], a["needs_rodrigo"],
                                           f"NUMBERS: {p['street']}, {p['city']} · {comps_report.headline(s)[:90]}", comps_report.to_html(s))
                url = f"https://app.asana.com/0/0/{t['gid']}/f" if t and t.get("gid") else None
            except Exception as e:  # noqa: BLE001 — the comment on the lead's own card is what matters most
                log.warning("numbers card for %s: %s", p["street"], e)
        asana.comment_html(card_gid, comps_report.to_comment(s, url))
        n += 1
    return n


def call_card(conn, cfg: Any, asana, card: dict[str, Any], feedback: list[str]) -> bool:
    """Lead marked HOT for a call, one card in Needs Rodrigo for tomorrow with the agent's phone and his note."""
    a = cfg.get("outreach.asana", {}) or {}
    row = conn.execute("SELECT a.name, a.phone, a.email FROM outreach o LEFT JOIN agents a ON a.id=o.agent_id WHERE o.id=?",
                       (card["outreach_id"],)).fetchone()
    props = card_props(conn, card["outreach_id"])
    for p in props:
        db.set_lead_status(conn, p["id"], "NEEDS_CALL", "HOT (Rodrigo): call tomorrow")
    if not (a.get("updates_project") and a.get("needs_rodrigo")):
        return False
    day = (now_utc() - timedelta(hours=5) + timedelta(days=1)).strftime("%a %b %d")
    name, phone = (row["name"] if row else None) or "agent", (row["phone"] if row else None) or "no phone on file"
    houses = "; ".join(f"{p['street']}, {p['city']}" for p in props)
    try:
        asana.create_task(a["updates_project"], a["needs_rodrigo"], f"📞 Call {day}: {name} · {phone} · {houses}",
                          f"HOT lead (your comment): {' '.join(feedback)[:600]}\n\nAgent: {name} · {phone} · {(row['email'] if row else '') or ''}\n"
                          f"Houses: {houses}\n\nThe email follows the normal flow on the Email Approvals board. An automatic "
                          "same-day follow-up email isn't built yet: call, or comment 'follow up' on the card and Claude drafts one.")
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("call card: %s", e)
        return False
