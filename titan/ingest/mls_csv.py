"""MLS (Matrix) CSV exports dropped into raw/mls/. Column names vary by export, so each field is looked up through a list of
aliases (compared lowercase, letters and digits only). Works for a thin "Hot Sheet" export (MLS #, Area, Address, Current Price,
Change Type, St, …) and for a full custom export (City, Zip, Remarks, List Agent email/phone, DOM, Original List Price, …).

Hot-sheet specifics (Miami / Beaches MLS, sample 2026-10-23):
- No city/zip: the county comes from the MLS area code (2-digit = Miami-Dade, 3xxx = Broward, 4000–5899 = Palm Beach).
  Coded areas with a name attached (Treasure Coast, Keys, "Florida Other", outside US) fall outside the market and are filtered.
- "Change Type" is either a price change ("3.45%") or a status change ("T->A", HTML-escaped as "T-&gtA").
  A hot sheet lists recent changes, so a % row is read as a price cut made today. Values over 60% are data-entry fixes, ignored."""
from __future__ import annotations

import csv
import html
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .normalize import Listing, finalize, to_float, to_int

ALIASES: dict[str, list[str]] = {
    "mls": ["mls", "mlslink", "mlsnumber", "mlsno", "listingid", "ml"],
    "street": ["address", "streetaddress", "fulladdress", "addressline", "propertyaddress"],
    "street_number": ["streetnumber", "streetno"],
    "street_name": ["streetname"],
    "unit": ["unit", "unitnumber", "unitno"],
    "city": ["city", "postalcity", "municipality"],
    "zip": ["zip", "zipcode", "postalcode", "zip5"],
    "county": ["county", "countyorparish"],
    "area": ["area", "mlsarea", "mlsareamajor"],
    "price": ["currentprice", "listprice", "price", "currentlistprice"],
    "original_price": ["originallistprice", "originalprice", "olp"],
    "dom": ["dom", "daysonmarket", "adom", "cdom"],
    "beds": ["beds", "bedrooms", "bedstotal", "bedroomstotal"],
    "full_baths": ["fbaths", "fullbaths", "bathsfull", "bathroomsfull"],
    "half_baths": ["hbaths", "halfbaths", "bathshalf", "bathroomshalf"],
    "baths": ["baths", "bathstotal", "totalbaths", "bathroomstotal"],
    "sqft": ["sqftla", "sqftliving", "livingarea", "sqft", "sqftlivingarea", "totalsqft", "approxsqft"],
    "lot_sqft": ["lotsqft", "lotsize", "lotsizesqft", "lotsquarefootage"],
    "year_built": ["yearbuilt", "yrbuilt"],
    "type": ["propertytype", "type", "propertysubtype", "subtype", "proptype"],
    "units": ["numberofunits", "units", "unitstotal"],
    "status": ["st", "status", "listingstatus", "mlsstatus"],
    "change": ["changetype"],
    "list_date": ["listdate", "listingdate", "listingcontractdate"],
    "price_change_date": ["pricechangedate", "lastpricechangedate", "statuschangedate"],
    "remarks": ["remarks", "publicremarks", "propertydescription", "description", "internetremarks"],
    "agent_name": ["listagentfullname", "listagent", "listagentname", "listingagent", "laname"],
    "agent_email": ["listagentemail", "laemail", "listagentemailaddress", "agentemail"],
    "agent_phone": ["listagentdirectphone", "listagentcellphone", "listagentmobilephone", "lacellphone", "laphone",
                    "listagentphone", "agentphone"],
    "brokerage": ["listofficename", "listoffice", "listingoffice", "loname", "brokername"],
    "broker_code": ["listbrokercode", "listagentmlsid", "listagentid"],
    "hoa": ["hoafee", "hoa", "associationfee", "hoamonthly"],
    "lat": ["latitude", "lat"],
    "lng": ["longitude", "lng", "lon"],
}

# Miami MLS / Beaches MLS status codes → words passes_filters understands
STATUS = {"a": "active", "ac": "active contingent", "ps": "pending sale", "p": "pending", "c": "closed", "cs": "closed",
          "csl": "closed", "w": "withdrawn", "x": "off market (expired)", "t": "off market (temp)", "r": "rented"}
INACTIVE = {"x", "t", "w", "c", "cs", "csl"}

TYPE = {"single family": "single_family", "residential": "single_family", "single family residence": "single_family",
        "townhouse": "townhouse", "villa": "townhouse", "multi-family": "multi_family", "multifamily": "multi_family",
        "residential income": "multi_family", "income": "multi_family", "duplex": "multi_family",
        "land": "land", "vacant land": "land", "lots and land": "land", "condo": "condo", "condominium": "condo"}

