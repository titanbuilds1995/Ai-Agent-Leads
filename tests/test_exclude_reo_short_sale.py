"""Rodrigo, 2026-10-24: REO / bank-owned and short sale listings are never pursued."""
import json

from titan.ingest.normalize import Listing, detect_flags, exclude_flagged, finalize, passes_filters
from titan.scoring import run_scoring
from titan.valuation import run_valuation


def _l(remarks, status="for_sale"):
    return finalize(Listing(street="1 NW 1st St", city="Miami", state="FL", zip="33125", list_price=300000, source="t",
                            remarks=remarks, property_type="single_family", sqft=1200, days_on_market=10, listing_status=status))


def test_flags_catch_reo_and_short_sale_but_not_negations_or_pre_foreclosure():
    assert "short_sale" in detect_flags("Short sale, subject to lender approval")
    assert "short_sale" in detect_flags("SHORT-SALE. Bring offers")
    for t in ("Bank owned property, sold as-is", "REO. Fannie Mae HomePath", "Foreclosure, no seller disclosure", "HUD home"):
        assert "reo" in detect_flags(t), t
    for t in ("NOT a short sale or foreclosure!", "This is not bank owned", "No REO, no short sale, regular sale",
              "Pre-foreclosure, seller needs to sell before auction"):
        assert not {"reo", "short_sale"} & set(detect_flags(t)), t


def test_new_listings_are_filtered_out(cfg):
    ok, why = passes_filters(_l("Short sale. Needs work, cash only."), cfg)
    assert not ok and why == "excluded short_sale"
    ok, why = passes_filters(_l("Bank owned. As-is."), cfg)
    assert not ok and why == "excluded reo"
    assert passes_filters(_l("Needs work. Not a short sale."), cfg)[0]


def test_already_stored_listings_are_removed_and_never_valued(loaded, cfg):
    pid = loaded.execute("SELECT id FROM properties LIMIT 1").fetchone()[0]
    loaded.execute("UPDATE properties SET remarks='Short sale, lender approval required. Needs work.', status_flags='[]' WHERE id=?", (pid,))
    out = run_scoring(loaded, cfg, None, force=True)
    assert out["excluded_reo_short_sale"] >= 1
    assert loaded.execute("SELECT lead_status FROM properties WHERE id=?", (pid,)).fetchone()[0] == "EXCLUDED"
    run_valuation(loaded, cfg, None, min_score=0)
    assert loaded.execute("SELECT 1 FROM valuations WHERE property_id=?", (pid,)).fetchone() is None
    assert exclude_flagged(loaded, cfg) == 0        # idempotent
