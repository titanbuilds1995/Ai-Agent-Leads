"""The comp engine on the mistakes Rodrigo caught (2026-10-27/28) and the daily loop that keeps it honest."""
import json
import random

from titan import comps_engine as ce
from titan import comps_loop as cl
from titan.valuation import value_property


def _sale(i, price, sqft=1500, lat=26.10, lng=-80.20, remarks="", ptype="single_family", beds=3, year=1975, **kw):
    return {"address": f"{100 + i} NW {i} St, Tamarac, FL 33321", "sold_price": price, "sqft": sqft, "property_type": ptype, "lat": lat, "lng": lng,
            "zip": "33321", "sold_date": f"2026-{(i % 9) + 1:02d}-10", "remarks": remarks, "beds": beds, "year_built": year, "mls": f"F{i}", **kw}


TOWNHOUSE = {"street": "73 Ann Lee Ln", "city": "Tamarac", "zip": "33321", "property_type": "single_family", "sqft": 1800, "beds": 3, "baths": 2,
             "year_built": 1974, "lot_sqft": 2200, "hoa": 250, "lat": 26.10, "lng": -80.20, "list_price": 223788,
             "remarks": "Spacious townhouse in a quiet HOA community, needs updating."}


def _mixed_pool():
    pool = []
    for i in range(6):          # detached pool houses nearby, the ones that polluted the card
        pool.append(_sale(i, 496000 + i * 3000, sqft=1750, remarks="Beautiful single family home with pool", lot_sqft=7500, pool=True))
    for i in range(6, 12):      # the real neighbours: attached, same product
        pool.append(_sale(i, 280000 + (i - 6) * 5000, sqft=1800, remarks="Renovated townhome, end unit", lot_sqft=2100, hoa=240,
                          lat=26.1005, lng=-80.2005))
    return pool


def test_product_reads_a_townhouse_in_an_hoa_whatever_the_feed_calls_it():
    assert ce.product(TOWNHOUSE) == "attached"
    assert ce.product({"property_type": "single_family", "remarks": "Nice house", "lot_sqft": 7500}) == "detached"
    assert ce.product({"property_type": "single_family", "remarks": "", "lot_sqft": 1900, "hoa": 180}) == "attached"
    assert ce.product({"property_type": "multi_family"}) == "multi"
    assert ce.product({"property_type": "single_family", "units": 2}) == "multi"
    assert ce.product({"property_type": "land"}) == "land"
    assert ce.product({"property_type": "single_family", "raw": json.dumps({"description": {"sub_type": "townhouse"}})}) == "attached"


def test_townhouse_is_comped_against_attached_sales_only(cfg):
    sel = ce.select(TOWNHOUSE, _mixed_pool(), ce.params(cfg, "attached"))
    assert sel["product"] == "attached"
    assert len(sel["comps"]) == 6 and all("townhome" in c["remarks"] for c in sel["comps"])
    reasons = [why for _, why in sel["dropped"]]
    assert sum("different product" in r for r in reasons) == 6
    est = ce.arv(TOWNHOUSE, sel["comps"], ce.params(cfg, "attached"))
    assert 270000 <= est["arv"] <= 320000            # not $496k
    assert "renovated" in est["basis"]


def test_engine_valuation_and_snapshot_agree_on_the_townhouse(cfg):
    res = value_property(TOWNHOUSE, cfg, lambda p: _mixed_pool())
    assert res["arv_method"] == "comps" and 270000 <= res["arv"] <= 320000
    assert res["comp_grade"] in "AB"
    assert res["comp_notes"]["product"] == "attached"
    assert any("different product" in d for d in res["comp_notes"]["dropped"])
    from titan.comps_report import pick_comps
    comps, area = pick_comps(TOWNHOUSE, _mixed_pool(), cfg)
    assert sorted(c["address"] for c in comps) == sorted(c["address"] for c in res["comps"])


