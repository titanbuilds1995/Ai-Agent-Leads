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
