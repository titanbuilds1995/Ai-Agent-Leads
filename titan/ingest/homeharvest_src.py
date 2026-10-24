"""Free Realtor.com source via HomeHarvest (github.com/ZacharyHampton/HomeHarvest, pinned 0.8.18, code read 2026-10-23:
it only calls realtor.com's own GraphQL API). No Apify cost. Realtor.com blocks most cloud/datacenter IPs, so this runs
on the Mac; from a cloud container the source fails cleanly and the other sources carry on.

`return_type="raw"` hands back realtor.com's snake_case records (location.address.*, description.*, advertisers[]),
the shape `realtor.map_item` was written for, so both Realtor paths share one mapper."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from ..util import log, today_et
from . import realtor
from .normalize import Listing

# config.yaml market.property_types → HomeHarvest SearchPropertyType values
TYPES = {"single_family": ["single_family"], "townhouse": ["townhomes"], "multi_family": ["multi_family", "duplex_triplex"],
         "land": ["land"], "condo": ["condos"]}

Scrape = Callable[..., list[dict[str, Any]]]


def _scraper() -> Scrape:
    from homeharvest import scrape_property  # lazy: optional dependency, only needed when this source runs
    return scrape_property


def _types(cfg: Any) -> list[str]:
    out: list[str] = []
    for t in cfg["market"]["property_types"]:
        out += [x for x in TYPES.get(t, []) if x not in out]
    return out


def _save_raw(cfg: Any, name: str, items: list[dict[str, Any]]) -> None:
    try:
        d = cfg.path("raw")
        d.mkdir(parents=True, exist_ok=True)
        (Path(d) / f"{today_et()}_homeharvest_{name}.json").write_text(json.dumps(items, default=str))
    except Exception as e:  # noqa: BLE001 — a raw dump is a debugging aid, never fatal
        log.warning("homeharvest: could not save raw %s: %s", name, e)


def fetch_for_sale(cfg: Any, scrape: Scrape | None = None) -> tuple[list[Listing], dict[str, Any]]:
    """Per county: listed in the lookback window + updated in it (catches price cuts on older listings). Deduped."""
    scrape = scrape or _scraper()
    m, hh = cfg["market"], cfg.get("ingest.homeharvest", {}) or {}
    days = m["lookback_days"]
    common = dict(listing_type="for_sale", return_type="raw", property_type=_types(cfg), price_min=m["price_min"],
                  price_max=m["price_max"], exclude_pending=True, limit=hh.get("limit_per_query", 3000),
                  extra_property_data=hh.get("extra_property_data", True), proxy=hh.get("proxy") or None)
    seen: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    capped: list[str] = []
    bands = price_bands(m["price_min"], m["price_max"], hh.get("price_band_width", 100_000))
    queries = 0
    for county in m["counties"]:
        loc = f"{county} County, FL"
        for lo, hi in bands:
            for label, kw in (("new", {"past_days": days}), ("updated", {"updated_in_past_hours": days * 24})):
                queries += 1
                key = f"{county}:{lo // 1000}-{hi // 1000}k:{label}"
                try:
                    items = scrape(loc, **{**common, "price_min": lo, "price_max": hi}, **kw) or []
                except Exception as e:  # noqa: BLE001 — one county/query failing must not drop the rest
                    errors[key] = f"{type(e).__name__}: {e}"[:200]
                    log.warning("homeharvest %s failed: %s", key, e)
                    continue
                if len(items) >= common["limit"]:
                    capped.append(key)    # hit the per-query limit: narrow price_band_width
                    log.warning("homeharvest %s returned the full %d-item limit; some listings may be missing", key, len(items))
                for it in items:
                    if isinstance(it, dict):
                        seen.setdefault(str(it.get("property_id") or it.get("listing_id") or id(it)), it)
    items = list(seen.values())
    if not items and errors and len(errors) == queries:
        raise RuntimeError(f"every HomeHarvest query failed (realtor.com blocks cloud IPs; run on the Mac): {next(iter(errors.values()))}")
    _save_raw(cfg, "for_sale", items)
    from .pipeline import map_items
    listings, extra = map_items("homeharvest", items, realtor.map_item)
    for l in listings:
        l.source = "homeharvest"
    if errors:
        extra["errors"] = errors
    if capped:
        extra["capped_queries"] = capped
    return listings, extra


def price_bands(lo: int, hi: int, width: int) -> list[tuple[int, int]]:
    """$150k–$750k by $100k → (150k,250k), (250k,350k) … so no single query hits the result limit."""
    out, a = [], lo
    while a < hi:
        b = min(a + width, hi)
        out.append((a, b))
        a = b
    return out or [(lo, hi)]


def comps_fn(cfg: Any, conn, scrape: Scrape | None = None) -> Callable[[dict[str, Any]], list[dict[str, Any]]]:
    """Sold comps for the subject's ZIP (one query per ZIP per day, cached in comps_cache), then valuation.select_comps
    filters by distance/size. A county-wide sold query caps out at the result limit and drops most of a big county, so
    the county query is only the fallback when the ZIP is unknown or too thin."""
    from .. import db
    cache: dict[str, list[dict[str, Any]]] = {}
    months = cfg.get("valuation.comp_months", 6)

    min_zip = max(cfg.get("valuation.min_comps", 3) * 3, 8)

    def load(location: str) -> list[dict[str, Any]]:
        key = f"hh-sold:{location}:{today_et()}"
        if key in cache:
            return cache[key]
        row = conn.execute("SELECT comps FROM comps_cache WHERE key=?", (key,)).fetchone()
        if row:
            cache[key] = json.loads(row["comps"])
            return cache[key]
        try:
            items = (scrape or _scraper())(location, listing_type="sold", return_type="raw",
                                           past_days=int(timedelta(days=30 * months).days), property_type=_types(cfg),
                                           limit=cfg.get("ingest.homeharvest.limit_per_query", 3000), extra_property_data=False)
        except Exception as e:  # noqa: BLE001
            log.error("homeharvest sold comps for %s failed: %s", location, e)
            cache[key] = []
            return []
        comps = []
        for it in items or []:
            l = realtor.map_item(it) if isinstance(it, dict) else None
            price = _sold_price(it) if l else None
            if not l or not price:
                continue
            comps.append({"address": l.address_line, "sold_price": price, "sqft": l.sqft, "lot_sqft": l.lot_sqft,
                          "property_type": l.property_type, "lat": l.lat, "lng": l.lng, "zip": l.zip,
                          "sold_date": it.get("last_sold_date") or (it.get("description") or {}).get("sold_date")})
        conn.execute("INSERT OR REPLACE INTO comps_cache(key,fetched_at,comps) VALUES(?,?,?)", (key, db.iso(), json.dumps(comps)))
        cache[key] = comps
        return comps

    def fn(p: dict[str, Any]) -> list[dict[str, Any]]:
        z = (p.get("zip") or "")[:5]
        comps = load(z) if len(z) == 5 and z.isdigit() else []
        if len(comps) < min_zip and p.get("county"):
            seen = {c["address"] for c in comps}
            comps = comps + [c for c in load(f"{p['county']} County, FL") if c["address"] not in seen]
        return comps

    return fn


def _sold_price(it: dict[str, Any]) -> int | None:
    from .normalize import to_int
    return to_int((it.get("description") or {}).get("sold_price")) or to_int(it.get("last_sold_price"))
