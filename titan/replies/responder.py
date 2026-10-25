"""Decide what to do with a classified reply. Pure function of (classification, context, config) → Action.

Conversation first (Rodrigo, 2026-10-24): the engine never sends a price, a counter or a commitment. It keeps a real
conversation going (one question at a time, never re-asking what the agent already answered) and brings the lead to
Rodrigo with a review summary as soon as there is meaningful engagement, a request for an offer, or a seller number.
Signals kept apart: RESPONDED (agent replied) < DISCUSSING (willing to talk) < FLEXIBLE (agent said the seller has room)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..util import first_name, pretty_phone, inspection_phrase
from .classifier import Classification


@dataclass
class Action:
    reply: str | None            # None → no outbound message
    thread_state: str            # OPEN | HOT_LEAD | CLOSED | STOPPED | ESCALATED
    lead_status: str | None      # property.lead_status to set, or None to leave
    note: str
    dnc: bool = False
    set_bridge_sent: bool = False
    offer_price: int | None = None
    review: bool = False         # bring this lead to Rodrigo (review summary + Asana card)
    signal: str | None = None    # RESPONDED | DISCUSSING | FLEXIBLE


def _terms_sentence(cfg: Any) -> str:
    f, t = cfg["formula"], cfg["outreach"]["terms"]
    bits = ["as-is"]
    bits.append(inspection_phrase(t))
    bits.append(f"{f['close_days']}-day close")
    if t.get("buyer_pays_closing"):
        bits.append("buyer pays closing costs")
    bits.append(f"${f['emd']:,.0f} EMD")
    return ", ".join(bits)


def _sign(cfg: Any, channel: str) -> str:
    """Reply signature: short form from the style guide (§1) on email, name + company on text."""
    op = cfg["operator"]
    if channel == "sms":
        return f"- {op['sender_name'].split()[0]}, {op.get('company_short', op['company'])}"
    return f"Best,\n\n{op['sender_name'].split()[0]}\n{op.get('company_short', op['company'])}\n{pretty_phone(op['sender_phone'])}"


def answer_question(text: str, ctx: dict[str, Any], cfg: Any, channel: str) -> tuple[str | None, list[str]]:
    """Facts-only answers. Returns (reply or None, unanswered topics)."""
    low = text.lower()
    op, f, t = cfg["operator"], cfg["formula"], cfg["outreach"]["terms"]
    facts: list[str] = []
    unanswered: list[str] = []
    if re.search(r"inspection|contingenc|due diligence", low):
        facts.append("it would be as-is with no inspection contingency" if not t.get("inspection_contingency") else f"it would be as-is and we only need {inspection_phrase(t)}")
    if re.search(r"closing cost|who pays|title|closing fee", low):
        facts.append("buyer pays closing costs" if t.get("buyer_pays_closing") else "closing costs split the standard way")
    if re.search(r"proof of funds|pof|verif|funds|bank letter", low):
        facts.append(f"proof of funds is {t.get('proof_of_funds', 'available on request')}")
    if re.search(r"earnest|emd|deposit|escrow deposit", low):
        facts.append(f"EMD is ${f['emd']:,.0f}")
    if re.search(r"how (fast|quick|soon)|timeline|time frame|timeframe|close by|closing date|when (can|could|would) you close|days to close", low):
        facts.append(f"we close in about {f['close_days']} days, flexible if the seller needs a different date")
    if re.search(r"financ|loan|mortgage|hard money|cash\b", low):
        facts.append("it's a cash purchase, no financing contingency")
    if re.search(r"who (are you|is this)|what company|are you (a |an )?(agent|realtor|licensed)|represent|your company|titan", low):
        facts.append(f"this is {op['sender_name'].split()[0]} with {op['company']}; we're looking for properties in {ctx.get('county') or 'South Florida'}")
    if re.search(r"assign|wholesal|double clos|end buyer|partner", low):
        unanswered.append("deal structure")
    if re.search(r"appraisal|survey|seller concession|lease ?back|post[- ]occupancy|rent ?back|repairs? credit|price\b.*\?|how much|offer\b.*\?", low):
        unanswered.append("terms outside config")
    if not facts:
        return None, unanswered or ["unrecognized question"]
    first = first_name(ctx.get("agent_name"))
    joined = "; ".join(facts)
    joined = joined[0].upper() + joined[1:]
    tail = " Anything else, let's get on a quick call and I can walk you through it." if unanswered else ""
    if channel == "sms":
        body = f"Hi {first}, {joined}.{tail} {_sign(cfg, 'sms')}"
    else:
        body = f"Hi {first},\n\nThank you for getting back to me. {joined}.{tail} Let me know if you have any further questions.\n\n{_sign(cfg, 'email')}"
    return body, unanswered


FLEX_RE = re.compile(r"\b((seller|owner|they|she|he)( is|'s| are| would be| might be| may be)? ?(very |pretty |definitely )?(motivated|flexible|negotiable|open to (offers|negotiat\w+|something|less))|"
                     r"some (flexibility|room|wiggle room)|there'?s (some )?room|price is negotiable|will(ing to)? (consider|entertain|look at) (less|offers|anything|all offers)|"
                     r"open to (reasonable |all |any )?offers|bring (me |us )?(an |any |all )?offers?|make (an|any) offer)\b", re.I)

# Next questions, in order; each is skipped once the agent has covered the topic (in any reply so far).
TOPICS = [
    ("condition", r"roof|\bac\b|a/c|hvac|condition|repairs?|updated|renovat|foundation|plumbing|electrical|as[- ]is|cosmetic|gut|dated|"
                  r"needs (a lot|some|little|no) work|move[- ]in ready|turn ?key",
     "How much work would you say it really needs? Mostly cosmetic, or is there something bigger going on with it?"),
    ("expectations", r"\$|\bnet\b|\bnumber\b|expect|hoping|bottom|lowest|price (would|they)|\d{3}k|realistic|firm",
     "Where do you think the seller realistically needs to be?"),
    ("timing", r"timing|timeline|clos(e|ing) (by|in|before|within)|\d+\s*days|weeks?|months?|asap|soon|date|relocat|moving",
     "What kind of timing is the seller working with?"),
    ("occupancy", r"vacant|occupied|tenant|lease|living there|empty",
     "Is anyone living there right now, or is it vacant?"),
]


def next_question(history_in: str, history_out: str) -> tuple[str, str] | None:
    """First topic the agent has not covered and we have not already asked about."""
    low_in, low_out = (history_in or "").lower(), (history_out or "").lower()
    for key, covered, q in TOPICS:
        if re.search(covered, low_in):
            continue
        if q.lower()[:30] in low_out:
            continue
        return key, q
    return None


def decide(cls: Classification, ctx: dict[str, Any], cfg: Any, channel: str, thread: dict[str, Any], text: str = "") -> Action:
    """ctx keys: agent_name, street, city, county, list_price, history_in (all agent replies so far, incl. this one),
    history_out (our earlier replies), auto_replies (how many conversation replies we already drafted on this thread)."""
    first = first_name(ctx.get("agent_name"))
    street = ctx.get("street") or "the property"
    label = cls.label
    sign = _sign(cfg, channel)
    flexible = bool(FLEX_RE.search(text or "")) or (label == "COUNTER_WITH_NUMBER" and cls.number and ctx.get("list_price")
                                                    and cls.number < ctx["list_price"])

    if label == "STOP":
        return Action(None, "STOPPED", None, "agent asked to stop; added to do-not-contact", dnc=True)
    if label == "OUT_OF_OFFICE":
        return Action(None, thread.get("state", "OPEN"), None, "auto-reply; waiting")
    if label == "SOLD_OR_PENDING":
        return Action(None, "CLOSED", "CLOSED", "property sold/pending; closed", signal="RESPONDED")
    if label == "NOT_INTERESTED":
        reply = None
        if cfg.get("outreach.reply_on_decline", True):
            reply = (f"Understood, thank you {first}. I appreciate you letting me know. If anything changes with the seller on {street}, "
                     f"feel free to reach out anytime.\n\n{sign}") if channel == "email" else \
                    f"Understood, thanks {first}. If anything changes on {street}, feel free to reach out. {sign}"
        return Action(reply, "CLOSED", "CLOSED", "seller firm / agent declined; closed respectfully (no pressure)", signal="RESPONDED")

    if label == "ASK_FOR_OFFER":
        # "Just send an offer" is an invitation to evaluate, not permission to send a number.
        reply = (f"Thanks {first}, I appreciate that. I don't want to throw a random number at you before we understand what we're actually "
                 f"buying, so let me get {street} in front of our acquisitions side and come back to you. In the meantime, is there anything "
                 f"on the condition or the seller's timing we should know?\n\n{sign}") if channel == "email" else \
                f"Thanks {first}! Let me run {street} through our side and get back to you with something real. Anything on condition or timing we should know? {sign}"
        return Action(reply, "ESCALATED", "NEEDS_REVIEW", "agent invited an offer: holding reply only, no number; Rodrigo reviews and decides the price",
                      review=True, signal="FLEXIBLE" if flexible else "DISCUSSING")

    if label == "COUNTER_WITH_NUMBER":
        n = cls.number
        reply = (f"Thanks {first}, that's helpful to know. Let me run that through our underwriting and see where it lands on our side. "
                 f"I'll get back to you shortly.\n\n{sign}") if channel == "email" else \
                f"Thanks {first}, that's helpful. Let me run it through our side and get back to you shortly. {sign}"
        return Action(reply, "ESCALATED", "NEEDS_REVIEW", f"agent shared a number ({'$' + format(n, ',') if n else 'unparsed'}): acknowledged, no counter; Rodrigo reviews",
                      review=True, signal="FLEXIBLE" if flexible else "DISCUSSING")

    if label == "INTERESTED":
        sig = "FLEXIBLE" if flexible else "DISCUSSING"
        done = int(ctx.get("auto_replies") or 0)
        nq = next_question(ctx.get("history_in") or text, ctx.get("history_out") or "")
        if done >= cfg.get("outreach.max_auto_questions", 2) or nq is None:
            return Action(None, "ESCALATED", "NEEDS_REVIEW", f"{sig.lower()}: enough back-and-forth; Rodrigo reviews before any number",
                          review=True, signal=sig)
        lead = "That's good to hear, that's a lot closer to what we like." if flexible else "Thanks for getting back to me."
        reply = (f"Hi {first},\n\n{lead} {nq[1]}\n\n{sign}") if channel == "email" else f"{lead} {nq[1]} {sign}"
        return Action(reply, "OPEN", sig, f"{sig.lower()}: asked about {nq[0]}", review=flexible, signal=sig)

    # QUESTION
    reply, unanswered = answer_question(text, ctx, cfg, channel)
    if reply is None or "terms outside config" in unanswered:
        return Action(None, "ESCALATED", "NEEDS_REVIEW", f"question we should not answer automatically ({', '.join(unanswered)}); Rodrigo reviews",
                      review=True, signal="DISCUSSING")
    return Action(reply, "OPEN", "DISCUSSING", "answered from config facts" + (f"; unanswered: {', '.join(unanswered)}" if unanswered else ""),
                  signal="DISCUSSING")
