import json
from urllib.parse import parse_qs, urlparse

from titan.ingest import zillow


def test_every_search_url_carries_a_county_map_area(cfg):
    for sold in (False, True):
        urls = zillow.search_urls(cfg, sold=sold)
        assert len(urls) == len(cfg["market"]["counties"])
        for county, u in zip(cfg["market"]["counties"], urls):
            sqs = json.loads(parse_qs(urlparse(u).query)["searchQueryState"][0])
            b = sqs["mapBounds"]
            assert b == zillow.MAP_BOUNDS[county] and b["north"] > b["south"] and b["east"] > b["west"]


def test_live_card_shape_maps_zip():
    it = {"zpid": "1", "listingPrice": {"amount": 625000}, "homeType": "SINGLE_FAMILY", "daysOnZillow": 3,
          "listingAddress": {"street": "10960 SW 25th St", "city": "Miami", "state": "FL", "zipCode": "33165"},
          "bedrooms": 4, "bathrooms": 3, "livingArea": 2016}
    l = zillow.map_item(it)
    assert (l.zip, l.county, l.list_price, l.days_on_market) == ("33165", "Miami-Dade", 625000, 3)
