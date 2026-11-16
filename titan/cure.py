"""Legal square footage and the cost to get there (Rodrigo, 2026-11-16, Celia Ruiz / 5341 NW 180th Ter: "we know that 500 sq ft have to
be demolished, so we can not count that as part of the living sq ft when we sell, we can only count what can be legalized. So we need
the ARV of what can be legalized and minus all of the cost to get us there").

One record per house (`property_cure`): the square footage a buyer can legally sell (legal_sqft), what must come down (demo_sqft),
what stays but needs after-the-fact permits (legalize_sqft), the cost of each (given, or figured from config per-sf rates), the recorded
liens / fines and who pays them, and a repairs override ("no rehab, the inside is done"). The valuation then:
  * comps and the $/sf exit are run on the LEGAL square footage (the layout "unpermitted" discount is dropped: the illegal sf is out);
  * MAO = ARV on legal sf × investor ratio − repairs − demolition − legalization − the liens WE would carry − our fee.
Where it comes from: Rodrigo's comment on the card ("legal sf 1564, demolish 500 sf, legalize 532 sf, no rehab, seller pays the liens";
`parse_comment`, his values always win) or the 🧠 read of the files on the lead (`source` files, filled only where he said nothing).
Shown on the card's THE NUMBERS, in the NUMBERS snapshot and in every prompt. Never changes the price we quote: Rodrigo does."""
from __future__ import annotations

import json
import re
from typing import Any

from . import db
from .util import iso, log

FIELDS = ("legal_sqft", "demo_sqft", "legalize_sqft", "demo_cost", "permit_cost", "liens_total", "liens_paid_by", "repairs", "note")
DEFAULTS = {"demo_per_sf": 15, "min_demo": 5000, "legalize_per_sf": 25, "min_legalize": 10000}

_NUM = r"\$?\s*([\d][\d,]*(?:\.\d+)?)\s*(k|K)?"
_MONEY = _NUM + r"(?!\d)(?!\s*(?:sq\b|sf\b|square|%))"      # a dollar figure: whole number, not square feet, not a percent
LEGAL_RE = re.compile(r"\b(?:legal|legalizable|permitted|sellable|count(?:able)?)\s+(?:living\s+)?(?:sq\.?\s*ft\.?|sf|square\s+f(?:ee|oo)t)\s*(?:is|of|=|:)?\s*" + _NUM
                      + r"|\b" + _NUM + r"\s*(?:sq\.?\s*ft\.?|sf|square\s+f(?:ee|oo)t)\s+(?:is|are)?\s*(?:legal|legalizable|permitted|sellable|what we can count)", re.I)
DEMO_RE = re.compile(r"\b(?:demolish|demo|knock(?:ed)?\s+down|tear(?:ing)?\s+down|take\s+down|remove)\s+(?:the\s+|about\s+|roughly\s+|around\s+)?" + _NUM
                     + r"\s*(?:sq\.?\s*ft\.?|sf|square\s+f(?:ee|oo)t)|\b" + _NUM
                     + r"\s*(?:sq\.?\s*ft\.?|sf|square\s+f(?:ee|oo)t)\s+(?:that\s+)?(?:has|have|needs?|need|will have|must|to)\s+(?:to\s+)?(?:be\s+)?(?:demolished|demo(?:'?d|ed)?|knocked\s+down|torn\s+down|removed|come\s+down)", re.I)
LEGALIZE_RE = re.compile(r"\b(?:legalize|permit|bring\s+(?:up\s+)?to\s+code)\s+(?:the\s+|about\s+)?" + _NUM + r"\s*(?:sq\.?\s*ft\.?|sf|square\s+f(?:ee|oo)t)"
                         r"|\b" + _NUM + r"\s*(?:sq\.?\s*ft\.?|sf|square\s+f(?:ee|oo)t)\s+(?:that\s+)?(?:can|could|has to|needs? to|must|to)\s+be\s+(?:legalized|permitted)", re.I)
DEMO_COST_RE = re.compile(r"\b(?:demo(?:lition)?|tear[- ]?down|knock[- ]?down)\s+(?:cost|budget|is|at|of|=|:|about|around|~)?\s*" + _MONEY, re.I)
PERMIT_COST_RE = re.compile(r"\b(?:permits?|legaliz\w+|engineer\w*|architect\w*|cure)\s+(?:cost|budget|is|at|of|=|:|about|around|~|will (?:run|cost|be))?\s*" + _MONEY, re.I)
LIENS_RE = re.compile(r"\b(?:liens?|fines?|violations?|code (?:liens?|fines?))\s+(?:total|are|is|at|of|=|:|about|around|~|come to|add up to)?\s*" + _MONEY, re.I)
REPAIRS_RE = re.compile(r"\b(?:rehab|repairs?|renovation)\s+(?:cost|budget|is|at|of|=|:|about|around|~)?\s*" + _MONEY, re.I)
NO_REHAB_RE = re.compile(r"\b(?:no|zero|without|doesn'?t need|does not need|not need(?:ing)?|needs? no|not needed|skip the)\s+(?:a\s+|any\s+)?(?:rehab|repairs?|renovation|work inside)"
                         r"|\b(?:inside|interior|house)\s+(?:is|looks)\s+(?:beautiful|done|finished|renovated|remodeled|in great shape|perfect|move[- ]in ready)"
                         r"|\bmove[- ]in ready\b|\bturn[- ]?key\b", re.I)
