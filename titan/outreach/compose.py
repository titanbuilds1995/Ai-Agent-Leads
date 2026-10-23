"""Draft text for first-touch emails and texts. Claude writes in the sender's voice; templates are the fallback.
Every draft passes validate_email()/validate_sms() before it is stored."""
from __future__ import annotations

import hashlib
import json
import random
import re
from typing import Any

from ..llm import LLM
from ..util import first_name, pretty_phone, inspection_phrase

URL_RE = re.compile(r"https?://|www\.|\.com/|\.net/|\.org/", re.I)
HTML_RE = re.compile(r"<[a-zA-Z/][^>]*>")

EMAIL_SCHEMA = {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
                "required": ["subject", "body"], "additionalProperties": False}

EMAIL_SYSTEM = """You write short first-touch emails from a real estate acquisitions person to a listing agent. Plain text only. No HTML, no links, no images, no bullet points, no subject-line hype.

Voice: match these real emails the sender has written (tone, length, directness):
{examples}

Hard rules:
- 60–120 words. One paragraph or two short ones. Sign with the sender's first name and company.
- Reference each property by its street address and one concrete detail from its remarks (quote or paraphrase, no invention).
- Say we are a local cash buyer in the property's county.
- Ask whether the seller is open to an offer below list and what number would get it done.
- Offer to close in {close_days} days, as-is, with {inspection}.
- Do not state a dollar figure. Do not promise anything beyond those terms. Do not mention being an AI.
- If several properties are listed, cover all of them in one email.
- Vary the wording; do not reuse the example emails verbatim.
Return JSON: subject (under 60 chars, plain, mentions the street) and body."""

EMAIL_TEMPLATES = [
    ("{first}, quick question on {street}",
     "Hi {first},\n\n{sender} here with {company_short}. We're a local cash buyer in {county} and your listing at {street} caught my eye, mostly because {detail}. "
     "Is the seller open to an offer under list if we close as-is in {close_days} days with {inspection}? If so, what number do you think gets it done?\n\n{sign}"),
    ("Cash offer on {street}?",
     "Hey {first},\n\nI'm {sender} with {company_short}, we buy houses in {county} for cash. Saw {street} and noticed {detail}. Not trying to waste your time or lowball anyone, "
     "I just want to know if the seller would consider something below asking for a clean {close_days}-day close, as-is, buyer pays closing. What range would they actually take?\n\n{sign}"),
    ("{street} - as-is cash buyer",
     "Hi {first},\n\nThis is {sender} with {company}. Noticed {street} in {city}, and that {detail}. We're local cash buyers in {county} and can close in about {close_days} days as-is, "
     "{inspection}, proof of funds on request. Is the seller open to a number below list? If yes, what would it take?\n\n{sign}"),
    ("Question about {street}",
     "{first},\n\n{sender} with {company_short} here. Your listing at {street} looks like it could fit what we buy in {county}, especially since {detail}. "
     "If the seller is motivated I'd rather make a real as-is cash offer, {close_days}-day close, than go back and forth. Are they open to something under list, and roughly where?\n\n{sign}"),
]
MULTI_TEMPLATE = ("{first}, a few of your listings in {county}",
                  "Hi {first},\n\n{sender} here with {company_short}, local cash buyer in {county}. A few of your listings look like they could fit us:\n{lines}\n"
                  "Would any of those sellers consider an offer below list for an as-is, {close_days}-day close with {inspection}? If so, which ones and roughly what number?\n\n{sign}")

SMS_TEMPLATES = [
    "Hi {first}, this is {sender} w/ {company_short}, local cash buyer. Saw your listing at {street}. Is the seller open to an offer under list for an as-is close in {close_days} days? {phone}",
    "{first}, {sender} with {company_short} here (cash buyer). Re {street}: would the seller take something below list for a {close_days}-day as-is close? {phone}",
    "Hi {first}, {sender} from {company_short}. Quick q on {street}: seller open to an as-is cash offer under list, closing in ~{close_days} days? {phone}",
    "{first}, this is {sender} ({company_short}, local cash buyer). Interested in {street}. Any flexibility below list for a fast as-is close? {phone}",
]


