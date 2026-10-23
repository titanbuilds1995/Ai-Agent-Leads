from titan.ingest.normalize import Listing, finalize
from titan.shortlist import rank, write


def _l(mls, price, sqft, original=None, bom=False, county="Broward", t="single_family"):
    l = finalize(Listing(street=f"{mls} NW 1st St", city="", state="FL", zip="", county=county, list_price=price, sqft=sqft,
                         original_list_price=original, property_type=t, source="mls", source_id=mls,
                         last_price_change_at="2026-10-23T00:00:00+00:00"))
    l.back_on_market = bom
    if original:
        l.price_cuts = 1
    return l


def test_bigger_cut_and_cheaper_ppsf_rank_first(cfg, tmp_path):
    ls = [_l(f"M{i}", 400000, 2000, original=410000) for i in range(5)]           # $200/sqft, ~2.4% cut: the median
    ls.append(_l("DEAL", 300000, 2000, original=375000))                            # 20% cut, 25% under median
    ls.append(_l("BOM", 400000, 2000, bom=True, original=405000))
    rows = rank(ls, cfg)
    assert rows[0]["mls"] == "DEAL" and rows[0]["cut_pct"] == 20.0 and rows[0]["ppsf_vs_median_pct"] == 25.0
    assert rows[1]["mls"] == "BOM" and "back on market" in rows[1]["why"]
    paste = write(rows, tmp_path / "s.csv", paste_n=2)
    assert paste.read_text().strip() == "DEAL,BOM"
    assert (tmp_path / "s.csv").read_text().splitlines()[0].startswith("rank,points,mls")
