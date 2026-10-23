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
    # one "new" and one "updated" query per county, filtered server-side by price + types
    assert len(fake.calls) == 2 * len(cfg["market"]["counties"])
    kw = fake.calls[0][1]
    assert kw["return_type"] == "raw" and kw["price_min"] == cfg["market"]["price_min"] and "single_family" in kw["property_type"]


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


def test_sold_comps_cached_per_county_per_day(cfg, conn):
    sold = dict(RAW, status="sold", list_price=None, last_sold_price=410000, last_sold_date="2026-08-01")
    fake = FakeScrape({"sold": [sold]})
    fn = homeharvest_src.comps_fn(cfg, conn, scrape=fake)
    comps = fn({"county": "Broward"})
    assert comps[0]["sold_price"] == 410000 and comps[0]["sqft"] == 1210 and comps[0]["lat"] == 26.14
    fn({"county": "Broward"})
    assert len(fake.calls) == 1
    assert json.loads(conn.execute("SELECT comps FROM comps_cache").fetchone()[0])[0]["sold_price"] == 410000
