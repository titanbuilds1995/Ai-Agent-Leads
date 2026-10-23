import json

from titan.scoring import run_scoring, score_property, score_rules
from titan.ingest.pipeline import ingest
from tests.conftest import FIXTURES


def _p(remarks, **kw):
    d = {"remarks": remarks, "days_on_market": 10, "price_cuts": 0, "back_on_market": 0, "vacant": 0, "status_flags": []}
    d.update(kw)
    return d


def test_hot_remarks_score_high(cfg):
    r = score_property(None, _p("CASH ONLY. Estate sale, sold as-is, needs TLC. Priced to sell, bring all offers.", days_on_market=74, price_cuts=2), cfg)
    assert r["score"] >= 65
    sigs = {c["signal"] for c in r["cited"]}
    assert {"cash_only", "as_is", "estate_probate", "dom_over_60", "two_plus_cuts"} <= sigs
    assert all(c["phrase"] for c in r["cited"])


def test_turnkey_scores_low(cfg):
    r = score_property(None, _p("Fully renovated turnkey home. Multiple offers received, highest and best due Monday."), cfg)
    assert r["score"] <= 25
    assert {n["signal"] for n in r["negatives"]} >= {"fully_renovated", "turnkey", "multiple_offers", "highest_and_best"}


def test_as_is_alone_is_weak(cfg):
    weak = score_property(None, _p("Great family home. Sold as-is."), cfg)
    strong = score_property(None, _p("Needs work, sold as-is, cash only."), cfg)
    assert weak["score"] < 15 and strong["score"] >= 40


def test_structured_flags_count(cfg):
    r = score_property(None, _p("Nice home.", status_flags=["short_sale"], back_on_market=1, vacant=1), cfg)
    sigs = {c["signal"] for c in r["cited"]}
    assert {"distress_status", "back_on_market", "vacant"} <= sigs


def test_every_scored_property_over_40_has_citation(conn, cfg):
    ingest(conn, cfg, fixture=FIXTURES / "listings_sample.json")
    counts = run_scoring(conn, cfg, llm=None)
    assert counts["scored"] > 0
    rows = conn.execute("SELECT score, cited FROM scores").fetchall()
    for r in rows:
        if r["score"] >= 40:
            assert json.loads(r["cited"])
    # second run is a no-op
    assert run_scoring(conn, cfg, llm=None)["scored"] == 0


def test_fixture_ordering(conn, cfg):
    ingest(conn, cfg, fixture=FIXTURES / "listings_sample.json")
    run_scoring(conn, cfg, llm=None)
    def s(street_like):
        return conn.execute("SELECT s.score FROM scores s JOIN properties p ON p.id=s.property_id WHERE p.street LIKE ?", (street_like,)).fetchone()[0]
    assert s("107 N%") >= 65          # hot1 estate/cash/as-is
    assert s("135 NW 15th%") <= 25           # cold1 renovated, multiple offers (Davie)