def _detail(p: dict[str, Any]) -> str:
    """One concrete thing from the listing to prove we read it."""
    cited = p.get("cited")
    if isinstance(cited, str):
        try:
            cited = json.loads(cited)
        except json.JSONDecodeError:
            cited = []
    prio = ["needs_work", "damage", "estate_probate", "price_reduced", "life_event", "bring_all_offers", "code_violations", "tenant_occupied",
            "distress_status", "investor_special", "motivated_seller", "as_is", "cash_only", "seller_financing", "vacant"]
    cands = [c for c in (cited or []) if c.get("phrase") and "=" not in c.get("phrase", "")]
    cands.sort(key=lambda c: prio.index(c["signal"]) if c.get("signal") in prio else 99)
    if cands:
        ph = re.sub(r"^\W+|\W+$", "", cands[0]["phrase"].strip())
        if ph.isupper():
            ph = ph.lower()
        return f'the remarks mention "{ph}"'
    if (p.get("price_cuts") or 0) >= 2:
        return f"it has had {p['price_cuts']} price reductions"
    if (p.get("days_on_market") or 0) > 60:
        return f"it has been on the market {p['days_on_market']} days"
    if p.get("price_cuts"):
        return "the price was just reduced"
    return "it looks like it could use some work"


def seed_for(agent_id: int, property_ids: list[int], run_date: str) -> int:
    h = hashlib.sha1(f"{agent_id}:{sorted(property_ids)}:{run_date}".encode()).hexdigest()
    return int(h[:8], 16)


def _ctx(cfg: Any, agent: dict[str, Any], props: list[dict[str, Any]]) -> dict[str, Any]:
    op = cfg["operator"]
    p = props[0]
    sign = f"{op['sender_name'].split()[0]}\n{op['company']}\n{pretty_phone(op['sender_phone'])}\n{op.get('mailing_address','')}".strip()
    return {"first": first_name(agent.get("name")), "sender": op["sender_name"].split()[0], "company": op["company"],
            "company_short": op.get("company_short", op["company"]), "phone": pretty_phone(op["sender_phone"]), "county": p.get("county") or "South Florida",
            "city": p.get("city") or "", "street": p.get("street") or "", "detail": _detail(p), "close_days": cfg["formula"]["close_days"], "inspection": inspection_phrase(cfg["outreach"]["terms"]),
            "sign": sign, "lines": "\n".join(f"- {q['street']}, {q['city']}: {_detail(q)}" for q in props)}


def template_email(cfg: Any, agent: dict[str, Any], props: list[dict[str, Any]], seed: int) -> tuple[str, str, str]:
    ctx = _ctx(cfg, agent, props)
    rnd = random.Random(seed)
    if len(props) > 1:
        subj, body = MULTI_TEMPLATE
        variant = "multi"
    else:
        i = rnd.randrange(len(EMAIL_TEMPLATES))
        subj, body = EMAIL_TEMPLATES[i]
        variant = f"t{i}"
    return subj.format(**ctx), body.format(**ctx), variant


def llm_email(llm: LLM | None, cfg: Any, agent: dict[str, Any], props: list[dict[str, Any]]) -> tuple[str, str] | None:
    if llm is None or not llm.available:
        return None
    op = cfg["operator"]
    examples = "\n---\n".join(e.strip() for e in cfg.get("outreach.example_emails", []))
    user = json.dumps({
        "agent_first_name": first_name(agent.get("name")), "sender": op["sender_name"], "company": op["company"], "phone": op["sender_phone"],
        "properties": [{"street": p["street"], "city": p["city"], "county": p["county"], "list_price": p["list_price"],
                        "remarks_excerpt": (p.get("remarks") or "")[:800], "detail_hint": _detail(p), "days_on_market": p.get("days_on_market"),
                        "price_cuts": p.get("price_cuts")} for p in props],
    }, ensure_ascii=False)
    out = llm.structured("draft_email", EMAIL_SYSTEM.format(examples=examples, close_days=cfg["formula"]["close_days"], inspection=inspection_phrase(cfg["outreach"]["terms"])), user, EMAIL_SCHEMA, max_tokens=700)
    if not out:
        return None
    return out["subject"].strip(), out["body"].strip()


