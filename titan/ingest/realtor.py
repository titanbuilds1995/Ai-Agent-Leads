"""Realtor.com via Apify (default actor epctex/realtor-scraper). Mapper is tolerant to field-name variants."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .normalize import Listing, finalize, to_float, to_int
from .common import g, first_str, price_history_from

TYPE_SLUGS = {"single_family": "single-family-home", "townhouse": "townhome", "multi_family": "multi-family-home",
              "land": "land", "condo": "condo"}


def search_urls(cfg: Any) -> list[str]:
    """One URL per county × (new in window, price reduced)."""
    m = cfg["market"]
    types = ",".join(TYPE_SLUGS[t] for t in m["property_types"] if t in TYPE_SLUGS)
    urls = []
    for county in m["counties"]:
        base = f"https://www.realtor.com/realestateandhomes-search/{quote(county.replace(' ', '-'))}-County_FL/type-{types}/price-{m['price_min']}-{m['price_max']}"
        urls.append(f"{base}/dom-{m['lookback_days']}")
        urls.append(f"{base}/show-price-reduced")
    return urls


def actor_input(cfg: Any) -> dict[str, Any]:
    return {
        "startUrls": [{"url": u} for u in search_urls(cfg)],
        "maxItems": cfg.get("ingest.apify.max_items_per_actor", 3000),
        "includeFloorplans": False,
        "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
    }


def _current_listing_history(hist: Any) -> list[dict[str, Any]]:
    """epctex `history` is the property's whole life (old listings, rentals, sales). Keep only the current sale
    listing: events since its most recent "Listed" that share its MLS number, so old listings don't read as cuts."""
    if not isinstance(hist, list):
        return []
    rows = sorted((h for h in hist if isinstance(h, dict)), key=lambda h: h.get("date") or "")
    ev = lambda h: (h.get("eventName") or h.get("event_name") or "").lower()  # noqa: E731 — camelCase (epctex) or snake_case (realtor API)
    starts = [i for i, h in enumerate(rows) if ev(h) == "listed"]
    if not starts:
        return []
    mls = rows[starts[-1]].get("sourceListingId")
    return [h for h in rows[starts[-1]:] if (not mls or h.get("sourceListingId") in (mls, None)) and "rent" not in ev(h)]


def map_item(it: dict[str, Any]) -> Listing | None:
    addr = g(it, "location.address", "address") or {}
    if isinstance(addr, str):
        addr = {"line": addr}
    street = first_str(g(addr, "line", "street", "street_address"), g(it, "address_line", "streetAddress"))
    city = first_str(g(addr, "city", "locality"), g(it, "city"))
    if not street or not city:
        return None
    desc = g(it, "description") or {}
    if isinstance(desc, str):
        desc = {"text": desc}
    adv = (g(it, "advertisers") or [{}])
    adv = adv[0] if isinstance(adv, list) and adv else (adv if isinstance(adv, dict) else {})
    office = g(adv, "office") or {}
    flags = g(it, "flags") or {}
    status_flags = []
    if g(flags, "is_foreclosure", "isForeclosure"): status_flags.append("reo")
    if g(flags, "is_short_sale", "isShortSale"): status_flags.append("short_sale")
    if g(it, "source.type") == "auction" or g(flags, "is_auction", "isAuction"): status_flags.append("auction")
    photos = g(it, "photos") or []
    l = Listing(
        street=street, city=city, state=first_str(g(addr, "state_code", "state", "region"), "FL"), zip=first_str(g(addr, "postal_code", "zip", "zipcode", "postalCode"), ""),
        county=first_str(g(addr, "county.name", "county"), g(it, "location.county.name")) if not isinstance(g(addr, "county"), dict) or g(addr, "county.name") else None,
        list_price=to_int(g(it, "list_price", "price", "listPrice")),
        original_list_price=to_int(g(it, "original_list_price", "price_history_original", "originalPrice")),
        price_history=price_history_from(g(it, "price_history", "priceHistory") or _current_listing_history(g(it, "history", "property_history")), price_key=("price",), event_key=("event_name", "event", "eventName"), date_key=("date",)),
        days_on_market=to_int(g(it, "days_on_market", "daysOnMarket", "description.days_on_market", "list_date_days", "daysOnMls")),
        beds=to_float(g(desc, "beds", "bedrooms") or g(it, "beds")), baths=to_float(g(desc, "baths", "baths_consolidated", "bathrooms") or g(it, "bathsConsolidated", "baths")),
        sqft=to_int(g(desc, "sqft", "livingArea") or g(it, "sqft")), lot_sqft=to_int(g(desc, "lot_sqft", "lotSize") or g(it, "lotSqft")),
        year_built=to_int(g(desc, "year_built", "yearBuilt") or g(it, "yearBuilt")), property_type=first_str(g(desc, "type", "sub_type"), g(it, "property_type", "propertyType", "type")),
        units=to_int(g(desc, "units") or g(it, "units")), remarks=first_str(g(desc, "text"), g(it, "remarks", "public_remarks"), "") or "",
        photos_count=to_int(g(it, "photo_count", "photoCount")) or (len(photos) if isinstance(photos, list) else None),
        listing_url=first_str(g(it, "href", "url", "permalink_url", "listing_url")), source="realtor",
        source_id=first_str(g(it, "property_id", "listing_id", "id", "listingId")), listing_status=first_str(g(it, "status", "listing_status")),
        listed_at=first_str(g(it, "list_date", "listDate")), last_price_change_at=first_str(g(it, "last_price_change_date", "price_reduced_date")),
        estimate=to_int(g(it, "estimate.estimate", "estimates.current_values.0.estimate", "estimate")), hoa=to_int(g(it, "hoa.fee", "hoa_fee")),
        status_flags=status_flags, lat=to_float(g(addr, "coordinate.lat", "lat") or g(it, "coordinates.latitude")), lng=to_float(g(addr, "coordinate.lon", "lng", "lon") or g(it, "coordinates.longitude")),
        agent_name=first_str(g(adv, "name", "agent_name"), g(it, "agent.name", "agentName")),
        agent_phone_raw=first_str(g(adv, "phone", "phones.0.number"), g(it, "agent.phone", "agentPhone")),
        agent_email=first_str(g(adv, "email"), g(it, "agent.email")),
        brokerage=first_str(g(office, "name"), g(adv, "company"), g(it, "broker.name", "brokerName", "office_name")),
        agent_profile_url=first_str(g(adv, "href", "profile_url", "url")),
        raw=it,
    )
    if g(flags, "is_price_reduced", "isPriceReduced") and l.price_cuts == 0:
        l.price_cuts = 1
    return finalize(l)
