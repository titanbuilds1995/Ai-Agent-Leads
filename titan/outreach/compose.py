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
# Protocol §1/§4/§27: listing-summarizer phrases, manufactured observations, and corporate outreach language.
BANNED_RE = re.compile(
    r"investment criteria|investment opportunity|investment potential|mutually beneficial|potentially interested|based on our analysis|"
    r"based on the information provided|caught our attention|cash purchasing criteria|\bportfolio\b|acquisition parameters|"
    r"property characteristics|according to the listing|we specialize in|hope this (email|message) finds you|fits? our criteria|"
    r"good fit for our|meet our (investment )?criteria|we work with properties|reaching out regarding|I am writing to|"
    r"(looked|looking) at the (photos|pictures|images)|from the (photos|pictures)|in the (photos|pictures)|"
    r"the (roof|kitchen|bathroom|interior|exterior) (looks|appears)|(photos|pictures) (show|make it look)|"
    r"I noticed (the property|your property|the home|this home) (needs|has|is)|the note in the remarks|in the remarks\b", re.I)
# Rodrigo, 2026-10-24: no dashes in emails (em dash, en dash, spaced hyphen, double hyphen). Hyphenated words ("move-in") are fine.
DASH_RE = re.compile(r"[—–]|\s-\s|--")
URL_RE = re.compile(r"https?://|www\.|\.com/|\.net/|\.org/", re.I)
HTML_RE = re.compile(r"<[a-zA-Z/][^>]*>")

EMAIL_SCHEMA = {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
                "required": ["subject", "body"], "additionalProperties": False}

_STR_LIST = {"type": "array", "items": {"type": "string"}}
ANALYSIS_SCHEMA = {"type": "object", "properties": {
    "facts": _STR_LIST, "positive_signals": _STR_LIST, "negative_signals": _STR_LIST, "unknown_big_ticket": _STR_LIST,
    "rehab_level": {"type": "integer"}, "hypothesis": {"type": "string"}, "key_unknown": {"type": "string"}, "angle": {"type": "string"}},
    "required": ["facts", "positive_signals", "negative_signals", "unknown_big_ticket", "rehab_level", "hypothesis", "key_unknown", "angle"],
    "additionalProperties": False}
# Think first, write second: the analysis fields come before subject/body so the model reasons before it writes.
FIRST_TOUCH_SCHEMA = {"type": "object", "properties": {"analysis": ANALYSIS_SCHEMA, "subject": {"type": "string"}, "body": {"type": "string"}},
                      "required": ["analysis", "subject", "body"], "additionalProperties": False}

EMAIL_SYSTEM = """You are {sender}, Junior Acquisitions Agent at {company}. You write the FIRST email to a listing agent about one of their listings. Plain text only: no HTML, links, images or bullet points.

Follow the protocol below exactly. It is the whole job.

=== OUTREACH PROTOCOL ===
{protocol}
=== END PROTOCOL ===

Working rules for this call:
- THINK FIRST: fill in the analysis (facts / positive signals / negative signals / unknown big-ticket items / rehab level 1-5 / one-sentence investment hypothesis / most important unknown / conversation angle) BEFORE writing the email. The email must follow from it.
- You have NOT seen any photographs (photos_analyzed is false). Never describe photos or what anything "looks like" visually.
- Use only what is in the input. Anything not stated is an unknown; say so conditionally ("if the roof isn't a project…"), never as a fact.
- Do not quote or paraphrase the listing's own wording back to the agent. Reason one level deeper instead.
- No price, offer, percentage, range, discount, "cash offer" or formula. Do not state any dollar figure. This is a conversation, not a negotiation.
- Only if the input says may_say_close_to_asking is true may you say the asking price doesn't seem far from where you'd need to be. Otherwise never imply you are close on price.
- One natural question an agent can answer easily (two at most). 50-110 words before the signature. Vary the opening naturally.
- Write as {sender} in first person ("I", "we buy…"). Mention {company_short} once, lightly. Address the agent by first name.
- End with the signature block from the input, unchanged. It already begins with "Best regards," so do NOT add another sign-off line ("Thanks," / "Appreciate it,") before it.
- No emojis, no "I hope this email finds you well", no pressure, never mention AI or automation.
- If property_type is "land": it is a lot, not a house. No renovation, roof or interior talk; ask about flexibility and anything about the parcel (zoning, access, utilities, why it's sitting).
- NO DASHES of any kind in the subject or body: no em dash (—), no en dash (–), no " - " between words, no "--". Use a comma or a period instead ("Hey John, I was looking at..."). This is a hard rule.
- Before answering, run the quality test from the protocol (could this go to 100 listings? did we repeat the listing? did we claim something we don't know? is there a hypothesis? one easy reason to respond? does it sound like a human investor?). If any fails, rewrite.
Return JSON: analysis, subject (under 60 chars, plain, names the street, e.g. "Quick question on NW 11th St"), body."""