def validate_email(subject: str, body: str, props: list[dict[str, Any]]) -> list[str]:
    problems = []
    if HTML_RE.search(body) or HTML_RE.search(subject):
        problems.append("html")
    if URL_RE.search(body):
        problems.append("link")
    if not any((p.get("street") or "").split(" ")[0] in body for p in props):
        problems.append("no address")
    words = len(body.split())
    if words < 35 or words > 220:
        problems.append(f"length {words}")
    if "cash" not in body.lower():
        problems.append("no cash buyer mention")
    if "?" not in body:
        problems.append("no question")
    if re.search(r"\$\s?\d", body):
        problems.append("dollar figure")
    if len(subject) > 90 or not subject.strip():
        problems.append("subject")
    return problems


def compose_email(llm: LLM | None, cfg: Any, agent: dict[str, Any], props: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    res = llm_email(llm, cfg, agent, props)
    if res:
        subject, body = res
        if not validate_email(subject, body, props):
            return {"subject": subject, "body": body, "variant": "claude"}
    subject, body, variant = template_email(cfg, agent, props, seed)
    problems = validate_email(subject, body, props)
    if problems:
        raise ValueError(f"template email failed validation: {problems}")
    return {"subject": subject, "body": body, "variant": variant}


def compose_sms(cfg: Any, agent: dict[str, Any], prop: dict[str, Any], seed: int) -> dict[str, Any]:
    ctx = _ctx(cfg, agent, [prop])
    order = list(range(len(SMS_TEMPLATES)))
    random.Random(seed).shuffle(order)
    for i in order:
        body = SMS_TEMPLATES[i].format(**ctx)
        if len(body) <= 160:
            return {"body": body, "variant": f"s{i}"}
    # street too long: shorten
    ctx["street"] = (ctx["street"][:28] + "…") if len(ctx["street"]) > 29 else ctx["street"]
    body = SMS_TEMPLATES[2].format(**ctx)
    return {"body": body[:160], "variant": "s2-short"}


def validate_sms(body: str) -> list[str]:
    problems = []
    if len(body) > 160:
        problems.append("length")
    if URL_RE.search(body) or HTML_RE.search(body):
        problems.append("link/html")
    return problems


def compose_offer_email(cfg: Any, agent: dict[str, Any], prop: dict[str, Any], offer_price: int) -> dict[str, str]:
    """The written offer sent after a positive text reply (section 7a) or on ASK_FOR_OFFER by email."""
    op, f, terms = cfg["operator"], cfg["formula"], cfg["outreach"]["terms"]
    ctx = _ctx(cfg, agent, [prop])
    body = (
        f"Hi {ctx['first']},\n\n"
        f"As discussed, here is our offer on {prop['street']}, {prop['city']}:\n\n"
        f"Offer price: ${offer_price:,.0f}\n"
        f"Terms: as-is, {inspection_phrase(terms)}, "
        f"{f['close_days']}-day close, {'buyer pays closing costs' if terms.get('buyer_pays_closing') else 'standard closing cost split'}, "
        f"${f['emd']:,.0f} earnest money deposit, proof of funds {terms.get('proof_of_funds', 'available on request')}.\n\n"
        f"If the seller is agreeable I can send the contract over right away. Happy to adjust the closing date if they need more or less time.\n\n"
        f"{ctx['sign']}"
    )
    return {"subject": f"Written offer - {prop['street']}", "body": body}
