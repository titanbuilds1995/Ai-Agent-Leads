import json

from titan import db
from titan.ingest import realtor, zillow
from titan.ingest.normalize import finalize, passes_filters
from titan.ingest.pipeline import ingest, load_fixture
from titan.util import normalize_address, normalize_phone
from tests.conftest import FIXTURES


def test_normalize_address_variants():
    a = normalize_address("107 N.W. 11th Street", "Miami", "FL", "33125")
    b = normalize_address("107 NW 11th St", "miami", "fl", "33125-1234")
    assert a == b == "107 nw 11th st|miami|fl|33125"
    assert normalize_address("1 Main St Apt 4B", "Miami", "FL", "33125").endswith("#4b|miami|fl|33125")


def test_phone_normalization():
    assert normalize_phone("(305) 555-0101") == "+13055550101"
    assert normalize_phone("1-305-555-0101") == "+13055550101"
    assert normalize_phone("555-0101") is None


def test_mappers_produce_consistent_listing():
    items = json.loads((FIXTURES / "listings_sample.json").read_text())
    r = realtor.map_item(items[0])
    z = zillow.map_item(items[12])
    assert r.county == "Miami-Dade" and r.property_type == "single_family" and r.agent_phone == "+13055550101"
    assert r.price_cuts == 2 and r.original_list_price == 429000 and r.list_price == 385000
    assert "estate" in r.status_flags and r.vacant
    assert z.county == "Broward" and z.property_type == "single_family" and z.agent_phone == "+13055550102"
    assert z.price_cuts == 1 and z.listing_url.startswith("https://www.zillow.com/")
    assert z.brokerage == "Keller Williams Miami"


def test_back_on_market_detected():
    items = json.loads((FIXTURES / "listings_sample.json").read_text())
    z = zillow.map_item(items[14])
    assert z.back_on_market is True


def test_filters(cfg):
    items = json.loads((FIXTURES / "listings_sample.json").read_text())
    ls = {it["_source"] + str(i): (realtor.map_item(it) if it["_source"] == "realtor" else zillow.map_item(it)) for i, it in enumerate(items)}
    reasons = {k: passes_filters(v, cfg)[1] for k, v in ls.items()}
    assert reasons["realtor7"].startswith("price")        # $2.4M
    assert reasons["realtor8"].startswith("status")       # pending
    assert reasons["realtor9"].startswith("type")         # condo
    assert reasons["realtor10"].startswith("county")      # Orlando
    assert reasons["zillow18"].startswith("not new")      # stale
    assert reasons["realtor0"] == "ok" and reasons["zillow12"] == "ok" and reasons["realtor3"] == "ok"  # land ok


def test_ingest_idempotent_and_dedupes(conn, cfg):
    c1 = ingest(conn, cfg, fixture=FIXTURES / "listings_sample.json")
    n1 = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    a1 = conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
    c2 = ingest(conn, cfg, fixture=FIXTURES / "listings_sample.json")
    n2 = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    assert n1 == n2 and c2["new"] == 0 and c2["updated"] == c1["kept"]
    assert conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == a1
    # listing #1 and #12 are the same address spelled differently → one row
    assert conn.execute("SELECT COUNT(*) FROM properties WHERE zip='33125' AND street LIKE '107%'").fetchone()[0] == 1
    assert c1["filtered"] >= 5


def test_office_line_flagged(conn, cfg):
    ingest(conn, cfg, fixture=FIXTURES / "listings_sample.json")
    rows = conn.execute("SELECT name, is_office_line FROM agents WHERE phone='+13055550106'").fetchall()
    assert len(rows) >= 3 and all(r["is_office_line"] == 1 for r in rows)
    assert conn.execute("SELECT is_office_line FROM agents WHERE phone='+13055550101'").fetchone()[0] == 0


def test_failed_source_does_not_block_others(conn, cfg, monkeypatch):
    from titan.ingest import pipeline

    class BadClient:
        def run_actor(self, actor, inp, max_items, label):
            if "realtor" in actor:
                raise RuntimeError("actor exploded")
            items = json.loads((FIXTURES / "listings_sample.json").read_text())
            return [i for i in items if i["_source"] == "zillow"]

    listings, status = pipeline.run_sources(cfg, BadClient(), sources=["realtor", "zillow"])
    assert status["realtor"]["ok"] is False and status["zillow"]["ok"] is True
    assert len(listings) == status["zillow"]["count"] > 0