_STYLE_CACHE: dict[str, str] = {}


def protocol_text(cfg: Any) -> str:
    """Rodrigo's outreach protocol (docs/style/OUTREACH_PROTOCOL.md), injected into every drafting prompt."""
    rel = cfg.get("outreach.protocol", "docs/style/OUTREACH_PROTOCOL.md")
    path = Path(rel)
    if not path.is_absolute():
        path = Path(getattr(cfg, "root", ".")) / path
    key = f"protocol:{path}"
    if key not in _STYLE_CACHE:
        try:
            _STYLE_CACHE[key] = path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("outreach protocol not readable (%s): %s", path, e)
            _STYLE_CACHE[key] = ""
    return _STYLE_CACHE[key]


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


# Fallback when Claude is unavailable. Each angle carries a small investment hypothesis + one easy question (protocol §6, §16, §19)
# and never repeats the listing's wording. {near_ask} is added only when our own numbers support it.
EMAIL_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "reduced": [   # price moved, sitting a while: pricing issue vs condition issue (§23)
        ("Quick question on {street_name}",
         "Hey {first}, I've been looking at the property on {street_name}. I saw they've already moved the price since it hit the market, "
         "and the house itself doesn't sound like a major rehab, so I'm wondering if this is becoming more of a pricing issue than a "
         "condition issue.{near_ask} Do you think the seller is getting closer to where they'd entertain an investor number?\n\n{sign}"),
        ("{street_name}",
         "{first}, I went through the listing on {street_name}. With the price having come down a bit, I'm curious whether the seller "
         "is starting to get realistic or whether there's something about the property that's been holding it back.{near_ask} "
         "What's your read on where they really need to be?\n\n{sign}"),
    ],
    "sitting": [   # long DOM, no cuts: why is it sitting? (§6)
        ("The house on {street_name}",
         "Hey {first}, I've been watching the house on {street_name} because it's been sitting for a while. We buy quite a bit in that "
         "area and it could work for us, but I'm curious whether that's mostly price or if there's something about the property I'm "
         "missing.{near_ask} What's been holding this one back?\n\n{sign}"),
        ("Quick question on {street_name}",
         "Hey {first}, quick question on {street_name}. It's been on the market a bit, and before I spend your time on numbers I'd like "
         "to understand the story. Is it a condition thing, a price thing, or just the market? If it's the first, how deep of a "
         "project would you say it really is?{near_ask}\n\n{sign}"),
    ],
    "heavy": [     # old house / investor-special signals: how deep does it go? (§24)
        ("Quick question on {street_name}",
         "Hey {first}, quick question on the property on {street_name}. This one looks like it could work for us, but I'm trying to "
         "understand how deep the project really goes before throwing a meaningless number at you. Are we mostly talking interior "
         "rehab, or are roof, plumbing and electrical coming into play too?\n\n{sign}"),
        ("{street_name}: how deep is the project?",
         "{first}, I'm trying to get my head around the condition on {street_name}. The age doesn't bother us by itself; I mainly "
         "need to figure out whether we're dealing with cosmetic updating or whether the big-ticket systems are starting to stack up. "
         "What would you say it really needs?\n\n{sign}"),
    ],
    "cosmetic": [  # dated-not-distressed hypothesis (§2, §22)
        ("The house on {street_name}",
         "Hey {first}, I was looking at the house on {street_name}. From the description it sounds more dated than truly distressed. "
         "If the roof, AC and the major stuff are still in decent shape, this might actually be pretty close to something we'd look "
         "at.{near_ask} How much work would you say it really needs?\n\n{sign}"),
        ("Quick question on {street_name}",
         "Hey {first}, I may have a buyer angle on your {street_name} listing. If what it needs is mostly kitchen, baths and cosmetic "
         "updating rather than a bunch of mechanical work, there may be a number where it works for us.{near_ask} How deep of a "
         "project would you say it is?\n\n{sign}"),
    ],
    "newer": [     # newer build: probably not a huge project (§25)
        ("{street_name}",
         "Hey {first}, the house on {street_name} actually looks pretty close to something we'd buy. With it being a newer build it "
         "doesn't sound like we'd be taking on a huge project, so the bigger question is the seller.{near_ask} Do you know how much "
         "room they realistically have from the asking price?\n\n{sign}"),
        ("Quick question on {street_name}",
         "Hey {first}, curious about something on the {street_name} property. Newer house, so I'm not expecting a big rehab, which "
         "makes it more about whether the numbers can line up.{near_ask} Is the seller open to some flexibility, or are they set on "
         "staying close to asking?\n\n{sign}"),
    ],
    "land": [      # vacant land: no renovation talk; the questions are use, utilities and seller flexibility
        ("Quick question on the lot on {street_name}",
         "Hey {first}, quick question on the lot on {street_name}. We've been picking up land in that area, and this one could work "
         "depending on what's possible with it. Do you know if the seller has some flexibility on price, and whether there's "
         "anything with zoning, access or utilities I should know about before I dig into the numbers?\n\n{sign}"),
        ("The lot on {street_name}",
         "{first}, I was looking at the lot on {street_name}. Before I spend your time on numbers, is the seller open to some "
         "flexibility, or are they firm near asking? And is there anything about the parcel that's been holding it back?\n\n{sign}"),
    ],
    "default": [   # no meaningful observation: don't force one (§19)
        ("Quick question on {street_name}",
         "Hey {first}, quick question on the house on {street_name}. I'm trying to figure out whether it's mostly an updating job or "
         "whether there's anything major going on mechanically. We buy quite a bit in that area and it could potentially work "
         "depending on how deep the project is.{near_ask} What's your read on the condition?\n\n{sign}"),
        ("The house on {street_name}",
         "{first}, I went through the listing on {street_name}. Honestly this one may be closer to what we buy than I initially "
         "thought, as long as we're not dealing with a roof, AC or plumbing project on top of everything else.{near_ask} How much "
         "work would you say it really needs?\n\n{sign}"),
    ],
}
MULTI_TEMPLATE = ("A few of your listings",
                  "Hey {first}, I went through a few of your listings and a couple could work for us depending on how deep the projects are:\n{lines}\n\n"
                  "Before I spend your time on numbers: on any of those, is it mostly cosmetic, or is there something bigger going on? "
                  "And do any of those sellers have some room from asking?\n\n{sign}")

