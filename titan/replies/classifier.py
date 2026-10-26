"""Classify an agent reply into one of 8 classes. Rules first (deterministic, fast); Claude only for ambiguous text."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..llm import LLM

CLASSES = ["BOUNCE", "INTERESTED", "ASK_FOR_OFFER", "COUNTER_WITH_NUMBER", "NOT_INTERESTED", "SOLD_OR_PENDING", "QUESTION", "OUT_OF_OFFICE", "STOP"]


@dataclass
class Classification:
    label: str
    confidence: float
    number: int | None = None          # parsed counter/ask price when present
    email: str | None = None           # an email address the agent gave us
    method: str = "rules"
    reasons: list[str] = field(default_factory=list)


STOP_RE = re.compile(r"\b(stop|unsubscribe|remove me|take me off|do not (contact|email|text|message)( me)?|don'?t (contact|email|text)( me)?( again)?|"
                     r"stop (emailing|texting|contacting|messaging)( me)?|opt ?out|no more (emails|texts|messages)|leave me alone|cease)\b", re.I)
OOO_RE = re.compile(r"\b(out of (the )?office|ooo|on vacation|away from (my )?(email|desk)|auto[\s-]?(reply|response|responder)|automatic reply|"
                    r"limited access to email|will (be )?(return|be back|respond) (on|after|when)|currently (away|unavailable|traveling))\b", re.I)
SOLD_RE = re.compile(r"\b(under contract|pending|sold|accepted (an )?offer|went (to )?contract|in escrow|closed|contingent|"
                     r"(no longer|not) (available|on the market|for sale)|off (the )?market|withdrawn|already (sold|under contract|has a contract)|"
                     r"executed contract|we have a contract|signed contract|cancelled|canceled)\b", re.I)
NOT_RE = re.compile(r"\b(not interested|no interest|no thanks|no thank you|pass|not (looking|going) to (entertain|consider|accept)|won'?t (go|take|accept|consider|entertain)|"
                    r"will not (go|take|accept|consider|entertain)|firm (on|at) (the )?(price|list)|(seller|owner|they|we) (is|are|'s)? ?(not|n't) (interested|motivated|open|willing|looking)|"
                    r"full (list )?price only|no lowball|not (selling|entertaining) (below|under)|(don'?t|do not) (waste|bother)|only (considering|accepting) (full|list|retail)|"
                    r"wants? (full|list) price|(isn'?t|is not|won'?t be) (going|willing) (below|under)|(seller|they) (said|says) no\b|no (room|flexibility)|not open to)\b", re.I)
ASK_RE = re.compile(r"\b(send (me |us |over |it )?(your |the |an? |a written )?(best |highest |cash |written |formal )?(offer|number|price|bid|proposal|contract|pof|proof of funds)|"
                    r"bring (me |us )?(your |an |a |the )?(best |highest |strongest |cash |written )?(offer|number|price|bid)|(best|highest) and final|"
                    r"what('?s| is| would be| are) (your|the) (offer|number|price|bid|best|range|budget)|where are you (at|thinking)|what (are you|were you|would you) (thinking|offering|paying|pay|offer)|"
                    r"(make|submit|put in|write up|shoot me|give me|throw out|email me) (an |your |the |a |me )?(offer|number|price|bid|something)|"
                    r"how much (are you|would you|can you|will you)|(put|get) (it|something|an offer) in writing|(seller|they|owner) (will|would|is open to|wants? to) (review|look at|see|consider|entertain) (it|an offer|your offer|offers|any offer|all offers)|"
                    r"happy to (present|submit|bring)|i'?ll present|present (it|anything|any offer)|ballpark|what number|your number|what (did|do) you have in mind)\b", re.I)
INTERESTED_RE = re.compile(r"\b(yes|yeah|yep|sure|absolutely|definitely|open to (it|offers|that|an offer|a conversation)|(is|are|would be|might be) (open|flexible|motivated|willing|receptive)|"
                           r"call me|give me a call|let'?s (talk|chat|discuss|connect|set up)|(seller|they|owner) (is|are|'s) (motivated|flexible|open|willing|negotiable)|"
                           r"some (flexibility|room|wiggle room)|there'?s (some )?room|negotiable|interested|would (consider|entertain|look at)|open to (negotiat\w+|discussing)|"
                           r"can (you |we )?(call|talk|chat)|when can (you|we)|love to|happy to (talk|chat|discuss)|sounds good|let me know when)\b", re.I)
CONDITION_RE = re.compile(r"\b(roof|a/?c\b|hvac|air cond|window units?|kitchen|bath(room)?s?|electrical|plumbing|wiring|panel|gut(ted)?|cosmetic|"
                          r"tenants?|lease|rent(s|ed|al)?|occupied|vacant|inherited|estate|probate|permits?|foundation|septic|windows|floors?|"
                          r"needs? (a |new |some )?(roof|work|everything|updating)|original|as.is|handyman|teardown|tear down)\b", re.I)
QUESTION_RE = re.compile(r"\?|\b(who (are you|is this)|what (company|is titan)|are you (a |an )?(agent|realtor|wholesaler|licensed|the buyer|buying yourself)|"
                         r"do you (need|require|have|want|work)|proof of funds|pof|earnest|emd|deposit|inspection|contingenc|closing cost|how (fast|quick|soon)|"
                         r"timeline|financing|assign|double clos|hard money|how (do|does) (this|it) work|can you (close|send|provide)|will you)\b", re.I)

MONEY_RE = re.compile(r"\$?\s?(\d{1,3}(?:[,\s]\d{3})+|\d+(?:\.\d+)?)\s?(k|K|thousand|mm?|M|million)?\b")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
COUNTER_CONTEXT_RE = re.compile(r"\b(at|for|around|about|take|accept|consider|entertain|looking for|wants?|needs?|minimum|lowest|bottom|floor|no less than|not (less|below)|under|above|north of|"
                                r"offer|counter|number|price|range|close(r)? to|would (do|go|take|sell)|get it done|gets? it done|beat|has? (an|a cash|another) offer|in hand|asking|firm)\b", re.I)


def parse_money(text: str, list_price: int | None = None) -> int | None:
    """Return the most plausible price in the text. '310' → 310,000 when list price is in the hundreds of thousands."""
    cands: list[int] = []
    for m in MONEY_RE.finditer(text):
        num, suffix = m.group(1), (m.group(2) or "").lower()
        # ignore things like phone numbers, dates, percentages, times, bedroom counts
        tail = text[m.end():m.end() + 2]
        head = text[max(0, m.start() - 1):m.start()]
        if "%" in tail or tail.startswith(("am", "pm", "/", "-", "th", "st", "nd", "rd", "br", "bd", "ba")) or head in ("/", "-", "#"):
            continue
        if re.fullmatch(r"\d{1,2}", num) and not suffix:
            continue
        v = float(num.replace(",", "").replace(" ", ""))
        if suffix in ("k", "thousand"):
            v *= 1_000
        elif suffix in ("m", "mm", "million"):
            v *= 1_000_000
        elif v < 10_000:
            if "." in num and v < 10 and list_price and 100_000 <= list_price < 1_000_000:
                v *= 100_000                                  # "3.9" → 390k
            elif v < 1_000 and list_price and list_price >= 100_000:
                v *= 1_000                                    # "310" → 310k
            elif 1_000 <= v < 10_000 and list_price and list_price >= 1_000_000:
                v *= 1_000
            elif v < 1_000:
                continue
        if 20_000 <= v <= 50_000_000:
            cands.append(int(v))
    if not cands:
        return None
    if list_price:
        return min(cands, key=lambda c: abs(c - list_price))
    return cands[0]


BOUNCE_RE = re.compile(r"(couldn'?t|could not|was not|wasn'?t|cannot|can'?t) be delivered|undeliverable|delivery (status notification|failure|has failed|incomplete)|"
                       r"mail delivery (failed|subsystem)|mailer-daemon|postmaster@|address not found|(mailbox|inbox) (is )?(full|unavailable|not found|does not exist)|"
                       r"recipient (inbox|mailbox) full|user unknown|no such user|does not exist|over quota|\b5\.[1247]\.\d\b|\b55[0-4]\b.{0,40}(user|mailbox|recipient|address)", re.I)


def classify_rules(text: str, list_price: int | None = None) -> Classification:
    t = (text or "").strip()
    low = t.lower()
    reasons: list[str] = []
    email = None
    em = EMAIL_RE.search(t)
    if em:
        email = em.group(0).lower()
    if not t:
        return Classification("QUESTION", 0.2, email=email, reasons=["empty"])
    if BOUNCE_RE.search(low):      # a mail server talking, not the agent: never a counter, never a conversation
        return Classification("BOUNCE", 0.98, reasons=["delivery failure notice"])
    if STOP_RE.search(low):
        return Classification("STOP", 0.95, email=email, reasons=["stop phrase"])
    if OOO_RE.search(low) and len(low) < 1200:
        return Classification("OUT_OF_OFFICE", 0.9, email=email, reasons=["ooo phrase"])
    if SOLD_RE.search(low) and not re.search(r"\b(not|n't|isn'?t|no longer|fell|back on|was|hasn'?t|haven'?t|before it goes|unless|if)\b[^.]{0,25}\b(under contract|pending|sold|contract|escrow)", low):
        return Classification("SOLD_OR_PENDING", 0.9, email=email, reasons=["sold/pending phrase"])
    number = parse_money(t, list_price)
    if number and COUNTER_CONTEXT_RE.search(low):
        return Classification("COUNTER_WITH_NUMBER", 0.85, number=number, email=email, reasons=["number with price context"])
    if ASK_RE.search(low):
        return Classification("ASK_FOR_OFFER", 0.85, email=email, reasons=["asks for offer"])
    if NOT_RE.search(low):
        return Classification("NOT_INTERESTED", 0.85, email=email, reasons=["decline phrase"])
    if number:
        return Classification("COUNTER_WITH_NUMBER", 0.6, number=number, email=email, reasons=["bare number"])
    if INTERESTED_RE.search(low) and not QUESTION_RE.search(low):
        return Classification("INTERESTED", 0.75, email=email, reasons=["positive phrase"])
    if QUESTION_RE.search(low):
        if INTERESTED_RE.search(low) and len(low) < 160:
            return Classification("INTERESTED", 0.6, email=email, reasons=["positive + question"])
        return Classification("QUESTION", 0.7, email=email, reasons=["question"])
    if INTERESTED_RE.search(low):
        return Classification("INTERESTED", 0.6, email=email, reasons=["positive phrase"])
    if CONDITION_RE.search(low):   # the agent is telling us about the house (roof, AC, tenants, rents...): keep the conversation going
        return Classification("INTERESTED", 0.55, email=email, reasons=["condition or situation details, no question"])
    return Classification("QUESTION", 0.3, email=email, reasons=["unmatched"])


LLM_SCHEMA = {"type": "object", "properties": {
    "label": {"type": "string", "enum": CLASSES}, "confidence": {"type": "number"},
    "number": {"type": ["integer", "null"]}, "reason": {"type": "string"}},
    "required": ["label", "confidence", "number", "reason"], "additionalProperties": False}

LLM_SYSTEM = """Classify a listing agent's reply to a cash buyer's inquiry. Exactly one label:
INTERESTED (positive, wants to talk, seller open), ASK_FOR_OFFER (asks us to send a number/offer/POF), COUNTER_WITH_NUMBER (states a price the seller wants or a competing offer to beat; include the number in dollars),
NOT_INTERESTED (declines, firm at list), SOLD_OR_PENDING (under contract/sold/off market), QUESTION (asks about us or our terms without saying yes/no), OUT_OF_OFFICE (auto-reply), STOP (asks not to be contacted).
Precedence when several apply: STOP > OUT_OF_OFFICE > SOLD_OR_PENDING > COUNTER_WITH_NUMBER > ASK_FOR_OFFER > NOT_INTERESTED > QUESTION > INTERESTED.
'310' in a price context means $310,000 when the list price is in the hundreds of thousands."""


def classify(text: str, list_price: int | None = None, llm: LLM | None = None) -> Classification:
    c = classify_rules(text, list_price)
    if c.confidence >= 0.75 or llm is None or not llm.available:
        return c
    out = llm.structured("classify_reply", LLM_SYSTEM, json.dumps({"list_price": list_price, "reply": text[:3000]}), LLM_SCHEMA, max_tokens=200)
    if not out or out.get("label") not in CLASSES:
        return c
    num = out.get("number") or c.number
    return Classification(out["label"], float(out.get("confidence", 0.7)), number=num, email=c.email, method="claude", reasons=[out.get("reason", "")])
