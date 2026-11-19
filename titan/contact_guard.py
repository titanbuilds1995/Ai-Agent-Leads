"""Don't email someone who is done hearing from us (Rodrigo, 2026-11-15, after Walter got three emails in an hour:
"we sent out way too many emails to this guy … if they already said excellent or absolutely and they already understood the
email, we don't email them again. Even if I say so, we need to make it bold and have me approve to it … for any communication").

Before any email goes into an existing conversation with someone outside our domain, the thread is read:
- their last message only acknowledges ours ("Excellent", "Absolutely", "Got it", "Thanks", "Sounds good", "Will review") → HOLD;
- we already wrote them in the last 24 hours and they have not answered since → HOLD (no bombarding).
A hold never sends. It shows a bold warning and waits for Rodrigo's explicit OK in words ("send anyway" / "send it"),
written AFTER the warning. His "send it" from before the warning does not count: that is the point of the warning.
Not an acknowledgement (2026-11-19, Zaira): "yes, still interested", "the seller is open", "send us an offer". Not held by an
earlier acknowledgement: an email that makes an offer at a price. The 24-hour rule still applies to offers.
`GmailClient.send(..., confirmed=True)` is the only way past it, and only the senders that saw his OK pass it.
"""
from __future__ import annotations

import re
import time
from typing import Any

ACK_RE = re.compile(
    r"\b(excellent|absolutely|perfect|great|awesome|wonderful|fantastic|amazing|thanks?|thank you|thx|ty|got it|received|"
    r"noted|understood|sounds good|sounds great|will do|will review|will look|i'?ll (?:review|look|take a look|check)|"
    r"looking forward|appreciate(?: it)?|ok(?:ay)?|cool|nice|great work|good work|copy that|roger|yes|yep|sure|👍|🙏|🎉)\b", re.I)
QUOTE_RE = re.compile(r"^\s*(?:>|On .{5,200}wrote:\s*$|-{2,}\s*Original Message|From: .+@|Sent from my )", re.I)
CONFIRM_RE = re.compile(r"\b(send (?:it )?anyway|send it|yes,? send(?: it)?(?: anyway)?|i approve|approved,? send|go ahead and send|confirm(?:ed)?,? send)\b", re.I)
# Rodrigo, 2026-11-19 (Zaira Zavala: "Yes, we are still interested." was read as a thank-you and held his $260,000 offer):
# a short message that says they are still in, open, or waiting on us is an ANSWER, not an acknowledgement. Ball is in our court.
ENGAGED_RE = re.compile(
    r"\b(?:interested|still (?:available|active|on the market|for sale|open)|open to|would consider|will consider|entertain|"
    r"make (?:us |me )?an offer|send (?:us |me |over |your )*(?:an? |your )?offer|submit|what (?:is|would be) your|your (?:best |highest )?offer|"
    r"seller (?:is|would|will|wants|might)|flexible|motivated|negotiable|let'?s talk|call me|give me a call|"
    r"show(?:ing)?|walk ?through|come see|see it)\b", re.I)
# An email that carries an offer at a price is the next step itself, not one more note: an earlier acknowledgement never holds it.
OFFER_RE = re.compile(r"\boffer\b", re.I)
PRICE_RE = re.compile(r"\$\s?\d{1,3}(?:,\d{3})+|\$\s?\d+(?:\.\d+)?\s?[kK]\b")
HOLD_PREFIX = "HOLD, not sent:"
ACK_MAX_WORDS = 30
RECENT_HOURS = 24


# Rodrigo, 2026-11-18 (Larry Starr, SVN: "Don't contact me again. I will take action against you if you do", after the Not-a-fit
# clean-up sent him a pass Rodrigo had told us to stop): someone who told us to stop never gets another email from any of our senders,
# whatever anyone confirms. Checked on their own words only (quoted history dropped), so our "stop" never trips it.
STOPPED_RE = re.compile(r"(?:do not|don'?t|never|stop) (?:contact|email|e-mail|message|text|call|reach out to|write to)(?:ing)? (?:me|us)\b|"
                        r"\bstop (?:emailing|contacting|messaging|texting|sending)\b|\bremove me\b|\btake me off\b|\bunsubscribe\b|"
                        r"\bleave me alone\b|\b(?:take|pursue) (?:legal )?action against\b|\bcease and desist\b|\bharass", re.I)