NEAR_ASK_LINE = " I did look at the asking price and it doesn't seem too far from where we'd need to be."

HEAVY_RE = re.compile(r"investor special|handyman|cash only|cash buyers? only|fixer|gut|major repairs|needs (a )?(new )?roof|"
                      r"structural|fire damage|water damage|mold|tear ?down|as[- ]is where[- ]is|sold as is for land", re.I)
COSMETIC_RE = re.compile(r"\btlc\b|dated|original (kitchen|bath|condition)|bring your (vision|ideas)|needs (some )?(updating|updates|cosmetic)|"
                         r"great bones|good bones|cosmetic|update|refresh|handyman'?s? dream", re.I)
SOLID_RE = re.compile(r"new(er)? roof|roof (20[12]\d|replaced)|new(er)? (ac|a/c|hvac)|impact windows|updated (electrical|plumbing)|"
                      r"re-?piped|new(er)? (plumbing|electrical|panel)", re.I)


def angle_for(p: dict[str, Any]) -> str:
    """Which investment hypothesis the fallback leads with (facts and signals only, never a quote of the listing)."""
    remarks = p.get("remarks") or ""
    if p.get("property_type") == "land":
        return "land"
    dom = p.get("days_on_market") or 0
    cuts = p.get("price_cuts") or 0
    yb = p.get("year_built") or 0
    if cuts and dom >= 45:
        return "reduced"
    if HEAVY_RE.search(remarks) or (yb and yb < 1970 and not SOLID_RE.search(remarks)):
        return "heavy"
    if COSMETIC_RE.search(remarks) or SOLID_RE.search(remarks):
        return "cosmetic"
    if yb >= 2000:
        return "newer"
    if dom >= 60:
        return "sitting"
    return "default"


