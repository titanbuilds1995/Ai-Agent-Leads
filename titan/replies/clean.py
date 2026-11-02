"""The agent's words only (Rodrigo, 2026-11-02: "when we get their emails back, there's no need for us to paste all of their
footer there"). Quoted history, closing lines, name / phone / license / link / image signature blocks and "Sent from my
iPhone" are dropped before a reply is shown on a card, summarised in WHERE WE STAND or handed to Claude as thread context.
The stored message keeps the full text; this is display-side only, so nothing is lost if a rule is too eager."""
from __future__ import annotations

import re

CLOSING_RE = re.compile(r"^\s*(best|kind|warm|warmest|many thanks|thanks( again| so much)?|thank you( again| so much)?|regards|best regards|kind regards|"
                        r"warm regards|sincerely|cheers|respectfully|take care|talk soon|all the best|have a (great|good|nice|blessed) (day|one|weekend)|"
                        r"god bless)[\s,.!]*$", re.I)
SIG_LINE_RE = re.compile(
    r"^\s*(\[image:.*\]|<?https?://\S+>?|www\.\S+|\S+\.(com|net|org|realtor|realty|homes)(/\S*)?|"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|"
    r"((p|o|c|m|f|d|t|ph|tel|cell|mobile|office|direct|fax|phone|work)\s*[:.]\s*)?\(?\+?1?[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(\s*(x|ext\.?)\s*\d+)?|"
    r"(fl\.?\s*)?(licen[sc]ed?|lic\.?|license\s*#?|realtor|broker( associate)?|sales associate|associate|realtor®|re/max.*|keller williams.*|"
    r"coldwell banker.*|compass.*|douglas elliman.*|sotheby.*|exp realty.*|lpt realty.*|united realty.*|real broker.*|.*realty.*|.*properties.*|.*real estate.*|"
    r".*group,? (inc|llc)\.?|.*,? (inc|llc|pa|p\.a\.)\.?)(\s*#?\s*[A-Z]{0,3}\d{4,}.*)?|"
    r"(license|lic)\.?\s*(#|no\.?|number)?\s*[A-Z]{0,3}\d{4,}|#?\s*[A-Z]{1,3}\d{5,}|"
    r"sent from my .*|get outlook for .*|sent via .*|confidentiality notice.*|this (e-?mail|message) (is|may be|contains).*|"
    r"[-_=*·•]{2,}|\|.*|.*\|)\s*$", re.I)
NAME_LINE_RE = re.compile(r"^\s*(?:[A-Z][A-Za-z'’.-]*\.?\s*){1,4}(?:,?\s*(?:P\.?A\.?|LLC|Jr\.?|Sr\.?|III|II|CRS|GRI|ABR|SRES|MBA|CDPE|e-?PRO))*\s*$")
GREETING_RE = re.compile(r"^\s*(hi|hey|hello|good (morning|afternoon|evening)|dear)( there| again)?( [A-Za-z'’.-]+){0,2}[,:!.]?\s*$", re.I)
SENTENCE_RE = re.compile(r"\b\w+\b.*\b\w+\b.*\b\w+\b.*\b\w+\b")   # four or more words = probably a sentence, not a signature line


def _is_signature_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if SIG_LINE_RE.match(s):
        return True
    if NAME_LINE_RE.match(s) and len(s.split()) <= 4 and not s.endswith((".", "?", "!")):
        return True
    return False


def clean_reply(text: str) -> str:
    """Quoted history, closing and signature removed; what the agent actually said, trimmed."""
    from ..outreach.gmail_client import strip_quoted
    raw = strip_quoted(text or "")
    text = re.sub(r"\[image:[^\]]*\]", "", raw)
    lines = [ln.rstrip() for ln in text.splitlines()]
    while lines and GREETING_RE.match(lines[0]):          # "Hi Charles," on its own line says nothing
        lines.pop(0)
    # 1. cut at the LAST closing line ("Best regards,") when nothing after it reads like a sentence
    for i in range(len(lines) - 1, -1, -1):
        if CLOSING_RE.match(lines[i]) and i > 0:
            tail = [ln for ln in lines[i + 1:] if ln.strip()]
            if all(not SENTENCE_RE.search(ln) or _is_signature_line(ln) for ln in tail):
                lines = lines[:i]
                break
    # 2. peel signature-looking lines off the end (name, phone, license, links, images, brokerage)
    while lines and _is_signature_line(lines[-1]) and (not lines[-1].strip() or not SENTENCE_RE.search(lines[-1]) or SIG_LINE_RE.match(lines[-1].strip())):
        if NAME_LINE_RE.match(lines[-1].strip() or "x") and not SIG_LINE_RE.match(lines[-1].strip()) and not any(l.strip() for l in lines[:-1]):
            break                                          # a lone "Yes" or "Sure" is the answer, not a name
        lines.pop()
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    return out.strip() or raw.strip()


def one_line(text: str, limit: int = 160) -> str:
    """A reply squeezed onto one line for a status block ("their last words")."""
    s = " ".join(clean_reply(text).split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"
