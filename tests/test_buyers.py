"""Cash buyers around a deal (Rodrigo, 2026-11-14, 82 Tortuga Rd): area pull → Chrome fills owners / LLCs → ranked lists."""
from datetime import date

from titan.buyers import area, build


def _item(street, lat, lng, price, remarks="", agent="Ana Agent", email="ana@x.com", sold="2026-06-01"):
    return {"location": {"address": {"line": street, "city": "Palm Springs", "state_code": "FL", "postal_code": "33461",
                                     "coordinate": {"lat": lat, "lon": lng}}},
            "description": {"text": remarks, "sqft": 1500, "beds": 3, "baths": 2, "sold_price": price, "sold_date": sold},
            "list_price": price, "last_sold_date": sold, "advertisers": [{"name": agent, "email": email, "phone": "5615550101"}]}


def fake_scrape(location, listing_type, radius=None, **kw):
    if radius is None:  # the subject lookup
        return [_item("82 Tortuga Rd", 26.64, -80.09, 245000)] if listing_type == "sold" else []
    if listing_type == "sold":
        return [_item("10 Palm Way", 26.641, -80.09, 230000, "Sold AS-IS, cash only, needs work"),
                _item("20 Oak St", 26.65, -80.09, 410000, "Fully renovated with brand new kitchen", agent="Flip Lister", email="fl@x.com"),
                _item("30 Elm St", 26.66, -80.10, 380000, "Lovely home")]
    if listing_type == "for_sale":
        return [_item("40 Pine St", 26.642, -80.091, 399000, "Completely remodeled", agent="Flip Lister", email="fl@x.com")]
    return []


def test_pull_then_build(tmp_path):
    p = area.pull("82 Tortuga Rd, Palm Springs, FL 33461", out_dir=tmp_path, scrape=fake_scrape)
    rows = {r["address"]: r for r in area.read_csv(p)}
    assert rows["10 Palm Way"]["signal"] == "INVESTOR_BUY" and rows["20 Oak St"]["signal"] == "FLIP_RESALE"
    assert float(rows["10 Palm Way"]["miles"]) < 0.2 and rows["10 Palm Way"]["listing_agent_email"] == "ana@x.com"

    # the Chrome step fills in owners / buyer's agent, then Sunbiz + people
    for r in rows.values():
        if r["address"] == "10 Palm Way":
            r.update(buyer_name="Sunshine Flips, L.L.C.", deed_date="2026-06-01", cash="yes",
                     buyer_agent="Bob Buyer", buyer_agent_email="bob@x.com")
        if r["address"] == "20 Oak St":
            r.update(buyer_name="John Smith", seller_name="SUNSHINE FLIPS LLC")
        if r["address"] == "30 Elm St":
            r.update(buyer_name="Wells Fargo Bank NA")
    area.write_csv(p, list(rows.values()), area.COLS)
    area.write_csv(p.parent / "llcs.csv", [{"llc_name": "Sunshine Flips LLC", "status": "ACTIVE", "website": "sunshineflips.com",
                                             "phone": "561-555-0199", "officers": "Maria Lopez (MGR); Tom Lee (AMBR)",
                                             "registered_agent": "Maria Lopez, 1 Main St"}], build.LLC_COLS)
    area.write_csv(p.parent / "people.csv", [{"name": "Maria Lopez", "llc_name": "Sunshine Flips LLC", "role": "manager",
                                               "phone": "561-555-0100", "source": "company website"}], build.PEOPLE_COLS)
    # re-pulling keeps what Chrome filled in
    area.pull("82 Tortuga Rd, Palm Springs, FL 33461", out_dir=tmp_path, scrape=fake_scrape)
    assert {r["address"]: r for r in area.read_csv(p)}["10 Palm Way"]["buyer_name"] == "Sunshine Flips, L.L.C."

    s = build.build(p.parent, price=245000, today=date(2026, 11, 14))
    assert s["entities"] == 1  # the bank and the person are not buyers
    b = area.read_csv(p.parent / "cash_buyers.csv")[0]
    assert b["buys_near"] == "1" and b["flips_sold_near"] == "1" and b["cash_buys"] == "1" and b["fits_deal_price"] == "yes"
    assert b["website"] == "sunshineflips.com" and b["looked_up"] == "yes"
    people = area.read_csv(p.parent / "llc_people.csv")
    assert [x["name"] for x in people] == ["Maria Lopez", "Tom Lee"] and people[0]["phone"] == "561-555-0100"
    agents = {a["agent"]: a for a in area.read_csv(p.parent / "investor_agents.csv")}
    assert agents["Bob Buyer"]["represented_investor_buyer"] == "1"
    assert agents["Flip Lister"]["listed_investor_flip"] == "1" and agents["Flip Lister"]["listed_renovated_flip"] == "1"
    assert agents["Ana Agent"]["sold_to_investor"] == "1"
    assert "Sunshine" in (p.parent / "summary.md").read_text()


def test_kind():
    assert build.kind("ABC HOLDINGS LLC") == "LLC" and build.kind("Opendoor Property J LLC") == "INSTITUTIONAL"
    assert build.kind("Mary Jones") == "PERSON" and build.kind("Bank of America NA") == "SKIP"
    assert build.kind("Smith Family Trust") == "TRUST"
    assert build.norm("Sunshine Flips, L.L.C.") == build.norm("SUNSHINE FLIPS LLC")