def test_waterfront_and_new_builds_never_comp_a_dry_old_house(cfg):
    dry = {"property_type": "single_family", "sqft": 1500, "beds": 3, "year_built": 1960, "lot_sqft": 7000, "lat": 26.10, "lng": -80.20,
           "zip": "33321", "remarks": "Older home on a dry lot, needs work", "list_price": 400000}
    pool = [_sale(i, 450000, remarks="Updated home") for i in range(5)]
    pool += [_sale(10 + i, 1200000, remarks="Waterfront home with dock, ocean access", water=True) for i in range(3)]
    pool += [_sale(20 + i, 900000, remarks="New construction, completed 2026", year=2026) for i in range(2)]
    sel = ce.select(dry, pool, ce.params(cfg, "detached"))
    assert len(sel["comps"]) == 5
    reasons = [why for _, why in sel["dropped"]]
    assert sum("waterfront" in r for r in reasons) == 3 and sum("new construction" in r for r in reasons) == 2
    wet = {**dry, "remarks": "Waterfront pool home on a wide canal"}
    sel2 = ce.select(wet, pool, ce.params(cfg, "detached"))
    assert all(c["flags"]["waterfront"] for c in sel2["comps"])      # a waterfront subject uses the waterfront sales


def test_a_new_build_at_977_per_sf_is_dropped_from_a_1942_duplex(cfg):
    duplex = {"property_type": "multi_family", "units": 2, "sqft": 2000, "beds": 4, "year_built": 1942, "lat": 26.01, "lng": -80.15, "zip": "33020",
              "remarks": "Duplex, both units rented", "list_price": 650000}
    pool = [_sale(i, 520000 + i * 10000, sqft=1950, ptype="multi_family", year=1950 + i, lat=26.01, lng=-80.15) for i in range(6)]
    pool.append(_sale(9, 3100000, sqft=2100, ptype="multi_family", year=1958, lat=26.01, lng=-80.15, remarks="Luxury duplex, fully rebuilt"))
    sel = ce.select(duplex, pool, ce.params(cfg, "multi"))
    assert all(c["sold_price"] < 1000000 for c in sel["comps"])
    assert any("far off the group" in why for _, why in sel["dropped"])


def test_split_layout_and_unpermitted_work_are_flagged_and_discounted(cfg):
    carlton = {"property_type": "single_family", "sqft": 1700, "beds": 3, "year_built": 1955, "lat": 26.13, "lng": -80.12, "zip": "33304",
               "remarks": "Main house 2/2 plus a detached guest cottage 1/1 with separate entrance. Sold as-is.", "list_price": 850000}
    f = ce.flags(carlton)
    assert f["split"] and not f["unpermitted"]
    mccarthy = {"property_type": "single_family", "sqft": 1166, "beds": 4, "year_built": 1960, "lat": 26.0, "lng": -80.2, "zip": "33021",
                "remarks": "Seller bought it as-is cash only and is selling as-is; buyer must hold seller harmless for work done without permit.",
                "list_price": 380000}
    f2 = ce.flags(mccarthy)
    assert f2["unpermitted"] and f2["sqft_suspect"]
    pool = [_sale(i, 600000, sqft=1200, beds=3, year=1960, lat=26.0, lng=-80.2, remarks="Renovated home") for i in range(6)]
    est = ce.arv(mccarthy, ce.select(mccarthy, pool, ce.params(cfg))["comps"], ce.params(cfg))
    plain = ce.arv(mccarthy, ce.select(mccarthy, pool, ce.params(cfg))["comps"], ce.params(cfg), apply_layout=False)
    assert est["arv"] < plain["arv"] and est["discount_pct"] == 12 and len(est["reasons"]) == 2
    res = value_property(mccarthy, cfg, lambda p: pool)
    assert res["comp_grade"] != "A" and "unpermitted" in res["comp_notes"]["flags"]


