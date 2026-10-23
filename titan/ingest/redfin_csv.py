"""Fallback: Redfin 'Download All' CSV exports dropped into raw/redfin/."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from .normalize import Listing, finalize, to_float, to_int


def parse_file(path: Path) -> Iterable[Listing]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            r = {k.strip().upper(): (v or "").strip() for k, v in row.items() if k}
            street, city = r.get("ADDRESS"), r.get("CITY")
            if not street or not city:
                continue
            yield finalize(Listing(
                street=street, city=city, state=r.get("STATE OR PROVINCE", "FL"), zip=r.get("ZIP OR POSTAL CODE", ""),
                list_price=to_int(r.get("PRICE")), source="redfin",
                days_on_market=to_int(r.get("DAYS ON MARKET")), beds=to_float(r.get("BEDS")), baths=to_float(r.get("BATHS")),
                sqft=to_int(r.get("SQUARE FEET")), lot_sqft=to_int(r.get("LOT SIZE")), year_built=to_int(r.get("YEAR BUILT")),
                property_type=r.get("PROPERTY TYPE"), listing_url=r.get("URL (SEE HTTPS://WWW.REDFIN.COM/BUY-A-HOME/COMPARATIVE-MARKET-ANALYSIS FOR INFO ON PRICING)") or r.get("URL"),
                source_id=r.get("MLS#"), listing_status=r.get("STATUS"), hoa=to_int(r.get("HOA/MONTH")),
                lat=to_float(r.get("LATITUDE")), lng=to_float(r.get("LONGITUDE")), remarks="",
                agent_name=r.get("LISTING AGENT") or None, agent_phone_raw=r.get("LISTING AGENT PHONE") or None,
                brokerage=r.get("LISTING BROKER") or None,
                raw={k: v for k, v in r.items()},
            ))


def parse_dir(d: Path) -> list[Listing]:
    out: list[Listing] = []
    if not d.exists():
        return out
    for p in sorted(d.glob("*.csv")):
        out.extend(parse_file(p))
    return out
