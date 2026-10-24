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

# First touch never leads with a price: no %, no "k"/"thousand" amounts, no offer/discount language.
NO_NUMBERS_RE = re.compile(r"\d+(\.\d+)?\s?%|\b\d{2,3}\s?k\b|\b(thousand|percent|below (list|asking)|under (list|asking)|cash offer|"
                           r"our offer|make an offer|an offer of|we can offer|we('d| would) offer|discount|lowball|ballpark)\b", re.I)
URL_RE = re.compile(r"https?://|www\.|\.com/|\.net/|\.org/", re.I)
HTML_RE = re.compile(r"<[a-zA-Z/][^>]*>")

EMAIL_SCHEMA = {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
                "required": ["subject", "body"], "additionalProperties": False}

EMAIL_SYSTEM = """You write the FIRST message from {sender}, who works with {company}, to a listing agent. Plain text only. No HTML, no links, no images, no bullet points.

Strategy (Rodrigo's rule): conversation first. This email opens a conversation; it does not make an offer. Its only job is to learn whether the agent is responsive and whether the seller has any flexibility.

Tone: a thoughtful local investor writing to an agent, short and natural. Borrow the plain, warm tone of the style guide and examples below, but write as {sender} (first person), never as Rodrigo.

=== STYLE GUIDE (tone reference) ===
{style_guide}
=== END STYLE GUIDE ===

=== EXAMPLE EMAILS (tone only; written by Rodrigo, do not copy names, numbers or wording) ===
{examples}
=== END EXAMPLES ===

Shape (vary the wording every time; never send identical text to every agent):
"Hey [first name], {sender} here with {company_short}. Hope you're doing well. I came across your listing on [street], and the [one verified detail] looks like it could fit what we're looking for in our next renovation project. Before we get deeper into the numbers, do you have a sense of whether the seller would consider some flexibility on price, or are they needing to stay close to the current asking price?"

Hard rules:
- 50–110 words. Sign off exactly with the signature block given in the input (do not change it).
- Mention one or two VERIFIED details from the input (remarks excerpt, beds/baths/sqft/lot, year built, days on market, price history) that explain our interest. Do not repeat the whole listing. Do not invent familiarity with the property or neighborhood.
- Keep facts separate from assumptions: a price cut or long time on market does not prove the seller is desperate; never say or imply that.
- No price, no offer, no percentage, no discount, no numeric range, no "cash offer" language. Do not state any dollar figure.
- Only if the input says may_say_close_to_asking is true, you may add: "I did look at the asking price, and it doesn't seem too far from where we'd need to be." Otherwise never suggest we are close on price.
- Ask one question (two at most) about the seller's flexibility on price.
- If several properties are listed, cover all of them in one short email with one question.
- No emojis, no "I hope this email finds you well", no pressure, do not mention being an AI.
Return JSON: subject (under 60 chars, plain, mentions the street, e.g. "Your listing on NW 11th St") and body."""

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
    ("Your listing on {street_name}",
     "Hey {first},\n\n{sender} here with {company_short}. Hope you're doing well. I came across your listing on {street_name}, and {detail} "
     "looks like it could fit what we're looking for in our next renovation project.{near_ask}\n\n"
     "Before we get deeper into the numbers, do you have a sense of whether the seller would consider some flexibility on price, "
     "or are they needing to stay close to the current asking price?\n\n{sign}"),
    ("Question about {street}",
     "Hi {first},\n\nThis is {sender} with {company_short}. I was looking at {street} in {city}, and {detail} caught my attention for "
     "a renovation project we're lining up.{near_ask}\n\n"
     "Do you know if the seller has any room on price, or are they set on staying near asking?\n\n{sign}"),
    ("{street_name} listing",
     "Hey {first},\n\n{sender} with {company_short} here. Hope your week is going well. Your listing at {street} stood out to me, mostly because "
     "{detail}, which is the kind of house we like to renovate.{near_ask}\n\n"
     "Before I spend your time on numbers, is the seller open to some flexibility on price, or do they need to stay close to the list price?\n\n{sign}"),
    ("Quick question on {street_name}",
     "Hi {first},\n\n{sender} here with {company_short}. I came across {street} and {detail} looks like a good fit for what we're "
     "looking for next.{near_ask}\n\n"
     "Do you have a feel for whether the seller would consider some flexibility, or are they firm near the asking price?\n\n{sign}"),
]
MULTI_TEMPLATE = ("A few of your listings",
                  "Hi {first},\n\n{sender} here with {company_short}. Hope you're doing well. A few of your listings look like they could fit our next renovation projects:\n{lines}\n\n"
                  "Before we get into numbers, do you have a sense of whether any of those sellers would consider some flexibility on price?\n\n{sign}")

NEAR_ASK_LINE = " I did look at the asking price, and it doesn't seem too far from where we'd need to be."

SMS_TEMPLATES = [
    "Hi {first}, {sender} w/ {company_short}. Saw your listing on {street_name}, could fit our next renovation. Any flexibility on price, or staying near asking?",
    "{first}, {sender} with {company_short} here. {street_name} caught my eye for a renovation project. Is the seller open to some flexibility on price?",
    "Hi {first}, this is {sender} from {company_short}. Quick q on {street_name}: would the seller consider some flexibility on price, or stay close to asking?",
    "{first}, {sender} ({company_short}). Interested in {street_name} for a renovation. Do you have a sense of the seller's flexibility on price?",
]


