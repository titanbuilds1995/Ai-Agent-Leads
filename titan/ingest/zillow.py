"""Zillow via Apify (default actor maxcopell/zillow-scraper)."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from .common import first_str, g, price_history_from
from .normalize import Listing, finalize, to_float, to_int

ZILLOW_TYPES = {"single_family": "isSingleFamily", "townhouse": "isTownhouse", "multi_family": "isMultiFamily",
                "land": "isLotLand", "condo": "isCondo", "mobile": "isManufactured"}
ALL_TYPES = ["isSingleFamily", "isTownhouse", "isMultiFamily", "isLotLand", "isCondo", "isManufactured", "isApartment"]
COUNTY_SLUG = {"Miami-Dade": "miami-dade-county-fl", "Broward": "broward-county-fl", "Palm Beach": "palm-beach-county-fl"}


def search_urls(cfg: Any, sold: bool = False) -> list[str]:
    m = cfg["market"]
    urls = []
    for county in m["counties"]:
        slug = COUNTY_SLUG.get(county, county.lower().replace(" ", "-") + "-county-fl")
        fs: dict[str, Any] = {"price": {"min": m["price_min"], "max": m["price_max"]}, "doz": {"value": str(m["lookback_days"])}}
        wanted = {ZILLOW_TYPES[t] for t in m["property_types"] if t in ZILLOW_TYPES}
        for t in ALL_TYPES:
            if t not in wanted:
                fs[t] = {"value": False}
        if sold:
            fs = {"isRecentlySold": {"value": True}, "isForSaleByAgent": {"value": False}, "isForSaleByOwner": {"value": False},
                  "isNewConstruction": {"value": False}, "isComingSoon": {"value": False}, "isAuction": {"value": False},
                  "isForSaleForeclosure": {"value": False}, "doz": {"value": "6m"}}
        sqs = {"isMapVisible": True, "filterState": fs, "isListVisible": True, "usersSearchTerm": county + " County FL"}
        urls.append(f"https://www.zillow.com/{slug}/?searchQueryState={quote(json.dumps(sqs, separators=(',', ':')))}")
    return urls


def actor_input(cfg: Any, sold: bool = False) -> dict[str, Any]:
    return {"searchUrls": [{"url": u} for u in search_urls(cfg, sold=sold)],
            "extractionMethod": "PAGINATION_WITH_ZOOM_IN",
            "maxItems": cfg.get("ingest.apify.max_items_per_actor", 3000)}


def map_item(it: dict[str, Any]) -> Listing | None:
    street = first_str(g(it, "addressStreet", "address.streetAddress", "streetAddress", "listingAddress.street", "listingAddress.streetAddress"))
    city = first_str(g(it, "addressCity", "address.city", "city", "listingAddress.city"))
    if not street and isinstance(g(it, "address"), str):
        parts = [p.strip() for p in it["address"].split(",")]
        if len(parts) >= 3:
            street, city = parts[0], parts[1]
            st_zip = parts[2].split()
            it.setdefault("addressState", st_zip[0] if st_zip else "FL")
            it.setdefault("addressZipcode", st_zip[1] if len(st_zip) > 1 else "")
    if not street or not city:
        return None
    attr = g(it, "attributionInfo") or {}
    hist = price_history_from(g(it, "priceHistory") or [], price_key=("price",), event_key=("event",), date_key=("date", "time"))
    status = first_str(g(it, "homeStatus", "keystoneHomeStatus", "statusType", "listing_sub_type"))
    flags = []
    sub = g(it, "listing_sub_type") or {}
    if g(sub, "is_forAuction") or g(it, "isAuction") or "auction" in (status or "").lower(): flags.append("auction")
    if g(sub, "is_bankOwned") or g(it, "isBankOwned") or "foreclos" in (status or "").lower(): flags.append("reo")
    if g(it, "isPreforeclosureAuction") or g(sub, "is_preforeclosure"): flags.append("pre_foreclosure")
    l = Listing(
        street=street, city=city, state=first_str(g(it, "addressState", "address.state", "listingAddress.state"), "FL"), zip=first_str(g(it, "addressZipcode", "address.zipcode", "listingAddress.zipcode", "listingAddress.zip"), ""),
        county=first_str(g(it, "county")),
        list_price=to_int(g(it, "unformattedPrice", "listingPrice.amount", "price.amount", "price", "listPrice", "listingPrice")),
        price_history=hist, days_on_market=to_int(g(it, "daysOnZillow", "timeOnZillow", "daysOnMarket")),
        beds=to_float(g(it, "beds", "bedrooms")), baths=to_float(g(it, "baths", "bathrooms")),
        sqft=to_int(g(it, "area", "livingArea", "livingAreaValue")),
        lot_sqft=_lot_sqft(g(it, "lotAreaValue", "lotSize"), g(it, "lotAreaUnit", "lotAreaUnits")),
        year_built=to_int(g(it, "yearBuilt")), property_type=first_str(g(it, "homeType", "propertyType")),
        remarks=first_str(g(it, "description", "remarks"), "") or "", photos_count=to_int(g(it, "photoCount")) or (len(g(it, "photos") or []) or None),
        listing_url=_url(first_str(g(it, "detailUrl", "url", "hdpUrl"))), source="zillow", source_id=first_str(g(it, "zpid", "id")),
        listing_status=status, listed_at=first_str(g(it, "datePosted", "listingDate", "dateListed")),
        estimate=to_int(g(it, "zestimate")), hoa=to_int(g(it, "monthlyHoaFee", "hoa", "hoaFee")), status_flags=flags,
        lat=to_float(g(it, "latitude", "latLong.latitude")), lng=to_float(g(it, "longitude", "latLong.longitude")),
        agent_name=first_str(g(attr, "agentName", "listingAgents.0.memberFullName"), g(it, "brokerName_agent", "agentName")),
        agent_phone_raw=first_str(g(attr, "agentPhoneNumber", "listingAgents.0.phone"), g(it, "agentPhone")),
        agent_email=first_str(g(attr, "agentEmail", "listingAgents.0.email")),
        brokerage=first_str(g(attr, "brokerName", "brokerageName"), g(it, "brokerName")),
        agent_profile_url=first_str(g(attr, "listingAgents.0.profileUrl")),
        raw=it,
    )
    return finalize(l)


def _lot_sqft(v: Any, unit: Any) -> int | None:
    f = to_float(v)
    if f is None:
        return None
    if unit and "acre" in str(unit).lower():
        return int(f * 43560)
    return int(f)


def _url(u: str | None) -> str | None:
    if not u:
        return None
    return u if u.startswith("http") else "https://www.zillow.com" + u