SMS_TEMPLATES = [
    "Hi {first}, {sender} w/ {company_short}. Quick q on {street_name}: mostly cosmetic, or something bigger going on? Any flexibility on price?",
    "{first}, {sender} with {company_short} here. Looking at {street_name} for a renovation. Is the seller open to some flexibility on price?",
    "Hi {first}, this is {sender} from {company_short}. On {street_name}: how deep is the project really, and does the seller have some flexibility on price?",
    "{first}, {sender} ({company_short}). Interested in {street_name}. Do you have a sense of the seller's flexibility on price?",
]


def seed_for(agent_id: int, property_ids: list[int], run_date: str) -> int:
    h = hashlib.sha1(f"{agent_id}:{sorted(property_ids)}:{run_date}".encode()).hexdigest()
    return int(h[:8], 16)


def signature(cfg: Any, closing: str = "Best regards,") -> str:
    """The sender's signature (Charles), without a website so the no-links rule holds, plus the footer."""
    op = cfg["operator"]
    if op.get("signature_lines"):
        return "\n".join([closing, "", *op["signature_lines"]])
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
    """Rare, not automatic (protocol): only when the ask is within near_ask_within_pct of our INTERNAL TARGET (the median-ratio
    screening number, p["mao"]), on real comps. Keyed to the target, not the top of the range, so it stays rare."""
    if p.get("arv_method") not in ("comps", "comps_wide"):
        return False
    lp, target = p.get("list_price"), p.get("mao")
    return bool(lp and target and target > 0 and lp <= target * (1 + cfg.get("outreach.near_ask_within_pct", 5) / 100))


def _ctx(cfg: Any, agent: dict[str, Any], props: list[dict[str, Any]]) -> dict[str, Any]:
    op = cfg["operator"]
    p = props[0]
    sign = signature(cfg)
    return {"first": first_name(agent.get("name")), "sender": op["sender_name"].split()[0], "company": op["company"],
            "street_name": street_name(p.get("street")), "near_ask": NEAR_ASK_LINE if len(props) == 1 and may_say_close_to_asking(p, cfg) else "",
            "company_short": op.get("company_short", op["company"]), "phone": pretty_phone(op["sender_phone"]), "county": p.get("county") or "South Florida",
            "city": p.get("city") or "", "street": p.get("street") or "", "close_days": cfg["formula"]["close_days"], "inspection": inspection_phrase(cfg["outreach"]["terms"]),
            "sign": sign, "lines": "\n".join(f"{q['street']}, {q['city']}" for q in props)}


def template_email(cfg: Any, agent: dict[str, Any], props: list[dict[str, Any]], seed: int) -> tuple[str, str, str]:
    ctx = _ctx(cfg, agent, props)
    rnd = random.Random(seed)
    if len(props) > 1:
        subj, body = MULTI_TEMPLATE
        variant = "multi"
    else:
        angle = angle_for(props[0])
        options = EMAIL_TEMPLATES[angle]
        i = rnd.randrange(len(options))
        subj, body = options[i]
        variant = f"t:{angle}:{i}"
    return subj.format(**ctx), body.format(**ctx), variant


def _history_summary(p: dict[str, Any]) -> list[str]:
    hist = p.get("price_history") or []
    if isinstance(hist, str):
        try:
            hist = json.loads(hist)
        except json.JSONDecodeError:
            hist = []
    out = []
    for h in hist[-6:]:
        if isinstance(h, dict) and (h.get("price") or h.get("event")):
            out.append(f"{h.get('date') or '?'}: {h.get('event') or ''} {('$' + format(int(h['price']), ',')) if h.get('price') else ''}".strip())
    return out


