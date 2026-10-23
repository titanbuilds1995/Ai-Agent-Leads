"""Decide what to do with a classified reply. Pure function of (classification, context, config) → Action.
Never commits beyond config.outreach.terms; never invents facts."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..util import first_name, pretty_phone
from .classifier import Classification


@dataclass
class Action:
    reply: str | None            # None → no outbound message
    thread_state: str            # OPEN | HOT_LEAD | CLOSED | STOPPED | ESCALATED
    lead_status: str | None      # property.lead_status to set, or None to leave
    note: str
    dnc: bool = False
    set_bridge_sent: bool = False
    handoff: bool = False        # SMS path: run the text-to-email handoff
    offer_price: int | None = None


def _terms_sentence(cfg: Any) -> str:
    f, t = cfg["formula"], cfg["outreach"]["terms"]
    bits = ["as-is"]
    bits.append("no inspection contingency" if not t.get("inspection_contingency") else "short inspection period")
    bits.append(f"{f['close_days']}-day close")
    if t.get("buyer_pays_closing"):
        bits.append("buyer pays closing costs")
    bits.append(f"${f['emd']:,.0f} EMD")
    return ", ".join(bits)


def _sign(cfg: Any, channel: str) -> str:
    op = cfg["operator"]
    if channel == "sms":
        return f"- {op['sender_name'].split()[0]}, {op.get('company_short', op['company'])}"
    return f"{op['sender_name'].split()[0]}\n{op['company']}\n{pretty_phone(op['sender_phone'])}"


def answer_question(text: str, ctx: dict[str, Any], cfg: Any, channel: str) -> tuple[str | None, list[str]]:
    """Facts-only answers. Returns (reply or None, unanswered topics)."""
    low = text.lower()
    op, f, t = cfg["operator"], cfg["formula"], cfg["outreach"]["terms"]
    facts: list[str] = []
    unanswered: list[str] = []
    if re.search(r"inspection|contingenc|due diligence", low):
        facts.append("we buy as-is with no inspection contingency" if not t.get("inspection_contingency") else "we only need a short inspection period")
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
        facts.append(f"this is {op['sender_name']} with {op['company']}, a local cash buyer in {ctx.get('county') or 'South Florida'}")
    if re.search(r"assign|wholesal|double clos|end buyer|partner", low):
        unanswered.append("deal structure")
    if re.search(r"appraisal|survey|seller concession|lease ?back|post[- ]occupancy|rent ?back|repairs? credit|price\b.*\?|how much|offer\b.*\?", low) and not facts:
        unanswered.append("terms outside config")
    if not facts:
        return None, unanswered or ["unrecognized question"]
    first = first_name(ctx.get("agent_name"))
    joined = "; ".join(facts)
    joined = joined[0].upper() + joined[1:]
    tail = f" Anything else, {op['sender_name'].split()[0]} can cover it on a quick call." if unanswered else ""
    if channel == "sms":
        body = f"Hi {first}, {joined}.{tail} {_sign(cfg, 'sms')}"
    else:
        body = f"Hi {first},\n\n{joined}.{tail}\n\n{_sign(cfg, 'email')}"
    return body, unanswered


def decide(cls: Classification, ctx: dict[str, Any], cfg: Any, channel: str, thread: dict[str, Any], text: str = "") -> Action:
    """ctx keys: agent_name, street, city, county, list_price, mao, offer_price."""
    first = first_name(ctx.get("agent_name"))
    street = ctx.get("street") or "the property"
    mao, offer = ctx.get("mao"), ctx.get("offer_price")
    label = cls.label

    if label == "STOP":
        return Action(None, "STOPPED", None, "agent asked to stop; added to do-not-contact", dnc=True)
    if label == "OUT_OF_OFFICE":
        return Action(None, thread.get("state", "OPEN"), None, "auto-reply; waiting")
    if label == "SOLD_OR_PENDING":
        return Action(None, "CLOSED", "CLOSED", "property sold/pending; closed")
    if label == "NOT_INTERESTED":
        reply = None
        if channel == "email" and cfg.get("outreach.reply_on_decline", True):
            reply = f"Understood, thanks {first}. If anything changes on {street}, I'm a call away.\n\n{_sign(cfg, 'email')}"
        return Action(reply, "CLOSED", "CLOSED", "agent declined; closed")

    if channel == "sms" and label in ("INTERESTED", "ASK_FOR_OFFER", "COUNTER_WITH_NUMBER"):
        # Section 7a: positive text reply → written offer by email, no human step.
        return Action(None, "HOT_LEAD", "HOT_LEAD", f"{label} by text; text-to-email handoff", handoff=True, offer_price=offer)

    if label == "INTERESTED":
        return Action(None, "HOT_LEAD", "HOT_LEAD", "positive reply; auto-reply off, Rodrigo takes over")

    if label == "ASK_FOR_OFFER":
        if not offer:
            return Action(None, "ESCALATED", "NEEDS_CALL", "asked for offer but no valuation on file")
        reply = (f"Hi {first},\n\nThanks for getting back to me. Based on what we see, we'd be at ${offer:,.0f} for {street}: {_terms_sentence(cfg)}, "
                 f"proof of funds on request. If that's in the ballpark I'll send the written offer right over.\n\n{_sign(cfg, 'email')}")
        return Action(reply, "OPEN", None, f"offer {offer} sent with terms", offer_price=offer)

    if label == "COUNTER_WITH_NUMBER":
        n = cls.number
        if not n or not mao:
            return Action(None, "ESCALATED", "HOT_LEAD", "counter without parsable number or no MAO; escalate")
        within = n <= mao * (1 + cfg["formula"]["counter_accept_within_pct"] / 100)
        if within:
            reply = (f"Thanks {first}, ${n:,.0f} is close enough to work with on {street}. {cfg['operator']['sender_name'].split()[0]} will call you directly "
                     f"to lock down the details. Same terms on our side: {_terms_sentence(cfg)}.\n\n{_sign(cfg, 'email')}")
            return Action(reply, "HOT_LEAD", "HOT_LEAD", f"counter {n} within {cfg['formula']['counter_accept_within_pct']}% of MAO {mao}; HOT_LEAD")
        if not thread.get("bridge_sent"):
            bridge = int(round((mao + n) / 2, -3))
            if ctx.get("list_price"):
                bridge = min(bridge, int(ctx["list_price"]))
            reply = (f"Appreciate it, {first}. ${n:,.0f} is above where the numbers work for us as-is on {street}. Could the seller meet us at ${bridge:,.0f}? "
                     f"Same terms: {_terms_sentence(cfg)}, proof of funds on request.\n\n{_sign(cfg, 'email')}")
            return Action(reply, "OPEN", None, f"counter {n} > MAO {mao}; bridge {bridge} sent", set_bridge_sent=True, offer_price=bridge)
        return Action(None, "ESCALATED", "HOT_LEAD", f"counter {n} after bridge; escalated to Rodrigo")

    # QUESTION
    reply, unanswered = answer_question(text, ctx, cfg, channel)
    if reply is None:
        return Action(None, "ESCALATED", "NEEDS_CALL", f"question outside config facts ({', '.join(unanswered)}); call the agent")
    return Action(reply, "OPEN", None, "answered from config facts" + (f"; unanswered: {', '.join(unanswered)}" if unanswered else ""))
