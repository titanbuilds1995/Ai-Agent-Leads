"""LoopNet through Apify (the only source for the commercial screen; Rodrigo already pays for Apify).

Actor: memo23/loopnet-scraper-ppe (config commercial.loopnet.actor). Two passes:
  1. SEARCH: one LoopNet search URL per asset family (config search_urls), filtered by LoopNet to the price box; the actor drops
     listings whose stated cap is under the floor and keeps the ones that show no cap ("On Request" → NEEDS-OM). Rows carry
     address, price, cap (when shown), type, size, year, broker name/company/phone/email and a truncated description.
  2. DETAILS: the full property sheet (NOI, occupancy, tenancy, lease, date on market, highlights, full description) for the
     in-box rows. LoopNet blocks these pages often; a sheet that does not come through is retried on the next run
     (`detail_level` = summary until it does). Each sheet costs about a cent, capped by detail_budget_usd per run.

`normalize()` turns either shape into the one dict the rest of the module works on."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from ..ingest.apify import ApifyClient
from ..util import log, normalize_phone
from . import extract as x
from .fl_geo import county_for_city, county_from_text

# LoopNet property type labels → our asset classes. Anything not here is out of the box (land, business, special purpose).
ASSET_MAP = [
    ("nnn", r"net lease|nnn|single[- ]tenant|freestanding|fast food|restaurant|drug ?store|pharmacy|bank|auto (?:parts|service|repair|dealership)|"
            r"convenience store|gas station|car wash|day ?care|dollar store|quick service|qsr|drive[- ]thru"),
    ("medical", r"medical|health ?care|dental|surgery|clinic|urgent care|dialysis|veterinar|assisted living|senior|nursing|rehab"),
    ("retail", r"retail|shopping center|strip|storefront|neighborhood center|power center|supermarket|grocery|showroom|"
               r"community center|outlet|mall|service station|liquor"),
    ("hotel", r"hotel|motel|hospitality|resort|inn\b|lodging|bed and breakfast|extended stay"),
    ("industrial", r"industrial|warehouse|flex|distribution|manufacturing|light industrial|cold storage|truck terminal|self[- ]storage|"
                   r"mini storage|contractor|showroom/warehouse|service garage|r&d|research"),
    ("office", r"office|professional|coworking|loft/creative|executive suites"),
    ("multifamily", r"multifamily|multi-family|apartment|duplex|triplex|quadruplex|fourplex|garden|mid-rise|high-rise|low-rise|mobile home|manufactured|"
                    r"student housing|dormitory|residential income"),
    ("land", r"\bland\b|lot\b|development site|development|acre|agricultur|farm|ranch|vacant land"),
    ("business", r"business(?:es)? for sale|business only|franchise|turn-?key business|operating business"),
    ("special", r"church|religious|golf|marina|school|sports|entertainment|theater|cemetery|parking|garage|car lot|funeral|"
                r"bowling|skating|museum|club|racetrack|airport|telecom|data center|special purpose|specialty"),
]
_ASSET_RE = [(k, re.compile(p, re.I)) for k, p in ASSET_MAP]
NNN_TEXT_RE = re.compile(r"\bnnn\b|triple[- ]net|net[- ]leased?|single[- ]tenant|absolute net", re.I)


def asset_class(property_type: str | None, subtype: str | None = None, text: str = "") -> str | None:
    """nnn / retail / medical / hotel / industrial / office / multifamily / land / business / special / None."""
    label = " ".join(s for s in (property_type, subtype) if s)
    if not label and not text:
        return None
    for key, rx in _ASSET_RE:
        if rx.search(label):
            if key in ("retail", "office", "industrial", "medical") and NNN_TEXT_RE.search(text or ""):
                return "nnn"
            return key
    for key, rx in _ASSET_RE:        # fall back to the description
        if rx.search(text or ""):
            return key
    return None


def _first(*vals: Any) -> Any:
    for v in vals:
        if v not in (None, "", [], {}):
            return v
    return None


def _days_since(d: str | None, today: date | None = None) -> int | None:
    if not d:
        return None
    try:
        dt = datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return max(0, ((today or date.today()) - dt).days)


def _brokers(raw: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for b in raw.get("brokers") or []:
        if isinstance(b, dict) and b.get("name"):
            out.append({"name": b.get("name"), "phone": b.get("phone"), "email": b.get("email"), "title": b.get("title"),
                        "company": b.get("company") or raw.get("brokerCompany") or raw.get("Broker")})
    if raw.get("brokerName") and not any(o["name"] == raw["brokerName"] for o in out):
        out.insert(0, {"name": raw.get("brokerName"), "phone": raw.get("brokerPhone"), "email": raw.get("brokerEmail"),
                       "title": raw.get("brokerTitle"), "company": raw.get("brokerCompany")})
    if out and not out[0].get("phone"):
        out[0]["phone"] = _first(raw.get("brokerPhone"), *(raw.get("brokerPhones") or []))
    if out and not out[0].get("email"):
        out[0]["email"] = _first(raw.get("brokerEmail"), raw.get("email"), raw.get("agent_email"), *(raw.get("brokerEmails") or []))
    return out


def _facts(raw: dict[str, Any]) -> dict[str, Any]:
    pf = raw.get("propertyFacts") or {}
    return {str(k): v for k, v in pf.items()} if isinstance(pf, dict) else {}


def normalize(raw: dict[str, Any], today: date | None = None) -> dict[str, Any] | None:
    """One actor row (summary card or full sheet) → the listing dict scoring/outputs use. None when it is not a LoopNet listing."""
    lid = str(_first(raw.get("propertyId"), raw.get("listingId"), raw.get("id")) or "")
    url = _first(raw.get("listingUrl"), raw.get("inferUrl"), raw.get("url")) or ""
    if not lid:
        m = re.search(r"/Listing/(?:[^/]+/)?(\d+)/?", url)
        lid = m.group(1) if m else ""
    if not lid:
        return None
    pf = _facts(raw)
    full = bool(pf) or raw.get("_detailLevel") not in (None, "summary")
    name = _first(raw.get("name"), raw.get("listingName"), raw.get("placardTitle")) or ""
    highlights = raw.get("highlights") or []
    desc = _first(raw.get("description"), raw.get("descriptionHtml")) or ""
    desc = re.sub(r"<[^>]+>", " ", desc)
    text = "\n".join([name, desc, *[str(h) for h in highlights], " ".join(f"{k}: {v}" for k, v in pf.items())])
    stats = " ".join(" ".join(str(s) for s in row) for row in (raw.get("stats") or []) if isinstance(row, (list, tuple)))
    city = _first(raw.get("city"), (raw.get("address") or {}).get("city") if isinstance(raw.get("address"), dict) else None)
    street = raw.get("address") if isinstance(raw.get("address"), str) else (raw.get("address") or {}).get("street")
    price_text = _first(pf.get("Price"), raw.get("priceText"), raw.get("formattedPrice"), raw.get("price"), raw.get("placardPrice"))
    price = raw.get("priceNumeric") if isinstance(raw.get("priceNumeric"), (int, float)) else x.money(price_text)
    lo, hi = x.money_range(price_text) if isinstance(price_text, str) and re.search(r"\s[-–]\s", price_text) else (None, None)
    if lo and hi and lo != hi:
        price = None          # a price range (condos / portfolio) is not an asking price
    ptype = _first(pf.get("PropertyType"), raw.get("propertyType"))
    subtype = _first(pf.get("PropertySubtype"), raw.get("propertySubtype"))
    cap = _first(x.percent(pf.get("CapRate")), x.percent(raw.get("capRatePercent")), x.percent(raw.get("capRate")), x.cap_from_text(text + " " + stats))
    noi = _first(x.money(pf.get("NOI")), x.money(pf.get("NetOperatingIncome")), x.noi(text))
    units = _first(x.number(pf.get("NoUnits")), x.number(raw.get("numberOfUnits")), x.number(raw.get("unitCount")))
    bsf = _first(x.sqft(pf.get("BuildingSize")), x.sqft(raw.get("buildingSize")), raw.get("totalSize") if isinstance(raw.get("totalSize"), (int, float)) else None,
                 x.sqft(raw.get("sizeFormatted")))
    lot = _first(x.sqft(pf.get("LotSize")), x.sqft(raw.get("lotSize")), int(raw["acres"] * 43560) if isinstance(raw.get("acres"), (int, float)) else None)
    occ = _first(x.percent(pf.get("Occupancy")), x.percent(pf.get("PercentLeased")), x.occupancy(text))
    tenancy_fact = (pf.get("Tenancy") or "").lower()
    tenants = _first(x.number(pf.get("NoTenants")), x.tenant_count(text))
    tenants = int(tenants) if tenants else None
    tenancy = "single" if tenancy_fact.startswith("single") else "multi" if tenancy_fact.startswith("multi") else x.tenancy(text, tenants)
    lease = _first((pf.get("LeaseType") or "").lower() or None, x.lease_type(text))
    if lease and lease not in dict(x.LEASE_TYPES):
        lease = x.lease_type(lease) or lease
    county = _first(county_for_city(city), county_from_text(text), raw.get("county"))
    brokers = _brokers(raw)
    b0 = brokers[0] if brokers else {}
    gross, exp = x.gross_income(text), x.expenses(text)
    listed = _first(raw.get("dateOnMarket"), raw.get("listedDate"), raw.get("dateListed"))
    sale_cond = (pf.get("SaleConditions") or "")
    hotel_metrics = x.has_hotel_metrics(text)
    aclass = asset_class(ptype, subtype, text)
    if (pf.get("SaleType") or "").lower().startswith("owner") and aclass not in ("land", "business"):
        pass   # owner/user sale: still a building; the occupancy check will catch "vacant"
    L: dict[str, Any] = {
        "listing_id": lid, "listing_url": url, "name": name, "street": street, "city": city, "state": _first(raw.get("stateCode"), raw.get("state"), "FL"),
        "zip": _first(raw.get("zip"), (raw.get("address") or {}).get("zipCode") if isinstance(raw.get("address"), dict) else None),
        "county": county, "lat": raw.get("latitude"), "lng": raw.get("longitude"),
        "property_type": ptype, "property_subtype": subtype, "asset_class": aclass, "building_class": _first(pf.get("BuildingClass"), raw.get("buildingClass")),
        "price": int(price) if price else None, "price_text": str(price_text or ""), "price_low": lo, "price_high": hi,
        "building_sf": int(bsf) if bsf else None, "lot_sf": int(lot) if lot else None, "year_built": int(x.number(_first(pf.get("YearBuilt"), raw.get("yearBuilt"))) or 0) or None,
        "units": int(units) if units else None, "stated_cap": cap, "stated_noi": noi, "gross_income": gross, "expenses": exp,
        "expense_ratio": x.expense_ratio(noi, gross), "occupancy": occ, "tenants": tenants, "tenancy": tenancy,
        "tenant_names": [], "credit_tenants": x.credit_tenants(text), "lease_type": lease, "remaining_term_years": x.remaining_term_years(text),
        "listed_at": listed, "updated_at": raw.get("dateUpdated"), "days_on_market": _days_since(listed, today),
        "price_history": raw.get("priceHistory") or [], "price_cuts": len([h for h in (raw.get("priceHistory") or []) if isinstance(h, dict) and (h.get("change") or 0) < 0]) or None,
        "previous_price": x.money(raw.get("previousPrice")),
        "broker_name": b0.get("name"), "brokerage": _first(b0.get("company"), raw.get("brokerCompany")), "broker_title": b0.get("title"),
        "broker_phone": normalize_phone(re.sub(r"\s*(x|ext)\.?\s*\d+$", "", str(b0.get("phone") or ""), flags=re.I)) or (b0.get("phone") or None),
        "broker_phone_raw": b0.get("phone"), "broker_email": (b0.get("email") or "").strip().lower() or None, "brokers": brokers,
        "brokerage_size": x.brokerage_size(_first(b0.get("company"), raw.get("brokerCompany"))),
        "description": desc, "highlights": [str(h) for h in highlights], "text": text,
        "motivation": x.motivation_signals(text), "proforma": x.is_proforma(text), "trailing": x.mentions_trailing(text),
        "hotel_metrics": hotel_metrics, "has_rent_roll": x.mentions_rent_roll(text), "sale_type": pf.get("SaleType"), "sale_conditions": sale_cond,
        "portfolio": bool(raw.get("isPortfolio")) or "portfolio of" in name.lower() or "portfolio" in sale_cond.lower()
                     or str(street or "").lower().startswith("portfolio of"),
        "business_only": bool(re.search(r"business (?:for sale|only|included)|businesses for sale", text, re.I)) and aclass not in ("hotel",),
        "status": _first(raw.get("closeStatus"), raw.get("status"), "active"),
        "detail_level": "full" if full else "summary", "detail_error": raw.get("_detailUnavailableReason") if not full else None,
        "photos_count": len(raw.get("images") or raw.get("photos") or []) or None,
        "raw": raw,
    }
    if L["building_sf"] and L["price"]:
        L["ppsf"] = round(L["price"] / L["building_sf"], 2)
    if L["units"] and L["price"]:
        L["price_per_unit"] = int(L["price"] / L["units"])
    return L


# --- the pull ------------------------------------------------------------------------------------------------------------

def search_input(cfg: Any, url: str, max_items: int | None = None) -> dict[str, Any]:
    box = cfg.get("commercial.buyer_box", {})
    ln = cfg.get("commercial.loopnet", {})
    return {
        "startUrls": [{"url": url}], "PriceMin": int(box.get("price_min", 0)), "PriceMax": int(box.get("price_max", 10**9)),
        "CapRateMin": str(box.get("cap_min", 8)), "maxItems": int(max_items or ln.get("max_items_per_search", 2000)),
        "moreResults": bool(ln.get("more_results", True)), "includeListingDetails": False, "proxy": {"useApifyProxy": False},
    }


def detail_input(urls: list[str]) -> dict[str, Any]:
    return {"startUrls": [{"url": u} for u in urls], "includeListingDetails": True, "maxItems": len(urls) + 5, "maxConcurrency": 5,
            "proxy": {"useApifyProxy": False}}


def pull_search(cfg: Any, apify: ApifyClient, families: list[str] | None = None, max_items: int | None = None) -> dict[str, list[dict[str, Any]]]:
    """Run the SEARCH pass: {family: [raw rows]}. Each family is one actor run (one LoopNet search URL)."""
    actor = cfg.get("commercial.loopnet.actor", "memo23/loopnet-scraper-ppe")
    urls: dict[str, str] = cfg.get("commercial.loopnet.search_urls", {}) or {}
    out: dict[str, list[dict[str, Any]]] = {}
    for fam, url in urls.items():
        if families and fam not in families:
            continue
        try:
            out[fam] = apify.run_actor(actor, search_input(cfg, url, max_items), max_items=int(max_items or cfg.get("commercial.loopnet.max_items_per_search", 2000)),
                                       label=f"loopnet_{fam}")
        except Exception as e:  # noqa: BLE001 — one dead family must not kill the pull
            log.error("loopnet search %s failed: %s", fam, e)
            out[fam] = []
    return out


def pull_details(cfg: Any, apify: ApifyClient, urls: list[str], batch: int = 25) -> list[dict[str, Any]]:
    """Run the DETAILS pass on the given listing URLs, in batches. Rows that come back as summary cards stay 'summary'."""
    actor = cfg.get("commercial.loopnet.actor", "memo23/loopnet-scraper-ppe")
    rows: list[dict[str, Any]] = []
    for i in range(0, len(urls), batch):
        chunk = urls[i:i + batch]
        try:
            rows += apify.run_actor(actor, detail_input(chunk), max_items=len(chunk) + 5, label=f"loopnet_details_{i // batch}")
        except Exception as e:  # noqa: BLE001
            log.error("loopnet details batch %d failed: %s", i // batch, e)
    return rows


def dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per listing id; a full sheet beats a summary card."""
    best: dict[str, dict[str, Any]] = {}
    for r in rows:
        L = normalize(r)
        if not L:
            continue
        cur = best.get(L["listing_id"])
        if cur is None or (L["detail_level"] == "full" and cur["detail_level"] != "full"):
            best[L["listing_id"]] = L
    return list(best.values())


def estimate_cost(search_rows: int, detail_rows: int, cfg: Any) -> dict[str, float]:
    """Store prices on 2026-11-08: $0.0015 per search row, $0.0015 + $0.01 per full sheet, $0.007 per run start."""
    per_row, per_sheet, start = 0.0015, 0.0115, 0.007
    fams = len(cfg.get("commercial.loopnet.search_urls", {}) or {})
    return {"search_usd": round(search_rows * per_row + fams * start, 2), "details_usd": round(detail_rows * per_sheet + (detail_rows // 25 + 1) * start, 2)}


def to_json(L: dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in L.items() if k != "raw"}, default=str)
