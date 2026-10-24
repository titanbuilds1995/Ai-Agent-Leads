"""Common Listing shape + the normalization every source goes through."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..util import county_for, display_street, normalize_address, normalize_phone, parse_iso

TYPE_MAP = {
    "single_family": "single_family", "single family": "single_family", "singlefamily": "single_family", "sfr": "single_family",
    "single-family": "single_family", "single_family_home": "single_family", "house": "single_family", "home": "single_family",
    "residential": "single_family", "detached": "single_family",
    "townhouse": "townhouse", "townhome": "townhouse", "townhomes": "townhouse", "town house": "townhouse",
    "multi_family": "multi_family", "multi-family": "multi_family", "multi family": "multi_family", "multifamily": "multi_family",
    "duplex": "multi_family", "triplex": "multi_family", "quadplex": "multi_family", "fourplex": "multi_family",
    "duplex_triplex": "multi_family", "multi_family_home": "multi_family", "income property": "multi_family",
    "land": "land", "lot": "land", "lots": "land", "vacant land": "land", "lots/land": "land", "lot/land": "land",
    "condo": "condo", "condos": "condo", "condominium": "condo", "apartment": "condo", "co-op": "condo", "coop": "condo",
    "mobile": "mobile", "manufactured": "mobile", "mobile home": "mobile", "manufactured_home": "mobile",
}

FLAG_PATTERNS = {
    "short_sale": r"\bshort[\s-]?sale\b",
    "reo": r"\b(reo|bank[\s-]owned|lender[\s-]owned|foreclosed|hud[\s-]owned|hud home|fannie mae|freddie mac|homepath|homesteps)\b|(?<!pre-)(?<!pre )\bforeclosure\b",
    "auction": r"\bauction\b",
    "pre_foreclosure": r"\bpre[\s-]?foreclosure\b|\blis pendens\b|\bnotice of default\b",
    "probate": r"\bprobate\b|\bpersonal representative\b|\bletters of administration\b",
    "not_a_home": r"\b(boat slip|boat dock(age)? (for sale|only)|dock slip|marina slip|wet slip|dry slip|parking (space|spot)s? (for sale|only)|"
                  r"deeded parking|storage unit|cabana (for sale|only)|timeshare|time share|fractional (ownership|interest)|"
                  r"lot rent|land lease community|leased land)\b",
    "estate": r"\bestate sale\b|\binherited\b|\bheirs?\b|\bdeceased\b|\btrust sale\b|\bsold by (the )?estate\b",
}
VACANT_RE = re.compile(r"\bvacant\b|\bunoccupied\b|\beasy to show\b.*\bvacant\b|\bno tenants?\b", re.I)


@dataclass
class Listing:
    street: str
    city: str
    state: str
    zip: str
    list_price: int | None
    source: str
    county: str | None = None
    original_list_price: int | None = None
    price_history: list[dict[str, Any]] = field(default_factory=list)
    days_on_market: int | None = None
    beds: float | None = None
    baths: float | None = None
    sqft: int | None = None
    lot_sqft: int | None = None
    year_built: int | None = None
    property_type: str | None = None
    units: int | None = None
    remarks: str = ""
    photos_count: int | None = None
    listing_url: str | None = None
    source_id: str | None = None
    listing_status: str | None = None
    listed_at: str | None = None
    last_price_change_at: str | None = None
    estimate: int | None = None
    hoa: int | None = None
    status_flags: list[str] = field(default_factory=list)
    lat: float | None = None
    lng: float | None = None
    agent_name: str | None = None
    agent_phone_raw: str | None = None
    agent_phone: str | None = None
    agent_email: str | None = None
    brokerage: str | None = None
    agent_profile_url: str | None = None
    raw: dict[str, Any] | None = None

    # derived
    price_cuts: int = 0
    back_on_market: bool = False
    vacant: bool = False

    @property
    def address_key(self) -> str:
        return normalize_address(self.street, self.city, self.state, self.zip)

    @property
    def address_line(self) -> str:
        return f"{self.street}, {self.city}, {self.state} {self.zip}".strip()

    def to_db(self) -> dict[str, Any]:
        d = asdict(self)
        d["back_on_market"] = int(self.back_on_market)
        d["vacant"] = int(self.vacant)
        for k in ("agent_name", "agent_phone_raw", "agent_phone", "agent_email", "brokerage", "agent_profile_url"):
            d.pop(k, None)
        return d


def to_int(v: Any) -> int | None:
    if v is None or v == "" or isinstance(v, (dict, list)):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = re.sub(r"[^\d.]", "", str(v))
    try:
        return int(float(s)) if s else None
    except ValueError:
        return None


def to_float(v: Any) -> float | None:
    if v is None or v == "" or isinstance(v, (dict, list)):
        return None
    try:
        return float(re.sub(r"[^\d.]", "", str(v))) if not isinstance(v, (int, float)) else float(v)
    except ValueError:
        return None


def norm_type(v: Any) -> str | None:
    if not v:
        return None
    s = str(v).strip().lower().replace("-", "_") if "_" in str(v) else str(v).strip().lower()
    return TYPE_MAP.get(s) or TYPE_MAP.get(s.replace("_", " ")) or TYPE_MAP.get(s.replace(" ", "_")) or s


# "not a short sale", "no REO", "non-foreclosure" must not trigger the flag
_DISTRESS = r"(short[\s-]?sale|reo|bank[\s-]owned|lender[\s-]owned|foreclosure|auction)"
NEGATED_RE = re.compile(rf"\b(not|no|non|never)[\s-]+(a |an |)?{_DISTRESS}(\s*(,|/|or|and|nor)\s*(a |an |)?{_DISTRESS})*\b", re.I)


def detect_flags(text: str, extra: dict[str, bool] | None = None) -> list[str]:
    text = NEGATED_RE.sub(" ", text or "")
    flags = [name for name, pat in FLAG_PATTERNS.items() if re.search(pat, text, re.I)]
    for k, v in (extra or {}).items():
        if v and k not in flags:
            flags.append(k)
    return flags


def finalize(l: Listing) -> Listing:
    """Derive county, flags, price cuts, back-on-market, vacancy, type, phone. Idempotent."""
    l.street = display_street(l.street)
    l.city = re.sub(r"\s+", " ", (l.city or "")).strip().title()
    l.state = (l.state or "FL").upper()[:2]
    l.zip = (l.zip or "")[:5]
    l.county = county_for(l.city, l.county)
    l.property_type = norm_type(l.property_type)
    l.agent_phone = normalize_phone(l.agent_phone_raw) if l.agent_phone_raw else l.agent_phone
    if l.days_on_market is None and parse_iso(l.listed_at):
        dom = max((datetime.now(timezone.utc) - parse_iso(l.listed_at)).days, 0)
        # a list date years back with no current-listing history is an old first-listing date, not real time on market
        l.days_on_market = dom if dom <= 730 or l.price_history else None
    l.remarks = (l.remarks or "").strip()
    text = f"{l.remarks} {l.listing_status or ''} {' '.join(l.status_flags)}"
    l.status_flags = detect_flags(text, {f: True for f in l.status_flags})
    l.vacant = bool(VACANT_RE.search(l.remarks))
    # price history → cuts, back on market, original price
    hist = sorted([h for h in l.price_history if h.get("price") or h.get("event")], key=lambda h: h.get("date") or "")
    cuts, listed_prices, last_change = 0, [], None
    prev_price = None
    for h in hist:
        ev = (h.get("event") or "").lower()
        p = to_int(h.get("price"))
        if "back on market" in ev or "relisted" in ev or ("listed" in ev and listed_prices):
            l.back_on_market = True
        if "listed" in ev and p:
            listed_prices.append(p)
        if "price change" in ev or "price cut" in ev or "reduced" in ev or (p and prev_price and p < prev_price and "sold" not in ev):
            if p and (prev_price is None or p < prev_price):
                cuts += 1
                last_change = h.get("date")
        if p:
            prev_price = p
    l.price_cuts = max(cuts, l.price_cuts)
    if last_change and not l.last_price_change_at:
        l.last_price_change_at = last_change
    if not l.original_list_price:
        l.original_list_price = listed_prices[0] if listed_prices else l.list_price
    if l.original_list_price and l.list_price and l.original_list_price > l.list_price and l.price_cuts == 0:
        l.price_cuts = 1
    if l.units is None and l.property_type == "multi_family":
        m = re.search(r"\b(duplex|triplex|quadplex|fourplex|(\d)\s*[- ]?units?)\b", l.remarks, re.I)
        if m:
            l.units = {"duplex": 2, "triplex": 3, "quadplex": 4, "fourplex": 4}.get(m.group(1).lower()) or to_int(m.group(2))
    return l


def passes_filters(l: Listing, cfg: Any, today: datetime | None = None) -> tuple[bool, str]:
    m = cfg["market"]
    excluded = [f for f in (l.status_flags or []) if f in m.get("exclude_flags", [])]
    if excluded:
        return False, f"excluded {excluded[0]}"
    if l.county not in m["counties"]:
        return False, f"county {l.county}"
    if l.property_type not in m["property_types"]:
        return False, f"type {l.property_type}"
    if l.property_type == "multi_family" and l.units and l.units > m.get("max_units", 4):
        return False, f"units {l.units}"
    if not l.list_price or not (m["price_min"] <= l.list_price <= m["price_max"]):
        return False, f"price {l.list_price}"
    if l.property_type != "land" and l.sqft and l.sqft < m.get("min_sqft", 0):
        return False, f"sqft {l.sqft}"
    status = (l.listing_status or "active").lower()
    if any(s in status for s in ("pending", "sold", "contingent", "off market", "off_market", "closed", "withdrawn")):
        return False, f"status {status}"
    today = today or datetime.now(timezone.utc)
    lookback = timedelta(days=m["lookback_days"])
    listed = parse_iso(l.listed_at)
    changed = parse_iso(l.last_price_change_at)
    recent_new = listed is not None and today - listed <= lookback
    recent_cut = changed is not None and today - changed <= lookback
    dom_recent = l.days_on_market is not None and l.days_on_market <= m["lookback_days"]
    # Targeting happens after ingest (titan/targeting.py), so by default every active listing is kept.
    if m.get("require_recent", False) and not (recent_new or recent_cut or dom_recent or l.price_cuts > 0):
        return False, "not new or price-reduced in window"
    return True, "ok"


def exclude_flagged(conn, cfg: Any) -> int:
    """Drop already-stored listings that are REO / short sale (market.exclude_flags): lead_status EXCLUDED, valuation removed,
    so they are never scored, valued, ranked or contacted. Re-reads the remarks, so it also catches rows stored before the rule."""
    import json
    bad = set(cfg["market"].get("exclude_flags", []))
    if not bad:
        return 0
    n = 0
    for r in conn.execute("SELECT id, remarks, listing_status, status_flags FROM properties WHERE lead_status != 'EXCLUDED'").fetchall():
        stored = json.loads(r["status_flags"] or "[]") if isinstance(r["status_flags"], str) else (r["status_flags"] or [])
        flags = set(detect_flags(f"{r['remarks'] or ''} {r['listing_status'] or ''}")) | set(stored)
        hit = sorted(flags & bad)
        if hit:
            conn.execute("UPDATE properties SET lead_status='EXCLUDED', lead_note=? WHERE id=?", (f"excluded: {', '.join(hit)}", r["id"]))
            conn.execute("DELETE FROM valuations WHERE property_id=?", (r["id"],))
            n += 1
    return n
