"""Draft text for first-touch emails and texts. Claude writes in the sender's voice; templates are the fallback.
Every draft passes validate_email()/validate_sms() before it is stored."""
from __future__ import annotations

import hashlib
import json
import random
import re
from typing import Any

from pathlib import Path

from ..llm import LLM
from ..util import first_name, pretty_phone, inspection_phrase, log

URL_RE = re.compile(r"https?://|www\.|\.com/|\.net/|\.org/", re.I)
HTML_RE = re.compile(r"<[a-zA-Z/][^>]*>")

EMAIL_SCHEMA = {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
                "required": ["subject", "body"], "additionalProperties": False}

EMAIL_SYSTEM = """You write short first-touch emails from Rodrigo Gayoso, Principal of Titan Realty Investments, to a listing agent. Plain text only. No HTML, no links, no images, no bullet points, no subject-line hype.

Voice: write exactly as Rodrigo writes. His style guide follows, then real emails he sent. Match his greetings ("Hi [First name]," or "Good morning,"), his openers ("This is Rodrigo with Titan Realty Investments."), his plain short sentences, his spelled-out "I am" / "we are", and his closers ("Look forward to hearing from you!" / "Thank you, and I look forward to hearing from you.").

=== STYLE GUIDE ===
{style_guide}
=== END STYLE GUIDE ===

=== REAL EMAILS RODRIGO SENT (few-shot; do not copy verbatim) ===
{examples}
=== END EXAMPLES ===

Hard rules for this first touch:
- 60–120 words. One paragraph or two short ones. Sign off exactly with the signature block given in the input (do not change it).
- Reference each property by its street address and one concrete detail from its remarks (quote or paraphrase, no invention).
- Say we are a local cash buyer in the property's county.
- Ask whether the seller is open to an offer below list and what number would get it done.
- Offer to close in {close_days} days, as-is, with {inspection}.
- Do not state a dollar figure. Do not promise anything beyond those terms. Do not mention being an AI.
- If several properties are listed, cover all of them in one email.
- Vary the wording; do not reuse the example emails verbatim. No emojis, no "I hope this email finds you well".
Return JSON: subject (under 60 chars, plain, mentions the street, e.g. "Cash offer: 101 NW 11th St") and body."""

_STYLE_CACHE: dict[str, str] = {}


def style_guide_text(cfg: Any) -> str:
    """The writing style guide (docs/style/...) injected into every drafting prompt. Empty string when missing."""
    rel = cfg.get("outreach.style_guide")
    if not rel:
        return ""
    path = Path(rel)
    if not path.is_absolute():
        path = Path(getattr(cfg, "root", ".")) / path
    key = str(path)
    if key not in _STYLE_CACHE:
        try:
            _STYLE_CACHE[key] = path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("style guide not readable (%s): %s", path, e)
            _STYLE_CACHE[key] = ""
    return _STYLE_CACHE[key]


EMAIL_TEMPLATES = [
    ("Cash offer: {street}",
     "Hi {first},\n\nThis is {sender} with {company_short}. We are local cash buyers in {county} and I came across your listing at {street}. "
     "I noticed {detail}, and it looks like it could fit what we buy.\n\n"
     "Is the seller open to an offer below list if we close as-is in {close_days} days with {inspection}? If so, what number do you think gets it done?\n\n"
     "Look forward to hearing from you!\n\n{sign}"),
    ("Question on {street}",
     "Hi {first},\n\n{sender} here with {company_short}. We buy houses in {county} for cash and {street} caught my eye, mostly because {detail}.\n\n"
     "I am not looking to waste your time or lowball anyone. I just want to know if the seller would consider something under asking for a clean {close_days}-day close, as-is, "
     "and we cover our own closing costs. What range would they actually take?\n\n"
     "Thank you, and I look forward to hearing from you.\n\n{sign}"),
    ("{street} - cash buyer, {close_days}-day close",
     "Good morning {first},\n\nThis is {sender} with {company}. I saw {street} in {city} and that {detail}. We are investors buying property in {county} for cash, "
     "and we can close in about {close_days} days as-is, {inspection}, proof of funds available on request.\n\n"
     "Is the seller open to a number below list? If so, what would it take to get this done?\n\n"
     "Any questions, call or text me anytime.\n\n{sign}"),
    ("{street} - offer from Titan Realty",
     "{first},\n\n{sender} with {company_short} here. Your listing at {street} looks like it could fit what we are buying in {county}, especially since {detail}.\n\n"
     "If the seller is motivated I would rather make a real as-is cash offer with a {close_days}-day close than go back and forth. "
     "Are they open to something under list, and roughly where would that need to be?\n\n"
     "Let me know your thoughts.\n\n{sign}"),
]
MULTI_TEMPLATE = ("A few of your listings in {county}",
                  "Hi {first},\n\nThis is {sender} with {company_short}. We are local cash buyers in {county}, and a few of your listings look like they could fit us:\n{lines}\n\n"
                  "Would any of those sellers consider an offer below list for an as-is, {close_days}-day close with {inspection}? If so, which ones and roughly what number?\n\n"
                  "Thank you, and I look forward to hearing from you.\n\n{sign}")

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


