"""Don't email someone who is done hearing from us (Rodrigo, 2026-11-15, after Walter got three emails in an hour:
"we sent out way too many emails to this guy … if they already said excellent or absolutely and they already understood the
email, we don't email them again. Even if I say so, we need to make it bold and have me approve to it … for any communication").

Before any email goes into an existing conversation with someone outside our domain, the thread is read:
- their last message only acknowledges ours ("Excellent", "Absolutely", "Got it", "Thanks", "Sounds good", "Will review") → HOLD;
- we already wrote them in the last 24 hours and they have not answered since → HOLD (no bombarding).
A hold never sends. It shows a bold warning and waits for Rodrigo's explicit OK in words ("send anyway" / "yes send it anyway"),
written AFTER the warning. His plain "send it" from before the warning does not count: that is the point of the warning.
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
CONFIRM_RE = re.compile(r"\b(send (?:it )?anyway|yes,? send(?: it)?(?: anyway)?|i approve|approved,? send|go ahead and send|confirm(?:ed)?,? send)\b", re.I)
HOLD_PREFIX = "HOLD, not sent:"
ACK_MAX_WORDS = 30
RECENT_HOURS = 24


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
    if not words or len(words) > ACK_MAX_WORDS or "?" in text:
        return False
    return bool(ACK_RE.search(text))


def _ours(sender: str, domain: str) -> bool:
    return bool(domain) and f"@{domain}" in (sender or "").lower()


def hold_reason(messages: list[dict[str, Any]], our_domain: str, now_ts: float | None = None,
                recent_hours: int = RECENT_HOURS) -> str | None:
    """Why this thread should not get another email from us now (None = fine).
    messages: the thread oldest first, each {'from', 'internal_ts', 'body'}."""
    msgs = [m for m in messages if (m.get("body") or "").strip() or m.get("from")]
    if not msgs:
        return None
    msgs = sorted(msgs, key=lambda m: int(m.get("internal_ts") or 0))
    dom = (our_domain or "").lower().lstrip("@")
    last = msgs[-1]
    if not _ours(last.get("from", ""), dom) and is_ack(last.get("body") or ""):
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
