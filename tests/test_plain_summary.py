from titan.cli import plain_summary


def test_plain_summary_lists_sources_and_says_when_nothing_is_hot(conn):
    s = {"date": "2026-10-23", "ingest": {"seen": 40, "kept": 12, "new": 12, "filtered": 28,
                                          "sources": {"homeharvest": {"ok": True, "count": 40},
                                                      "zillow": {"ok": False, "error": "APIFY_TOKEN missing"}}},
         "score": {"scored": 12, "claude": 12, "rules": 0, "llm_cost_usd": 0.31}}
    text = plain_summary(conn, s)
    assert "homeharvest  40 listings" in text and "zillow       FAILED: APIFY_TOKEN missing" in text
    assert "kept 12 of 40" in text and "cost $0.31" in text and "no HOT or WARM leads" in text
