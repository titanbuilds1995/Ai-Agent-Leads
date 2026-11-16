"""Legal square footage and the cost to get there (Rodrigo, 2026-11-16, Celia Ruiz: "we can only count what can be legalized …
the ARV of what can be legalized and minus all of the cost to get us there")."""
import json

from titan import cure
from titan.approvals import BOT, parse_card, sync_feedback
from titan.comment_router import classify
from titan.valuation import value_property
from tests.test_approvals import FakeAsana, _cards, _sections


def test_his_words_become_the_fields():
    f = cure.parse_comment("legal sf 1564, demolish 500 sf, legalize 532 sf, no rehab, the seller pays the liens. permits 30k, demo 15k")
    assert f == {"legal_sqft": 1564, "demo_sqft": 500, "legalize_sqft": 532, "demo_cost": 15000, "permit_cost": 30000, "repairs": 0, "liens_paid_by": "seller"}
    f = cure.parse_comment("500 sq ft have to be demolished, we assume the liens, liens are 59,780")
    assert f["demo_sqft"] == 500 and f["liens_paid_by"] == "buyer" and f["liens_total"] == 59780 and "repairs" not in f
    assert cure.parse_comment("the inside of the house is beautiful, everything is in great shape") == {"repairs": 0}
    assert cure.parse_comment("send it") is None and cure.parse_comment("ask her about the roof") is None
    assert {"cure", "numbers"} <= classify(["legal sf 1564, demolish 500 sf"])
    assert "cure" not in classify(["send it"])


def test_costs_default_from_square_feet_and_his_figures_win(cfg):
    c = cure.costs({"demo_sqft": 500, "legalize_sqft": 532, "liens_total": 59780, "liens_paid_by": "buyer"}, cfg)
    assert c["demo"] == 7500 and c["permits"] == 13300 and c["liens_on_us"] == 59780 and c["total"] == 7500 + 13300 + 59780
    c = cure.costs({"demo_sqft": 500, "demo_cost": 15000, "legalize_sqft": 532, "permit_cost": 30000, "liens_total": 59780, "liens_paid_by": "seller"}, cfg)
    assert c["demo"] == 15000 and c["permits"] == 30000 and c["liens_on_us"] == 0 and c["total"] == 45000
    assert any("SELLER pays" in l for l in c["lines"])


def _house_and_comps(loaded):
    p = dict(loaded.execute("SELECT * FROM properties WHERE sqft > 1200 AND property_type='single_family' ORDER BY id LIMIT 1").fetchone()
             or loaded.execute("SELECT * FROM properties WHERE sqft > 1200 ORDER BY id LIMIT 1").fetchone())
    v = loaded.execute("SELECT comps FROM valuations WHERE property_id=?", (p["id"],)).fetchone()
    comps = [{**c, "property_type": p.get("property_type")} for c in json.loads((v[0] if v else None) or "[]")]
    return p, comps


def test_the_valuation_runs_on_the_legal_square_footage_and_takes_the_cure_off(loaded, cfg):
    p, comps = _house_and_comps(loaded)
    fn = (lambda _p: comps) if comps else None
    plain = value_property(p, cfg, fn)
    rec = {"legal_sqft": int(p["sqft"] * 0.75), "demo_sqft": int(p["sqft"] * 0.25), "legalize_sqft": 400, "liens_total": 59780, "liens_paid_by": "buyer", "repairs": 0}
    legal = value_property(p, cfg, fn, cure=rec)
    assert legal["repair_cost"] == 0 and legal["arv_method"].endswith("_legal_sf") or legal["arv_method"] in ("list_price",)
    cc = cure.costs(rec, cfg)
    # ARV per the same $/sf on 75% of the size; MAO loses the cure
    if plain.get("ppsf") and legal.get("ppsf"):
        assert legal["arv"] < plain["arv"]
    assert legal["mao"] <= plain["mao"] + plain["repair_cost"] - cc["total"] + 200
    assert legal["comp_notes"]["cure"]["total"] == cc["total"] and any("legal" in l for l in legal["comp_notes"]["cure"]["lines"])
    # seller pays the liens → the liens leave our number
    rec2 = {**rec, "liens_paid_by": "seller"}
    assert value_property(p, cfg, fn, cure=rec2)["mao"] >= legal["mao"] + 59000


def test_set_cure_keeps_rodrigos_values_over_the_files(loaded, cfg):
    p, _ = _house_and_comps(loaded)
    cure.set_cure(loaded, p["id"], {"legal_sqft": 1564, "demo_sqft": 500}, source="rodrigo")
    rec = cure.set_cure(loaded, p["id"], {"legal_sqft": 1200, "liens_total": 59780}, source="files")
    assert rec["legal_sqft"] == 1564 and rec["demo_sqft"] == 500 and rec["liens_total"] == 59780
    res = cure.revalue(loaded, cfg, p["id"])
    assert res is not None and loaded.execute("SELECT mao FROM valuations WHERE property_id=?", (p["id"],)).fetchone()[0] == res["mao"]


class HtmlAsana(FakeAsana):
    def comment_html(self, task, html): self.stories[task].append(html)


def test_a_comment_with_legal_sf_revalues_the_house_and_says_so_on_the_card(loaded, cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("TITAN_MODE", "DRAFT_FOR_APPROVAL")
    cfg.root = tmp_path
    loaded.execute("UPDATE agents SET email=lower(replace(name,' ','.'))||'@example.com', email_confidence='high' WHERE email IS NULL")
    from tests.test_approvals import FakeGmail, run_outreach
    asana = HtmlAsana()
    run_outreach(loaded, cfg, None, FakeGmail(), None, asana=asana)
    gid = next(iter(asana.tasks))
    oid = parse_card(asana.tasks[gid]["notes"])["outreach_id"]
    pid = json.loads(loaded.execute("SELECT property_ids FROM outreach WHERE id=?", (oid,)).fetchone()[0])[0]
    before = loaded.execute("SELECT mao FROM valuations WHERE property_id=?", (pid,)).fetchone()[0]
    asana.stories[gid].append("legal sf is 1100, demolish 400 sf, no rehab, we assume the liens, liens 59,780")
    sync_feedback(loaded, cfg, asana, None)
    rec = cure.get(loaded, pid)
    assert rec["legal_sqft"] == 1100 and rec["demo_sqft"] == 400 and rec["repairs"] == 0 and rec["liens_paid_by"] == "buyer"
    after = loaded.execute("SELECT mao, arv_method FROM valuations WHERE property_id=?", (pid,)).fetchone()
    assert after[0] != before and after[1].endswith("_legal_sf")
    texts = asana.stories[gid]
    assert any(t.startswith(f"{BOT} 📐 Legal-sf math noted") and "internal MAO" in t for t in texts)
    assert any("NUMBERS" in t for t in texts)                      # the snapshot follows, on the same card