def llm_email(llm: LLM | None, cfg: Any, agent: dict[str, Any], props: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]] | None:
    """Protocol-driven first touch: the model fills the analysis first, then writes. Returns (subject, body, analysis)."""
    if llm is None or not llm.available:
        return None
    op = cfg["operator"]
    user = json.dumps({
        "agent_first_name": first_name(agent.get("name")), "agent_brokerage": agent.get("brokerage"),
        "sender": op["sender_name"].split()[0], "company": op["company"], "company_short": op.get("company_short", op["company"]),
        "signature_block": signature(cfg), "photos_analyzed": False,
        "properties": [{"street": p["street"], "city": p["city"], "zip": p.get("zip"), "asking_price": p["list_price"],
                        "original_list_price": p.get("original_list_price"), "price_cuts": p.get("price_cuts"),
                        "price_history": _history_summary(p), "days_on_market": p.get("days_on_market"), "back_on_market": bool(p.get("back_on_market")),
                        "beds": p.get("beds"), "baths": p.get("baths"), "sqft": p.get("sqft"), "lot_sqft": p.get("lot_sqft"),
                        "year_built": p.get("year_built"), "property_type": p.get("property_type"), "vacant": bool(p.get("vacant")),
                        "hoa_monthly": p.get("hoa"), "listing_remarks_verbatim": (p.get("remarks") or "")[:3000],
                        "may_say_close_to_asking": len(props) == 1 and may_say_close_to_asking(p, cfg)} for p in props],
    }, ensure_ascii=False)
    system = EMAIL_SYSTEM.format(protocol=protocol_text(cfg), sender=op["sender_name"].split()[0],
                                company=op["company"], company_short=op.get("company_short", op["company"]))
    out = llm.structured("draft_email", system, user, FIRST_TOUCH_SCHEMA, max_tokens=1800)
    if not out or not isinstance(out, dict):
        return None
    analysis = out.get("analysis") if isinstance(out.get("analysis"), dict) else {}
    return str(out.get("subject", "")).strip(), str(out.get("body", "")).strip(), analysis


def validate_email(subject: str, body: str, props: list[dict[str, Any]]) -> list[str]:
    problems = []
    if HTML_RE.search(body) or HTML_RE.search(subject):
        problems.append("html")
    if URL_RE.search(body):
        problems.append("link")
    if not any(street_name(p.get("street")) and street_name(p.get("street")) in body for p in props):
        problems.append("no address")
    words = len(body.split("\n\nBest")[0].split()) if "\n\nBest" in body else len(body.split())
    if words < 30 or words > 150:          # protocol §18: ~50-110 words before the signature
        problems.append(f"length {words}")
    if BANNED_RE.search(body) or BANNED_RE.search(subject):
        problems.append("protocol language (listing echo / fake observation / corporate phrasing)")
    if re.search(r"\n(thanks|thank you|appreciate it|cheers|talk soon)[,.!]?\s*\n\s*\nbest regards,", body, re.I):
        problems.append("double sign-off")
    if DASH_RE.search(body.split("\n\nBest")[0]) or DASH_RE.search(subject):
        problems.append("dash (Rodrigo: no em/en dashes or ' - ' in emails)")
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
    """Claude (protocol, think-first) with up to two attempts; the angle template is the fallback. Always validated."""
    for attempt in range(2):
        res = llm_email(llm, cfg, agent, props)
        if not res:
            break
        subject, body, analysis = res
        problems = validate_email(subject, body, props)
        if not problems:
            return {"subject": subject, "body": body, "variant": "claude", "analysis": analysis}
        log.warning("claude first touch rejected (%s), attempt %d", problems, attempt + 1)
    subject, body, variant = template_email(cfg, agent, props, seed)
    problems = validate_email(subject, body, props)
    if problems:
        raise ValueError(f"template email failed validation: {problems}")
    return {"subject": subject, "body": body, "variant": variant,
            "analysis": {"angle": angle_for(props[0]), "hypothesis": "(template: no Claude analysis for this one)"} if len(props) == 1 else {}}


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