def _detail(p: dict[str, Any]) -> str:
    """One concrete thing from the listing to prove we read it."""
    cited = p.get("cited")
    if isinstance(cited, str):
        try:
            cited = json.loads(cited)
        except json.JSONDecodeError:
            cited = []
    # Only details that explain why the HOUSE fits a renovation. Never quote motivation/distress phrases (estate, cash only,
    # motivated, price cut, divorce…) back at the agent: that reads as "we know the seller is desperate" (facts vs assumptions).
    prio = ["needs_work", "investor_special", "damage", "as_is"]
    cands = [c for c in (cited or []) if c.get("phrase") and "=" not in c.get("phrase", "") and c.get("signal") in prio]
    cands.sort(key=lambda c: prio.index(c["signal"]) if c.get("signal") in prio else 99)
    if cands:
        ph = re.sub(r"^\W+|\W+$", "", cands[0]["phrase"].strip())
        if ph.isupper():
            ph = ph.lower()
        return f'the "{ph}" note in the remarks'
    bits = []
    if p.get("beds") and p.get("baths"):
        bits.append(f"{p['beds']:g}/{p['baths']:g}")
    if p.get("lot_sqft") and p["lot_sqft"] >= 7000:
        return f"the {p['lot_sqft']:,} sq ft lot"
    if p.get("year_built") and p["year_built"] < 1980:
        return f"the {' '.join(bits + ['built in ' + str(p['year_built'])])}"
    if bits:
        return f"the {bits[0]} layout"
    return "the property"


def seed_for(agent_id: int, property_ids: list[int], run_date: str) -> int:
    h = hashlib.sha1(f"{agent_id}:{sorted(property_ids)}:{run_date}".encode()).hexdigest()
    return int(h[:8], 16)


def signature(cfg: Any, closing: str = "Best regards,") -> str:
    """The sender's signature (Charles), without a website so the no-links rule holds, plus the footer."""
    op = cfg["operator"]
    title = op.get("sender_title")
    company = op.get("company_short", op["company"])
    lines = [closing, "", op["sender_name"], f"{title} | {company}" if title else company, pretty_phone(op["sender_phone"])]
    if op.get("sender_email"):
        lines.append(op["sender_email"])
    if op.get("email_footer"):
        lines += ["", "--", op["email_footer"]]
    elif op.get("mailing_address"):
        lines.append(op["mailing_address"])
    return "\n".join(lines)


def street_name(street: str | None) -> str:
    """'107 NW 11th St' → 'NW 11th St' (how people talk about a listing)."""
    s = (street or "").strip()
    return re.sub(r"^\d+[A-Za-z]?\s+", "", s) or s


def may_say_close_to_asking(p: dict[str, Any], cfg: Any) -> bool:
    """Only when our own preliminary analysis supports it: list ≤ internal high × (1 + near_ask_within_pct), on real comps."""
    from ..valuation import preliminary_range
    if p.get("arv_method") not in ("comps", "comps_wide"):
        return False
    r = preliminary_range(p, cfg)
    lp = p.get("list_price")
    return bool(r and lp and r["high"] and lp <= r["high"] * (1 + cfg.get("outreach.near_ask_within_pct", 5) / 100))


def _ctx(cfg: Any, agent: dict[str, Any], props: list[dict[str, Any]]) -> dict[str, Any]:
    op = cfg["operator"]
    p = props[0]
    sign = signature(cfg)
    return {"first": first_name(agent.get("name")), "sender": op["sender_name"].split()[0], "company": op["company"],
            "street_name": street_name(p.get("street")), "near_ask": NEAR_ASK_LINE if len(props) == 1 and may_say_close_to_asking(p, cfg) else "",
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
        "properties": [{"street": p["street"], "city": p["city"], "list_price": p["list_price"],
                        "remarks_excerpt": (p.get("remarks") or "")[:800], "detail_hint": _detail(p), "days_on_market": p.get("days_on_market"),
                        "price_cuts": p.get("price_cuts"), "beds": p.get("beds"), "baths": p.get("baths"), "sqft": p.get("sqft"),
                        "lot_sqft": p.get("lot_sqft"), "year_built": p.get("year_built"),
                        "may_say_close_to_asking": len(props) == 1 and may_say_close_to_asking(p, cfg)} for p in props],
    }, ensure_ascii=False)
    system = EMAIL_SYSTEM.format(style_guide=style_guide_text(cfg), examples=examples, sender=op["sender_name"].split()[0],
                                company=op["company"], company_short=op.get("company_short", op["company"]))
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
    if not any(street_name(p.get("street")) and street_name(p.get("street")) in body for p in props):
        problems.append("no address")
    words = len(body.split())
    if words < 30 or words > 220:
        problems.append(f"length {words}")
    if "?" not in body:
        problems.append("no question")
    if re.search(r"\$\s?\d", body):
        problems.append("dollar figure")
    if NO_NUMBERS_RE.search(body.split("\n--\n")[0]) or re.search(r"\boffer\b", subject, re.I):
        problems.append("price or offer language")
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
    """The written offer. Only ever built for a price Rodrigo approved for this house (`titan.cli approve-offer`)."""
    f, terms = cfg["formula"], cfg["outreach"]["terms"]
    ctx = _ctx(cfg, agent, [prop])
    inspection = inspection_phrase(terms)
    inspection = inspection[0].upper() + inspection[1:] + "."
    closing = "We cover our own closing costs." if terms.get("buyer_pays_closing") else "Closing costs split the standard way."
    body = (
        f"Hi {ctx['first']},\n\n"
        f"As discussed, here is our offer for {prop['street']}, {prop['city']}.\n\n"
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