def test_grade_is_loud_when_the_set_is_thin_or_far():
    subject = {"property_type": "single_family", "sqft": 1500, "lat": 26.10, "lng": -80.20, "zip": "33321", "beds": 3, "year_built": 1975}
    good = [_sale(i, 400000 + i * 2000, remarks="Renovated", lat=26.1002, lng=-80.2002) for i in range(8)]
    sel = ce.select(subject, good, ce.DEFAULTS)
    assert ce.grade(subject, sel)["grade"] == "A"
    thin = ce.select(subject, good[:2], ce.DEFAULTS)
    g = ce.grade(subject, thin)
    assert g["grade"] in "CD" and any("only 2 sales" in w for w in g["why"])
    far = [_sale(i, 300000 + i * 60000, lat=26.12, lng=-80.22) for i in range(5)]       # ~1.9 mi away, wide spread
    g2 = ce.grade(subject, ce.select(subject, far, ce.DEFAULTS))
    assert g2["grade"] in "CD"
    assert "size is unknown" in " ".join(ce.grade({**subject, "sqft": None}, sel)["why"])


def test_dedupe_and_deed_transfers():
    rows = [_sale(1, 400000), {**_sale(1, 400000), "mls": None, "address": "101 NW 1 St, Tamarac, FL 33321"}, _sale(2, 1000)]
    assert len(ce.dedupe(rows)) == 2
    sel = ce.select({"property_type": "single_family", "sqft": 1500, "zip": "33321"}, rows, ce.DEFAULTS)
    assert [why for _, why in sel["dropped"]] == ["not an arm's-length price"]


def test_tuning_file_overrides_config(cfg, tmp_path, monkeypatch):
    monkeypatch.setitem(cfg.raw["comps"], "tuning_file", str(tmp_path / "t.json"))
    assert ce.params(cfg)["radius_miles"] == 0.5
    (tmp_path / "t.json").write_text(json.dumps({"global": {"radius_miles": 0.75, "beds_tol": 2}, "attached": {"sqft_tol_pct": 15}}))
    assert ce.params(cfg)["radius_miles"] == 0.75 and ce.params(cfg)["beds_tol"] == 2
    assert ce.params(cfg, "attached")["sqft_tol_pct"] == 15 and ce.params(cfg, "detached")["sqft_tol_pct"] == 25


def _synthetic_pool(z, n=60, seed=1):
    """A ZIP where price is $300/sf ± noise for ordinary houses, with a few polluters."""
    rng = random.Random(seed)
    pool = []
    for i in range(n):
        sq = rng.randint(1100, 2200)
        pool.append(_sale(i, int(sq * rng.uniform(280, 320)), sqft=sq, lat=26.10 + rng.uniform(-0.006, 0.006), lng=-80.20 + rng.uniform(-0.006, 0.006),
                          remarks=rng.choice(["Renovated", "Updated kitchen", "As-is", ""]), year=rng.randint(1955, 1995), zip=z))
    pool += [_sale(n + i, 2500000, sqft=2000, remarks="Waterfront estate", water=True, zip=z) for i in range(3)]
    pool += [_sale(n + 10 + i, 1500000, sqft=2100, year=2026, remarks="New construction", zip=z) for i in range(2)]
    for c in pool:
        c["address"] = c["address"].replace("33321", z)
    return pool


