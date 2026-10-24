"""HomeHarvest source with a fake scraper: no network, and homeharvest itself is never imported."""
import json

from titan.ingest import homeharvest_src

RAW = {
    "property_id": "4951372574", "listing_id": "2970000001", "status": "for_sale", "list_price": 289000,
    "list_date": "2026-10-01T12:00:00Z", "href": "https://www.realtor.com/realestateandhomes-detail/x",
    "location": {"address": {"line": "1520 NW 4th Ave", "city": "Fort Lauderdale", "state_code": "FL", "postal_code": "33311",
                             "coordinate": {"lat": 26.14, "lon": -80.15}},
                 "county": {"name": "Broward", "fips_code": "12011"}},
    "description": {"type": "single_family", "beds": 3, "baths": 2, "sqft": 1210, "lot_sqft": 6000, "year_built": 1958,
                    "text": "Investor special. Sold as-is, cash only. Needs roof and AC. Bring all offers."},
    "advertisers": [{"type": "seller", "name": "Maria Lopez", "email": "maria@example.com",
                     "phones": [{"number": "9545550123", "type": "Mobile"}], "office": {"name": "Lopez Realty"}}],
    "property_history": [
        {"date": "2019-05-01", "event_name": "Sold", "price": 180000},
        {"date": "2026-10-01", "event_name": "Listed", "price": 319000},
        {"date": "2026-10-15", "event_name": "Price Changed", "price": 289000},
    ],
}


class FakeScrape:
    def __init__(self, by_listing_type):
        self.by = by_listing_type
        self.calls = []

    def __call__(self, location, **kw):
        self.calls.append((location, kw))
        return self.by.get(kw["listing_type"], [])


def test_for_sale_maps_realtor_raw_shape(cfg, tmp_path):
    cfg.root = tmp_path
    fake = FakeScrape({"for_sale": [RAW]})
    listings, extra = homeharvest_src.fetch_for_sale(cfg, scrape=fake)
    assert len(listings) == 1 and extra["unmapped"] == 0   # same record from both queries/counties is deduped
    l = listings[0]
    assert (l.street, l.city, l.zip, l.county, l.source) == ("1520 NW 4th Ave", "Fort Lauderdale", "33311", "Broward", "homeharvest")
    assert l.agent_email == "maria@example.com" and l.agent_phone == "+19545550123" and l.brokerage == "Lopez Realty"
    assert l.original_list_price == 319000 and l.price_cuts == 1      # the 2019 sale is not a cut
    assert "cash only" in l.remarks.lower()
    # "new" + "updated" per county per $100k price band, filtered server-side by price + types
    bands = homeharvest_src.price_bands(cfg["market"]["price_min"], cfg["market"]["price_max"], 100_000)
    assert len(fake.calls) == 2 * len(bands) * len(cfg["market"]["counties"])
    kw = fake.calls[0][1]
    assert kw["return_type"] == "raw" and kw["price_min"] == cfg["market"]["price_min"] and "single_family" in kw["property_type"]
    assert max(c[1]["price_max"] for c in fake.calls) == cfg["market"]["price_max"]


def test_price_bands_and_capped_queries_are_reported(cfg, tmp_path):
    assert homeharvest_src.price_bands(150_000, 750_000, 100_000)[0] == (150_000, 250_000)
    assert homeharvest_src.price_bands(150_000, 750_000, 100_000)[-1] == (650_000, 750_000)
    cfg.root = tmp_path
    cfg.raw["ingest"]["homeharvest"]["limit_per_query"] = 1
    _, extra = homeharvest_src.fetch_for_sale(cfg, scrape=FakeScrape({"for_sale": [RAW]}))
    assert extra["capped_queries"]


def test_every_query_failing_raises_so_the_run_summary_shows_it(cfg, tmp_path):
    cfg.root = tmp_path

    def blocked(location, **kw):
        raise ConnectionError("403 from realtor.com")
    try:
        homeharvest_src.fetch_for_sale(cfg, scrape=blocked)
    except RuntimeError as e:
        assert "run on the Mac" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def _sold(i):
    loc = dict(RAW["location"], address=dict(RAW["location"]["address"], line=f"{i} NW 4th Ave"))
    return dict(RAW, location=loc, status="sold", list_price=None, last_sold_price=400000 + i, last_sold_date="2026-08-01")


def test_sold_comps_by_zip_then_county_fallback_cached(cfg, conn):
    calls = []

    def fake(location, **kw):
        calls.append(location)
        return [_sold(i) for i in range(10)] if location == "33311" else [_sold(i) for i in range(5, 15)]
    fn = homeharvest_src.comps_fn(cfg, conn, scrape=fake)
    comps = fn({"county": "Broward", "zip": "33311"})
    assert calls == ["33311"] and len(comps) == 10            # enough in the ZIP: no county query
    assert comps[0]["sold_price"] == 400000 and comps[0]["sqft"] == 1210 and comps[0]["lat"] == 26.14
    fn({"county": "Broward", "zip": "33311"})
    assert calls == ["33311"]                                  # cached for the day


def test_thin_zip_tops_up_from_county_without_duplicates(cfg, conn):
    def fake(location, **kw):
        return [_sold(i) for i in range(2)] if location == "33312" else [_sold(i) for i in range(6)]
    comps = homeharvest_src.comps_fn(cfg, conn, scrape=fake)({"county": "Broward", "zip": "33312"})
    assert len(comps) == 6 and len({c["address"] for c in comps}) == 6
    assert json.loads(conn.execute("SELECT comps FROM comps_cache").fetchone()[0])[0]["sold_price"] == 400000


def test_days_on_market_uses_the_current_listing_not_the_first_ever_listing():
    from datetime import datetime, timedelta, timezone
    from titan.ingest.realtor import map_item
    recent = (datetime.now(timezone.utc) - timedelta(days=20)).strftime("%Y-%m-%d")
    it = dict(RAW, list_date="2019-03-01T00:00:00Z", property_history=[
        {"date": "2019-03-01", "event_name": "Listed", "price": 250000}, {"date": "2019-09-01", "event_name": "Sold", "price": 240000},
        {"date": recent, "event_name": "Listed", "price": 289000}])
    l = map_item(it)
    assert 19 <= l.days_on_market <= 21 and l.listed_at.startswith(recent)
    stale = dict(RAW, list_date="2018-01-01T00:00:00Z", property_history=[])
    assert map_item(stale).days_on_market is None          # 8 years with no history: unknown, not "sitting"