SELLER_PAYS_RE = re.compile(r"\b(?:seller|they|she|he|owner)s?\s+(?:will\s+|to\s+|must\s+|has to\s+|have to\s+|should\s+|is going to\s+|are going to\s+)?"
                            r"(?:pay|clear|take care of|cover|satisfy|handle|assume|absorb)\w*\s+(?:for\s+|off\s+)?(?:any\s+|all\s+|the\s+|their\s+|every\s+)?"
                            r"(?:\w+\s+){0,3}?(?:liens?|fines?|monetary|judgment|penalt|violations?)"
                            r"|\b(?:assume|assuming|assumes?)\s+(?:that\s+)?(?:they|the seller|she|he)\s+(?:will\s+)?(?:take care of|pay|clear|cover)", re.I)
BUYER_PAYS_RE = re.compile(r"\b(?:we|buyer|us|our side|investor)\s+(?:will\s+|to\s+|would\s+)?(?:assume|take|take over|pay|carry|eat|absorb|inherit)\w*\s+(?:\w+\s+){0,3}?(?:liens?|fines?|violations?|judgment)"
                           r"|\bas[- ]is with (?:the |all )?(?:liens?|fines?|violations?)|\bliens? (?:stay|stays|on us|come with it|on the buyer)", re.I)
CURE_RE = re.compile("|".join(r.pattern for r in (LEGAL_RE, DEMO_RE, LEGALIZE_RE, NO_REHAB_RE, SELLER_PAYS_RE, BUYER_PAYS_RE, LIENS_RE)), re.I)



def _first(rx: re.Pattern, text: str, at_least: float = 1) -> int | None:
    """The first figure the pattern finds that is at least `at_least` (so "legalize 532 sf" never reads as $53 of permits)."""
    for m in rx.finditer(text):
        for i in range(1, m.re.groups + 1, 2):
            if m.group(i):
                v = float(m.group(i).replace(",", ""))
                if i + 1 <= m.re.groups and m.group(i + 1):
                    v *= 1000
                if v >= at_least:
                    return int(round(v))
    return None


def parse_comment(text: str) -> dict[str, Any] | None:
    """His words → the fields he gave (only those). None when the comment says nothing about legal sf, demolition, permits,
    rehab or who pays the fines. Money with k ("demo 15k") is thousands; a plain sf figure is square feet."""
    t = text or ""
    out: dict[str, Any] = {}
    for key, rx in (("legal_sqft", LEGAL_RE), ("demo_sqft", DEMO_RE), ("legalize_sqft", LEGALIZE_RE)):
        v = _first(rx, t)
        if v:
            out[key] = v
    for key, rx in (("demo_cost", DEMO_COST_RE), ("permit_cost", PERMIT_COST_RE), ("liens_total", LIENS_RE), ("repairs", REPAIRS_RE)):
        v = _first(rx, t, at_least=100)         # "permits 30k" / "liens 59,780"; a bare "permit 2" is not money
        if v is not None:
            out[key] = v
    if NO_REHAB_RE.search(t):
        out["repairs"] = 0
    if SELLER_PAYS_RE.search(t):
        out["liens_paid_by"] = "seller"
    elif BUYER_PAYS_RE.search(t):
        out["liens_paid_by"] = "buyer"
    return out or None


def get(conn, property_id: int) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM property_cure WHERE property_id=?", (property_id,)).fetchone()
    return db.row_to_dict(r) if r else None


def set_cure(conn, property_id: int, fields: dict[str, Any], source: str = "rodrigo") -> dict[str, Any]:
    """Merge new fields into the house's record. Rodrigo's values win: a `files` read never overwrites a field he set."""
    cur = get(conn, property_id) or {}
    owners = json.loads(cur.get("sources") or "{}")
    merged = {k: cur.get(k) for k in FIELDS}
    for k, v in (fields or {}).items():
        if k not in FIELDS or v is None:
            continue
        if source != "rodrigo" and owners.get(k) == "rodrigo":
            continue
        merged[k] = v
        owners[k] = source
    conn.execute("INSERT INTO property_cure(property_id, legal_sqft, demo_sqft, legalize_sqft, demo_cost, permit_cost, liens_total, liens_paid_by, repairs, "
                 "note, sources, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(property_id) DO UPDATE SET legal_sqft=excluded.legal_sqft, "
                 "demo_sqft=excluded.demo_sqft, legalize_sqft=excluded.legalize_sqft, demo_cost=excluded.demo_cost, permit_cost=excluded.permit_cost, "
                 "liens_total=excluded.liens_total, liens_paid_by=excluded.liens_paid_by, repairs=excluded.repairs, note=excluded.note, "
                 "sources=excluded.sources, updated_at=excluded.updated_at",
                 (property_id, merged["legal_sqft"], merged["demo_sqft"], merged["legalize_sqft"], merged["demo_cost"], merged["permit_cost"],
                  merged["liens_total"], merged["liens_paid_by"], merged["repairs"], merged["note"], json.dumps(owners), iso()))
    return get(conn, property_id) or {}