MAX_CUT_PCT = 60.0


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def county_from_area(area: str | None) -> str | None:
    a = (area or "").strip()
    if not a.isdigit():
        return None            # "6351 Sebastian", "98 Florida Keys", "5940 Florida Other" … outside the market
    n = int(a)
    if len(a) <= 2:
        return None if n == 98 else "Miami-Dade"
    if 3000 <= n <= 3999:
        return "Broward"
    if 4000 <= n <= 5899:
        return "Palm Beach"
    return None


def _type(raw: str | None, street: str) -> str | None:
    t = (raw or "").strip().lower()
    if not t:
        return None
    if "condo" in t and "townhouse" in t:      # Matrix lumps "Condo/Co-Op/Villa/Townhouse": a unit number means condo
        return "condo" if re.search(r"\bunit\b|#", street, re.I) else "townhouse"
    for k, v in TYPE.items():
        if t == k or t.startswith(k):
            return v
    return t


def _change(raw: str | None) -> tuple[float | None, str | None, str | None]:
    """→ (price cut %, previous status code, new status code)."""
    s = html.unescape((raw or "").replace("&gt", ">").replace("&lt", "<")).strip()
    if not s:
        return None, None, None
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*%", s)
    if m:
        pct = abs(float(m.group(1)))
        return (pct if 0 < pct <= MAX_CUT_PCT else None), None, None
    m = re.match(r"^([A-Za-z]*)\s*->\s*([A-Za-z]+)$", s)
    if m:
        return None, (m.group(1) or "new").lower(), m.group(2).lower()
    return None, None, None


def _date(raw: str | None) -> str | None:
    if not raw:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M"):
        try:
            return datetime.strptime(raw.strip(), fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def _read_rows(path: Path) -> list[dict[str, str]]:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            with open(path, newline="", encoding=enc) as fh:
                return list(csv.DictReader(fh))
        except UnicodeDecodeError:
            continue
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        return list(csv.DictReader(fh))


def map_row(row: dict[str, str], as_of: date | None = None) -> Listing | None:
    by_key = {_key(k): (v or "").strip() for k, v in row.items() if k}

    def f(name: str) -> str | None:
        for a in ALIASES[name]:
            v = by_key.get(a)
            if v:
                return v
        return None

    street = f("street") or " ".join(x for x in (f("street_number"), f("street_name")) if x)
    if f("unit") and f("unit") not in street:
        street = f"{street} Unit {f('unit')}"
    street = re.sub(r"\s*Unit\s*#\s*", " Unit ", re.sub(r"\s+", " ", street or "")).strip()
    if not street:
        return None
    county = f("county") or county_from_area(f("area"))
    price = to_int(f("price"))
    cut_pct, prev_status, new_status = _change(f("change"))
    status_code = (f("status") or "").lower()
    stamp = datetime.combine(as_of or date.today(), datetime.min.time(), tzinfo=timezone.utc).isoformat()
    original = to_int(f("original_price"))
    history: list[dict[str, Any]] = []
    if cut_pct and price:
        prior = int(round(price / (1 - cut_pct / 100), -2))
        original = original or prior
        history = [{"date": None, "price": prior, "event": "Listed"}, {"date": stamp[:10], "price": price, "event": "Price Changed"}]
    full, half = to_float(f("full_baths")), to_float(f("half_baths"))
    baths = to_float(f("baths")) or ((full or 0) + 0.5 * (half or 0) if full is not None else None)
    l = Listing(
        street=street, city=f("city") or "", state="FL", zip=(f("zip") or "")[:5], county=county,
        list_price=price, original_list_price=original, price_history=history,
        days_on_market=to_int(f("dom")), beds=to_float(f("beds")), baths=baths, sqft=to_int(f("sqft")),
        lot_sqft=to_int(f("lot_sqft")), year_built=to_int(f("year_built")), property_type=_type(f("type"), street),
        units=to_int(f("units")), remarks=f("remarks") or "", source="mls", source_id=f("mls"),
        listing_status=STATUS.get(status_code, status_code or None), listed_at=_date(f("list_date")),
        last_price_change_at=_date(f("price_change_date")) or (stamp if cut_pct else None),
        hoa=to_int(f("hoa")), lat=to_float(f("lat")), lng=to_float(f("lng")),
        agent_name=f("agent_name"), agent_phone_raw=f("agent_phone"), agent_email=f("agent_email"), brokerage=f("brokerage"),
        raw={k: v for k, v in row.items() if k},
    )
    l = finalize(l)
    if prev_status in INACTIVE and new_status in ("a", "ac"):
        l.back_on_market = True
    if cut_pct and l.price_cuts == 0:
        l.price_cuts = 1
    return l


def parse_file(path: Path, as_of: date | None = None) -> Iterable[Listing]:
    as_of = as_of or datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date()
    for row in _read_rows(path):
        l = map_row(row, as_of)
        if l:
            yield l


def parse_dir(d: Path) -> list[Listing]:
    out: list[Listing] = []
    if not d.exists():
        return out
    for p in sorted(d.glob("*.csv")):
        out.extend(parse_file(p))
    return out
