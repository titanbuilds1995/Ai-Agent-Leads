"""The OM-request message to a NEEDS-OM broker, in Charles's voice, built from the existing outreach pieces (intro line, signature,
paragraph layout, the same banned-phrase / no-numbers / no-dash checks). Nothing here sends; the text goes on the Asana task and
into the broker contact sheet so Rodrigo can email it on Monday."""
from __future__ import annotations

import re
from typing import Any

from ..outreach.compose import BANNED_RE, BUYER_RE, DASH_RE, DIRECT_RE, INTRO_RE, PERSONAL_RE, URL_RE, format_paragraphs, signature

ASSET_WORDS = {
    "nnn": "net lease", "retail": "retail", "medical": "medical", "hotel": "hospitality", "industrial": "industrial",
    "office": "office", "multifamily": "multifamily",
}
DOC_WORDS = {"T-12": "the trailing twelve", "rent roll": "the rent roll", "OM": "the offering memorandum", "lease abstract": "the lease abstract"}


def first_name(name: str | None) -> str:
    n = (name or "").strip()
    n = re.sub(r",.*$", "", n)                   # "Alex Lucke, CCIM"
    parts = [p for p in n.split() if not re.fullmatch(r"(?i)(mr|mrs|ms|dr)\.?", p)]
    return parts[0] if parts else "there"


def om_request(cfg: Any, L: dict[str, Any]) -> dict[str, str]:
    """Subject + body. Short, direct, one ask, no numbers, in the first-email layout (greeting on its own line)."""
    street = (L.get("street") or L.get("name") or "your listing").strip()
    city = L.get("city") or ""
    where = f"{street} in {city}" if city else street
    missing = [d for d in (L.get("docs_missing") or ["OM", "T-12", "rent roll"]) if d in DOC_WORDS]
    if not missing:
        missing = ["OM", "T-12", "rent roll"]
    if len(missing) >= 3:
        asks = f"{DOC_WORDS[missing[0]]}, {DOC_WORDS[missing[1]]} and {DOC_WORDS[missing[2]]}"
    elif len(missing) == 2:
        asks = f"{DOC_WORDS[missing[0]]} and {DOC_WORDS[missing[1]]}"
    else:
        asks = DOC_WORDS[missing[0]]
    kind = ASSET_WORDS.get(L.get("asset_class") or "", "commercial")
    if L.get("asset_class") == "hotel":           # a hotel has no rent roll: the STR report is the equivalent
        asks = asks.replace("the rent roll", "the STR report")
    elif L.get("asset_class") == "multifamily":
        asks = asks.replace("the lease abstract", "the unit mix")
    if L.get("stated_cap"):
        # Rodrigo, 2026-11-09: we speak as the principal (a 1031 exchange with a partner of ours, never "a buyer we work with"); the listing shows a cap; we need the numbers to verify it is a real 8+ cap, and we give them whatever they need
        body = (
            f"Hey {first_name(L.get('broker_name'))},\n\n"
            f"Charles with Titan Realty Investments. I saw your listing at {where} on LoopNet.\n\n"
            f"We are placing a 1031 exchange with a partner of ours and the clock is running. This one is in our box. We need the numbers "
            f"behind the cap rate to verify it holds up as a real eight plus cap. Could you send over {asks}?\n\n"
            f"If you need anything from our side to release them, proof of funds or otherwise, say the word and it is yours.\n\n"
            + signature(cfg)
        )
    else:
        # the listing shows no cap: one question, whoever answers first moves up the list
        body = (
            f"Hey {first_name(L.get('broker_name'))},\n\n"
            f"Charles with Titan Realty Investments. I saw your listing at {where} on LoopNet.\n\n"
            f"We are placing a 1031 exchange with a partner of ours and the clock is running. This property is in our box as long as it is "
            f"a real eight plus cap. The listing does not show a cap rate, so I have to ask straight out: where is it?\n\n"
            f"If it clears that bar, could you send over {asks}? If you need anything from our side to release them, say the word.\n\n"
            + signature(cfg)
        )
    body = format_paragraphs(body)
    subject = f"{street}" + (f", {city}" if city else "") + " on LoopNet"
    return {"subject": subject[:90], "body": body}


def validate_om_request(subject: str, body: str) -> list[str]:
    """Same house rules as the first emails, minus the residential-only ones."""
    problems: list[str] = []
    head = body.strip().split("\n")
    if not (len(head) > 2 and head[0].rstrip().endswith(",") and not head[1].strip()):
        problems.append("greeting not on its own line")
    first = body.split("\n\n")[1] if "\n\n" in body else body
    if not INTRO_RE.search(first):
        problems.append("no intro")
    if BANNED_RE.search(body) or BUYER_RE.search(body) or PERSONAL_RE.search(body) or DIRECT_RE.search(body):
        problems.append("house-rule phrase")
    if re.search(r"\b(buyer|client|investor)\b", body, re.I):
        problems.append("names a buyer (we speak as the principal)")
    if URL_RE.search(body):
        problems.append("link")
    if DASH_RE.search(body.split("\n\nBest")[0]) or DASH_RE.search(subject):
        problems.append("dash")
    if re.search(r"\$\s?\d|\d+(\.\d+)?\s?%", body):
        problems.append("number")
    words = len(body.split("\n\nBest")[0].split())
    if words < 35 or words > 110:
        problems.append(f"length {words}")
    if "?" not in body:
        problems.append("no question")
    return problems