def told_us_to_stop(messages: list[dict[str, Any]], our_domain: str) -> str | None:
    """The words of the first message in the thread where they told us to stop, or None."""
    dom = (our_domain or "").lower().lstrip("@")
    for m in messages:
        if _ours(m.get("from", ""), dom):
            continue
        said = own_words(m.get("body") or "")
        hit = STOPPED_RE.search(said)
        if hit:
            return re.sub(r"\s+", " ", said[max(0, hit.start() - 40): hit.end() + 40]).strip()
    return None


class StopContact(Exception):
    """They told us to stop. Never sent, no override (not "send anyway", not confirmed=True)."""


class ContactHold(Exception):
    """An email that would bombard someone. Never sent without Rodrigo's OK written after the warning."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(warning(reason))


def own_words(body: str) -> str:
    """The sender's own words: quoted history and the 'Sent from' line dropped."""
    out = []
    for ln in (body or "").splitlines():
        if QUOTE_RE.match(ln):
            break
        out.append(ln)
    return "\n".join(out).strip()


def is_ack(body: str) -> bool:
    """A short message that only acknowledges ours: nothing asked, nothing new to answer."""
    text = own_words(body)
    words = re.findall(r"[\w']+", text)
    if not words or len(words) > ACK_MAX_WORDS or "?" in text or ENGAGED_RE.search(text):
        return False
    return bool(ACK_RE.search(text))


def is_offer(body: str) -> bool:
    """Our email makes an offer at a price (Rodrigo's number): the word offer and a dollar figure in our own words."""
    text = own_words(body)
    return bool(OFFER_RE.search(text) and PRICE_RE.search(text))


def _ours(sender: str, domain: str) -> bool:
    return bool(domain) and f"@{domain}" in (sender or "").lower()


def hold_reason(messages: list[dict[str, Any]], our_domain: str, now_ts: float | None = None,
                recent_hours: int = RECENT_HOURS, offer: bool = False) -> str | None:
    """Why this thread should not get another email from us now (None = fine).
    messages: the thread oldest first, each {'from', 'internal_ts', 'body'}."""
    msgs = [m for m in messages if (m.get("body") or "").strip() or m.get("from")]
    if not msgs:
        return None
    msgs = sorted(msgs, key=lambda m: int(m.get("internal_ts") or 0))
    dom = (our_domain or "").lower().lstrip("@")
    last = msgs[-1]
    if not offer and not _ours(last.get("from", ""), dom) and is_ack(last.get("body") or ""):
        said = re.sub(r"\s+", " ", own_words(last.get("body") or ""))[:80]
        who = re.sub(r"\s*<.*?>", "", last.get("from") or "they").strip().strip('"') or "They"
        return f'{who} already answered our last email ("{said}"). They have what they need.'
    now_ts = now_ts if now_ts is not None else time.time()
    since = now_ts - recent_hours * 3600
    ours_recent = 0
    for m in reversed(msgs):
        if not _ours(m.get("from", ""), dom):
            break                                  # they wrote after us: our earlier emails were answered
        if int(m.get("internal_ts") or 0) >= since:
            ours_recent += 1
    if ours_recent:
        return (f"We already emailed them {ours_recent} time{'s' if ours_recent > 1 else ''} in the last {recent_hours} hours "
                "and they have not answered since. Another email now is bombarding them.")
    return None


def warning(reason: str) -> str:
    return (f"**{HOLD_PREFIX} {reason}**\n\nSending again may not be the right move. If you still want it to go, "
            "comment \"send anyway\" on this card and it goes at the next round. Otherwise leave it, or change the email.")


def confirmed_after_hold(comments: list[dict[str, Any]], bot_prefix: str) -> bool:
    """Rodrigo wrote 'send anyway' AFTER the engine's hold warning on this card."""
    seen_hold = confirmed = False
    for c in comments:
        txt = (c.get("text") or "").strip()
        if txt.startswith(bot_prefix):
            if HOLD_PREFIX in txt:
                seen_hold, confirmed = True, False      # only an OK written after the LATEST warning counts
            continue
        if seen_hold and CONFIRM_RE.search(txt):
            confirmed = True
    return confirmed


def already_warned(comments: list[dict[str, Any]], bot_prefix: str) -> bool:
    """A hold warning is already the engine's latest word on this card (warn once, not every round)."""
    for c in reversed(comments):
        txt = (c.get("text") or "").strip()
        if txt.startswith(bot_prefix):
            return HOLD_PREFIX in txt
        return False
    return False