def costs(cure: dict[str, Any] | None, cfg: Any) -> dict[str, Any]:
    """What getting to a legal, sellable house costs us: demolition, after-the-fact permits, the liens we would carry."""
    if not cure:
        return {"demo": 0, "permits": 0, "liens_on_us": 0, "total": 0, "lines": []}
    c = {**DEFAULTS, **((cfg.get("cure", {}) if hasattr(cfg, "get") else {}) or {})}
    demo = int(cure["demo_cost"]) if cure.get("demo_cost") is not None else (max(c["min_demo"], int(cure["demo_sqft"] * c["demo_per_sf"])) if cure.get("demo_sqft") else 0)
    permits = int(cure["permit_cost"]) if cure.get("permit_cost") is not None else (max(c["min_legalize"], int(cure["legalize_sqft"] * c["legalize_per_sf"])) if cure.get("legalize_sqft") else 0)
    liens = int(cure.get("liens_total") or 0)
    on_us = liens if (cure.get("liens_paid_by") or "buyer") == "buyer" else 0
    lines = []
    if cure.get("legal_sqft"):
        lines.append(f"ARV on the legal {int(cure['legal_sqft']):,} sf only" + (f" ({int(cure['demo_sqft']):,} sf to demolish is not counted)" if cure.get("demo_sqft") else ""))
    if demo:
        lines.append(f"Demolition about {_m(demo)}" + ("" if cure.get("demo_cost") is not None else f" ({int(cure.get('demo_sqft') or 0):,} sf × ${c['demo_per_sf']}/sf, min {_m(c['min_demo'])})"))
    if permits:
        lines.append(f"Permits / legalization about {_m(permits)}" + ("" if cure.get("permit_cost") is not None else f" ({int(cure.get('legalize_sqft') or 0):,} sf × ${c['legalize_per_sf']}/sf, min {_m(c['min_legalize'])})"))
    if liens:
        lines.append(f"Liens and fines {_m(liens)}: " + ("the SELLER pays them at closing (not in our number)" if not on_us else "on US if we take it as-is (in our number)"))
    if cure.get("repairs") is not None:
        lines.append("Rehab: none (Rodrigo: the inside is done)" if int(cure["repairs"]) == 0 else f"Rehab set by Rodrigo: {_m(int(cure['repairs']))}")
    return {"demo": demo, "permits": permits, "liens_on_us": on_us, "liens": liens, "total": demo + permits + on_us, "lines": lines}


def subject(p: dict[str, Any], cure: dict[str, Any] | None) -> dict[str, Any]:
    """The house as a buyer can legally sell it: the comps engine sizes it by legal_sqft when one is known."""
    if cure and cure.get("legal_sqft"):
        return {**p, "sqft": int(cure["legal_sqft"]), "legal_sf": True}
    return p


def _m(v: int | float) -> str:
    return f"${v:,.0f}"


def revalue(conn, cfg: Any, property_id: int, comps_fn=None) -> dict[str, Any] | None:
    """Re-run the valuation for one house with its cure record (fresh comps when a comps_fn is given, else the comps the engine
    stored). Returns the new valuation row's numbers, or None when the house has none."""
    from .ranking import rate
    from .valuation import review_exclusions, store_valuation, value_property
    p = db.row_to_dict(conn.execute("SELECT * FROM properties WHERE id=?", (property_id,)).fetchone())
    if not p:
        return None
    v = db.row_to_dict(conn.execute("SELECT * FROM valuations WHERE property_id=?", (property_id,)).fetchone()) or {}
    fn = comps_fn
    if fn is None:
        stored = [{**c, "property_type": p.get("property_type")} for c in json.loads(v.get("comps") or "[]")]
        fn = (lambda _p, s=stored: s) if stored else None
    try:
        res = value_property(p, cfg, fn, exclude=review_exclusions(conn, property_id), cure=get(conn, property_id))
    except Exception as e:  # noqa: BLE001
        log.exception("revalue %s: %s", property_id, e)
        return None
    sc = conn.execute("SELECT score FROM scores WHERE property_id=?", (property_id,)).fetchone()
    rating = rate(sc[0] if sc else None, res["list_price"], res["mao"], cfg, res["arv_method"]) if sc else (v.get("rating") or "COLD")
    store_valuation(conn, property_id, res, rating)
    return res


def summary(conn, cfg: Any, property_id: int) -> list[str]:
    """The cure lines for a card or a snapshot (empty when the house has no record)."""
    return costs(get(conn, property_id), cfg)["lines"]