def test_backtest_scores_real_sales_and_tuning_only_keeps_a_clear_gain(conn, cfg, tmp_path, monkeypatch):
    monkeypatch.setitem(cfg.raw["comps"], "tuning_file", str(tmp_path / "tuning.json"))
    for z in ("33321", "33322", "33323"):
        conn.execute("INSERT INTO comps_cache(key,fetched_at,comps) VALUES(?,?,?)", (f"hh-sold:{z}:2026-10-28", "2026-10-28T10:00:00Z", json.dumps(_synthetic_pool(z))))
    pools = cl.sold_pools(conn)
    assert set(pools) == {"33321", "33322", "33323"}
    cases = cl.test_cases(pools, per_zip=20)
    score = cl.backtest(pools, ce.params(cfg), cases)
    assert score["n"] >= 50 and score["mdape"] < 8          # the polluters never reach the number
    best, best_score, trials = cl.tune(pools, ce.params(cfg), cases, max_seconds=30, passes=1)
    assert len(trials) >= 2 and best_score["mdape"] <= score["mdape"]
    baseline = {**score, "params": {k: ce.params(cfg)[k] for k in cl.GRID}}
    # a tiny gain on a few sales is noise: not adopted
    assert cl.adopt(cfg, conn, best, {**best_score, "n": 20}, baseline, "2026-10-28") is False
    row = conn.execute("SELECT adopted, note FROM comps_calibration ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == 0 and "too few sales" in row[1]
    # a clear gain on enough sales is kept and written to the tuning file the engine reads
    assert cl.adopt(cfg, conn, {**best, "radius_miles": 0.75}, {**best_score, "n": 300, "mdape": score["mdape"] - 1.0}, baseline, "2026-10-28") is True
    assert ce.params(cfg)["radius_miles"] == 0.75
    assert json.loads((tmp_path / "tuning.json").read_text())["history"][-1]["date"] == "2026-10-28"


class _FakeLLM:
    available = True
    model = "fake"

    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def structured(self, purpose, system, user, schema, max_tokens=1024):
        self.prompts.append((purpose, user))
        return self.answer


def test_claude_review_drops_only_real_keys_and_feeds_the_valuation(loaded, cfg):
    pid = loaded.execute("SELECT property_id FROM valuations WHERE arv_method LIKE 'comps%' LIMIT 1").fetchone()[0]
    from titan import db
    p = db.row_to_dict(loaded.execute("SELECT * FROM properties WHERE id=?", (pid,)).fetchone())
    comps = json.loads((__import__("tests.conftest", fromlist=["FIXTURES"]).FIXTURES / "sold_comps_sample.json").read_text())
    sel = ce.select(p, comps, ce.params(cfg, ce.product(p)), want=12)
    real = ce.comp_key(sel["comps"][0])
    loaded.execute("INSERT OR IGNORE INTO lessons(kind,outreach_id,card_gid,text,created_at) VALUES('first_touch',NULL,'g1','re check these comps, they are super highly priced','2026-10-27T00:00:00Z')")
    llm = _FakeLLM({"drop": [{"key": real, "reason": "pool house, ours has none"}, {"key": "made|up", "reason": "x"}],
                    "subject_flags": ["split layout"], "arv_low": 480000, "arv_high": 520000, "confidence": "medium", "note": "Dropped the pool house."})
    r = cl.claude_review(loaded, cfg, llm, p, sel, "2026-10-28")
    assert json.loads(r["exclude_keys"]) == [real] and r["arv_low"] == 480000
    assert "super highly priced" in llm.prompts[0][1] and llm.prompts[0][0] == "comps_review"
    from titan.valuation import review_exclusions
    assert review_exclusions(loaded, pid) == {real}
    res = value_property(p, cfg, lambda _p: comps, exclude=review_exclusions(loaded, pid))
    assert all(ce.comp_key(c) != real for c in res["comps"]) and res["comp_notes"]["excluded"] == 1
    # an answer that would empty the table is ignored
    llm2 = _FakeLLM({"drop": [{"key": ce.comp_key(c), "reason": "no"} for c in sel["comps"]], "subject_flags": [], "arv_low": 0, "arv_high": 0,
                     "confidence": "low", "note": ""})
    r2 = cl.claude_review(loaded, cfg, llm2, p, sel, "2026-10-28")
    assert json.loads(r2["exclude_keys"]) == []


def test_the_loop_runs_end_to_end_and_the_report_has_a_comps_section(loaded, cfg, tmp_path, monkeypatch):
    monkeypatch.setitem(cfg.raw["comps"], "tuning_file", str(tmp_path / "tuning.json"))
    monkeypatch.setitem(cfg.raw["paths"], "reports", str(tmp_path / "reports"))
    comps = json.loads((__import__("tests.conftest", fromlist=["FIXTURES"]).FIXTURES / "sold_comps_sample.json").read_text())
    for z in ("33125", "33321"):
        loaded.execute("INSERT INTO comps_cache(key,fetched_at,comps) VALUES(?,?,?)", (f"hh-sold:{z}:2026-10-28", "2026-10-28T10:00:00Z", json.dumps(_synthetic_pool(z, 40))))
    pid = loaded.execute("SELECT property_id FROM valuations WHERE arv_method LIKE 'comps%' LIMIT 1").fetchone()[0]
    loaded.execute("INSERT INTO outreach(agent_id,property_ids,channel,status,run_date,created_at,asana_task_id) VALUES(1,?, 'email','sent','2026-10-28','2026-10-28T00:00:00Z','card1')",
                   (json.dumps([pid]),))
    llm = _FakeLLM({"drop": [], "subject_flags": [], "arv_low": 500000, "arv_high": 540000, "confidence": "medium", "note": "Set looks right."})

    class Asana:
        def __init__(self):
            self.comments = []

        def comment(self, gid, text):
            self.comments.append((gid, text))
    asana = Asana()
    out = cl.run(loaded, cfg, lambda p: comps, llm, asana=asana, run_date="2026-10-28")
    assert out["pools"] == 2 and out["baseline"]["n"] > 0 and out["working_set"] >= 1
    assert out["claude_reviews"] >= 1 and loaded.execute("SELECT COUNT(*) FROM comps_review").fetchone()[0] >= 1
    assert loaded.execute("SELECT comp_grade FROM valuations WHERE property_id=?", (pid,)).fetchone()[0] in "ABCD"
    lines = cl.summary_lines(loaded, cfg, "2026-10-28")
    assert any("Accuracy on real sales" in l for l in lines) and any("Comps grades" in l for l in lines)
    assert any("Claude's review" in l for l in lines)
    from titan.report import build_report
    assert "## Comps (how good the numbers are)" in build_report(loaded, cfg, "2026-10-28")
    from titan.report_html import build_html
    assert "How good the numbers are" in build_html(loaded, cfg, "2026-10-28")
    # the second run finds no review due (7-day spacing) and still re-values without noise
    out2 = cl.run(loaded, cfg, lambda p: comps, llm, asana=asana, run_date="2026-10-28")
    assert out2["claude_reviews"] == 0


def test_arv_moves_are_logged_and_posted_once_on_the_lead_card(loaded, cfg):
    pid = loaded.execute("SELECT property_id FROM valuations WHERE arv_method LIKE 'comps%' LIMIT 1").fetchone()[0]
    loaded.execute("INSERT INTO outreach(agent_id,property_ids,channel,status,run_date,created_at,asana_task_id) VALUES(1,?, 'email','sent','2026-10-28','2026-10-28T00:00:00Z','cardX')",
                   (json.dumps([pid]),))
    loaded.execute("UPDATE valuations SET arv=arv*2, comp_grade='A' WHERE property_id=?", (pid,))     # yesterday's number was wrong
    comps = json.loads((__import__("tests.conftest", fromlist=["FIXTURES"]).FIXTURES / "sold_comps_sample.json").read_text())
    props = [p for p in cl.working_set(loaded) if p["id"] == pid]
    assert props and props[0]["asana_task_id"] == "cardX"
    changes = cl.revalue(loaded, cfg, lambda p: comps, props, "2026-10-28")
    assert len(changes) == 1 and changes[0]["new_arv"] < changes[0]["old_arv"]
    assert "ARV" in cl.change_comment(changes[0]) and "$" in cl.change_comment(changes[0])
    assert loaded.execute("SELECT COUNT(*) FROM comps_changes WHERE property_id=?", (pid,)).fetchone()[0] == 1


def test_comp_lessons_pick_rodrigos_comp_comments_only(conn):
    for t in ("re check these comps, they are super highly priced", "say we can close in 30 days", "it's a townhouse, wrong product"):
        conn.execute("INSERT INTO lessons(kind,outreach_id,card_gid,text,created_at) VALUES('first_touch',NULL,?,?,'2026-10-27T00:00:00Z')", (t[:5], t))
    got = cl.comp_lessons(conn)
    assert len(got) == 2 and all("30 days" not in g for g in got)