def signature(cfg: Any, closing: str = "Best regards,") -> str:
    """Rodrigo's first-contact signature (style guide §1), without the website so the no-links rule holds."""
    op = cfg["operator"]
    title = op.get("sender_title")
    company = op.get("company_short", op["company"])
    lines = [closing, "", op["sender_name"], f"{title} | {company}" if title else company, pretty_phone(op["sender_phone"])]
    if op.get("mailing_address"):
        lines.append(op["mailing_address"])
    return "\n".join(lines)


def _ctx(cfg: Any, agent: dict[str, Any], props: list[dict[str, Any]]) -> dict[str, Any]:
    op = cfg["operator"]
    p = props[0]
    sign = signature(cfg)
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
        "agent_first_name": first_name(agent.get("name")), "sender": op["sender_name"], "company": op["company"], "phone": pretty_phone(op["sender_phone"]),
        "signature_block": signature(cfg),
        "properties": [{"street": p["street"], "city": p["city"], "county": p["county"], "list_price": p["list_price"],
                        "remarks_excerpt": (p.get("remarks") or "")[:800], "detail_hint": _detail(p), "days_on_market": p.get("days_on_market"),
                        "price_cuts": p.get("price_cuts")} for p in props],
    }, ensure_ascii=False)
    system = EMAIL_SYSTEM.format(style_guide=style_guide_text(cfg), examples=examples, close_days=cfg["formula"]["close_days"],
                                inspection=inspection_phrase(cfg["outreach"]["terms"]))
    out = llm.structured("draft_email", system, user, EMAIL_SCHEMA, max_tokens=700)
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
    """The written offer sent after a positive text reply (section 7a) or on ASK_FOR_OFFER by email. Style guide §13 'cash offer' bones."""
    f, terms = cfg["formula"], cfg["outreach"]["terms"]
    ctx = _ctx(cfg, agent, [prop])
    inspection = inspection_phrase(terms)
    inspection = inspection[0].upper() + inspection[1:] + "."
    closing = "We cover our own closing costs." if terms.get("buyer_pays_closing") else "Closing costs split the standard way."
    body = (
        f"Hi {ctx['first']},\n\n"
        f"As discussed, here is our cash offer for {prop['street']}, {prop['city']}.\n\n"
        f"The main terms:\n\n"
        f"- Offer price: ${offer_price:,.0f}, all cash. No financing or appraisal contingency.\n"
        f"- Property sold as-is. No repairs required on the seller's end. {inspection}\n"
        f"- Earnest money: ${f['emd']:,.0f}, held in escrow by the title company.\n"
        f"- Closing in {f['close_days']} days, or on a later date if that suits the seller better. {closing}\n"
        f"- Proof of funds {terms.get('proof_of_funds', 'available on request')}.\n\n"
        f"If the seller is agreeable I will send the contract over right away. Any questions, call or text me anytime.\n\n"
        f"{signature(cfg, 'Best,')}"
    )
    return {"subject": f"Cash offer: {prop['street']}, {prop['city']}", "body": body}
