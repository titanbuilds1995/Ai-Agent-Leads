"""Real epctex/realtor-scraper item shape (camelCase, address.locality, advertisers[].phones), captured 2026-10-23."""
import json
from pathlib import Path

from titan.ingest.realtor import map_item

ITEM = json.loads((Path(__file__).parent / "fixtures" / "realtor_epctex_item.json").read_text())


def test_maps_live_epctex_shape():
    l = map_item(ITEM)
    assert l is not None
    assert (l.street, l.city, l.zip, l.county) == ("233 NW 12th Ct", "Florida City", "33034", "Miami-Dade")
    assert l.list_price == 589900 and l.beds == 5 and l.baths == 4.5 and l.sqft == 1902
    assert l.property_type == "single_family" and l.days_on_market == 0
    assert l.agent_name == "Perpetua Muthoni Emilien" and l.agent_email == "perpetuarealty@gmail.com"
    assert l.agent_phone == "+17542041393" and l.brokerage == "EXP Realty, LLC"
    assert l.lat and l.lng and l.source_id == "9997353279"


def test_old_listings_in_history_are_not_price_cuts():
    # history holds a 2024 listing at $695k and a rental; only the current $589,900 listing counts
    l = map_item(ITEM)
    assert l.original_list_price == 589900 and l.price_cuts == 0 and not l.back_on_market
