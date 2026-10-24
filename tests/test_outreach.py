import json
import re
from datetime import datetime, timezone

import pytest

from titan import db
from titan.outreach.compose import BANNED_RE, HTML_RE, URL_RE, compose_email, compose_sms, template_email, validate_email, validate_sms
from titan.outreach.send import plan, run_outreach


def _agent(i=1):
    return {"id": i, "name": "Maria Lopez", "email": "maria.lopez@kwmiami.com", "phone": "+13055550101"}


def _prop(i=1, **kw):
    p = {"id": i, "street": f"{100 + i} NW 11th St", "city": "Miami", "county": "Miami-Dade", "list_price": 385000,
         "remarks": "CASH ONLY. Estate sale, sold as-is, needs TLC.", "cited": json.dumps([{"signal": "cash_only", "phrase": "CASH ONLY. Estate sale"}]),
         "days_on_market": 74, "price_cuts": 2}
    p.update(kw)
    return p


def test_template_email_passes_validation(cfg):
    for seed in range(12):
        d = compose_email(None, cfg, _agent(), [_prop()], seed)
        assert validate_email(d["subject"], d["body"], [_prop()]) == []
        assert "NW 11th St" in d["body"] and "Charles" in d["body"] and "Rodrigo" not in d["body"]
        assert not HTML_RE.search(d["body"]) and not URL_RE.search(d["body"])


def test_first_touch_is_conversation_first(cfg):
    """No price, no offer, no percentage, no cash-offer pitch; asks about the seller's flexibility (Rodrigo, 2026-10-24)."""
    for seed in range(12):
        d = compose_email(None, cfg, _agent(), [_prop()], seed)
        text = d["body"].split("\n\nBest")[0].lower()
        assert "?" in text and any(k in text for k in ("flexib", "room", "really need", "how deep", "condition", "entertain", "holding"))
        assert "$" not in text and "%" not in text and "offer" not in text and "cash" not in text and "offer" not in d["subject"].lower()
        assert "doesn't seem too far" not in text          # no preliminary analysis on this prop → never implied
        assert not BANNED_RE.search(d["body"])             # no listing echo, no fake observations, no corporate phrasing
        assert "—" not in d["body"] and " - " not in d["body"] and "—" not in d["subject"]   # Rodrigo: no dashes


def test_never_quotes_the_listing_back(cfg):
    """Protocol §1: never repeat the listing's own wording to the agent, and never motivation/distress phrases."""
    d = compose_email(None, cfg, _agent(), [_prop()], 0)        # remarks: CASH ONLY. Estate sale, sold as-is, needs TLC.
    low = d["body"].lower()
    assert "estate" not in low and "cash only" not in low and "needs tlc" not in low and "remarks" not in low
    p = _prop(remarks="Needs TLC. Original kitchen, great bones.", cited=json.dumps([{"signal": "needs_work", "phrase": "needs TLC"}]),
              price_cuts=0, days_on_market=20)
    body = compose_email(None, cfg, _agent(), [p], 0)["body"]
    assert "needs TLC" not in body and "great bones" not in body
    assert "dated" in body.lower() or "cosmetic" in body.lower()  # reasons one level deeper instead (§2)


def test_angle_follows_the_facts(cfg):
    from titan.outreach.compose import angle_for
    assert angle_for({"remarks": "", "days_on_market": 90, "price_cuts": 2}) == "reduced"
    assert angle_for({"remarks": "Investor special, cash only", "days_on_market": 10}) == "heavy"
    assert angle_for({"remarks": "Well kept", "year_built": 1955, "days_on_market": 10}) == "heavy"          # old + nothing updated
    assert angle_for({"remarks": "New roof 2022, original kitchen", "year_built": 1955}) == "cosmetic"       # old but systems handled
    assert angle_for({"remarks": "", "year_built": 2015, "days_on_market": 5}) == "newer"
    assert angle_for({"remarks": "", "year_built": 1990, "days_on_market": 95}) == "sitting"
    assert angle_for({"remarks": "", "year_built": 1990, "days_on_market": 12}) == "default"


def test_validator_rejects_protocol_violations(cfg):
    p = [_prop()]
    sig = "\n\nBest regards,\n\nCharles Grant"
    filler = " We buy quite a bit in the area and this could work depending on how deep the project is. What's your read on the condition?"
    for bad in ("I looked at the photos and the roof looks fine on NW 11th St.", "Your NW 11th St listing caught our attention.",
                "I noticed the property needs TLC on NW 11th St and it fits our criteria.", "NW 11th St could be a good fit for our portfolio."):
        assert any("protocol" in x for x in validate_email("Quick question on NW 11th St", bad + filler + sig, p)), bad
    assert any("dash" in x for x in validate_email("Quick question on NW 11th St", "Hey Maria — quick question on NW 11th St." + filler + sig, p))
    assert any("dash" in x for x in validate_email("NW 11th St - quick question", "Hey Maria, quick question on NW 11th St." + filler + sig, p))
    good = "Hey Maria, quick question on NW 11th St. It sounds more dated than distressed; if the major systems are fine it may be close." + filler + sig
    assert validate_email("Quick question on NW 11th St", good, p) == []


def test_close_to_asking_line_only_when_analysis_supports_it(cfg):
    base = dict(arv=500_000, repair_cost=20_000, arv_method="comps", comp_count=6, zip="33125", property_type="single_family")
    near = _prop(list_price=300_000, **base)        # internal high ≈ 500k × p75 − 20k − 15k, well above 300k
    far = _prop(list_price=480_000, **base)
    assert any("doesn't seem too far" in compose_email(None, cfg, _agent(), [near], s)["body"] for s in range(8))
    assert all("doesn't seem too far" not in compose_email(None, cfg, _agent(), [far], s)["body"] for s in range(8))
    guessed = _prop(list_price=300_000, **dict(base, arv_method="estimate"))
    assert all("doesn't seem too far" not in compose_email(None, cfg, _agent(), [guessed], s)["body"] for s in range(8))


def test_validator_rejects_price_language(cfg):
    p = [_prop()]
    body = "Hi Maria, Charles here. Your listing on NW 11th St looks good. Would the seller take 10% below list? " + "word " * 30
    assert "price or offer language" in validate_email("Your listing on NW 11th St", body, p)
    assert "price or offer language" in validate_email("Cash offer: NW 11th St", "NW 11th St, any flexibility? " + "word " * 40, p)


def test_emails_vary(cfg):
    bodies = {compose_email(None, cfg, _agent(), [_prop()], seed)["body"] for seed in range(40)}
    assert len(bodies) >= 2                                   # within one angle
    profiles = [_prop(), _prop(remarks="", year_built=2015, days_on_market=5, price_cuts=0),
                _prop(remarks="", year_built=1990, days_on_market=95, price_cuts=0), _prop(remarks="Investor special", days_on_market=5, price_cuts=0)]
    assert len({compose_email(None, cfg, _agent(), [q], 1)["body"] for q in profiles}) == 4   # different houses, different emails


def test_multi_property_email_batches(cfg):
    d = compose_email(None, cfg, _agent(), [_prop(1), _prop(2, street="55 SW 8th St", city="Hialeah")], 1)
    assert "101 NW 11th St" in d["body"] and "55 SW 8th St" in d["body"] and d["variant"] == "multi"


def test_validate_email_rejects_bad_content():
    p = [_prop()]
    assert "html" in validate_email("s", "<b>Hi</b> " + "word " * 60, p)
    assert "link" in validate_email("s", "see https://x.com 101 NW 11th St cash? " + "word " * 60, p)
    assert "dollar figure" in validate_email("s", "we offer $300,000 for 101 NW 11th St cash? " + "word " * 60, p)


def test_sms_under_160_and_varied(cfg):
    bodies = set()
    for seed in range(20):
        d = compose_sms(cfg, _agent(), _prop(), seed)
        assert validate_sms(d["body"]) == [] and len(d["body"]) <= 160
        assert "Maria" in d["body"] and "Charles" in d["body"] and "Titan" in d["body"] and "NW 11th St" in d["body"]
        assert "flexib" in d["body"] and "$" not in d["body"] and "offer" not in d["body"].lower()
        bodies.add(d["body"])
    assert len(bodies) >= 3
    long_street = _prop(street="12345 Southwest Pembroke Lakes Boulevard Northwest Unit 1204")
    assert len(compose_sms(cfg, _agent(), long_street, 3)["body"]) <= 160


def _give_contacts(conn):
    conn.execute("UPDATE agents SET email=lower(replace(name,' ','.'))||'@example-brokerage.com', email_confidence='high', email_source='test' WHERE email IS NULL")


def test_plan_sms_first_prefers_mobile_and_falls_back_to_email(loaded, cfg, monkeypatch):
    _give_contacts(loaded)
    monkeypatch.setenv("TITAN_CHANNEL", "SMS_FIRST")
    todo = plan(loaded, cfg, "2026-10-22")
    assert todo["sms"], "HOT/WARM agents with mobiles should get a text"
    sms_agents = {i["agent"]["id"] for i in todo["sms"]}
    for b in todo["email"]:
        assert b["agent"]["id"] not in sms_agents          # email only when no usable phone
    office = loaded.execute("SELECT id FROM agents WHERE is_office_line=1").fetchall()
    assert all(i["agent"]["id"] not in {r["id"] for r in office} for i in todo["sms"])


def test_plan_email_first_and_both(loaded, cfg, monkeypatch):
    _give_contacts(loaded)
    monkeypatch.setenv("TITAN_CHANNEL", "EMAIL_FIRST")
    todo = plan(loaded, cfg, "2026-10-22")
    assert todo["email"] and not any(b["agent"]["id"] in {i["agent"]["id"] for i in todo["sms"]} for b in todo["email"])
    monkeypatch.setenv("TITAN_CHANNEL", "BOTH")
    both = plan(loaded, cfg, "2026-10-22")
    assert len(both["sms"]) >= len(todo["sms"]) and len(both["email"]) >= len(todo["email"])


def test_draft_mode_writes_local_drafts_and_respects_rules(loaded, cfg, monkeypatch, tmp_path):
    _give_contacts(loaded)
    monkeypatch.setenv("TITAN_CHANNEL", "BOTH")
    monkeypatch.setenv("TITAN_MODE", "DRAFT_FOR_APPROVAL")
    counts = run_outreach(loaded, cfg, llm=None, gmail=None, twilio=None, drafts_dir=tmp_path)
    assert counts["email_drafted"] > 0 and counts["sms_drafted"] > 0 and counts["email_sent"] == 0 and counts["sms_sent"] == 0
    files = list(tmp_path.glob("*.txt"))
    assert len(files) == counts["email_drafted"] + counts["sms_drafted"]
    for f in files:
        txt = f.read_text()
        assert not HTML_RE.search(txt) and not URL_RE.search(txt)
    # one email per agent, even with several listings
    rows = loaded.execute("SELECT agent_id, COUNT(*) c FROM outreach WHERE channel='email' GROUP BY agent_id").fetchall()
    assert all(r["c"] == 1 for r in rows)
    # second run the same day: nothing new (cooldown + property already contacted)
    again = run_outreach(loaded, cfg, llm=None, gmail=None, twilio=None, drafts_dir=tmp_path)
    assert again["email_drafted"] == 0 and again["sms_drafted"] == 0


def test_daily_cap_and_dnc(loaded, cfg, monkeypatch, tmp_path):
    _give_contacts(loaded)
    monkeypatch.setenv("TITAN_CHANNEL", "EMAIL_FIRST")
    cfg.raw["outreach"]["daily_email_cap"] = 1
    dnc_agent = loaded.execute("SELECT a.id, a.email FROM agents a JOIN properties p ON p.agent_id=a.id JOIN valuations v ON v.property_id=p.id WHERE v.rating='HOT' LIMIT 1").fetchone()
    db.add_dnc(loaded, dnc_agent["id"], None, dnc_agent["email"], "test")
    counts = run_outreach(loaded, cfg, llm=None, gmail=None, twilio=None, drafts_dir=tmp_path)
    assert counts["email_drafted"] == 1
    assert loaded.execute("SELECT COUNT(*) FROM outreach WHERE agent_id=?", (dnc_agent["id"],)).fetchone()[0] == 0


def test_auto_mode_sends_via_clients_and_respects_window(loaded, cfg, monkeypatch, tmp_path):
    _give_contacts(loaded)
    monkeypatch.setenv("TITAN_CHANNEL", "BOTH")
    monkeypatch.setenv("TITAN_MODE", "AUTO_SEND_FIRST_TOUCH")
    sent_mail, sent_sms = [], []

    class G:
        def send(self, to, subject, body, **kw):
            sent_mail.append((to, subject, body)); return {"message_id": f"m{len(sent_mail)}", "thread_id": f"t{len(sent_mail)}"}
        def create_draft(self, *a, **k): raise AssertionError("should send, not draft")

    class T:
        def send(self, to, body):
            sent_sms.append((to, body)); return {"sid": f"SM{len(sent_sms)}", "status": "queued"}

    sunday = datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)
    counts = run_outreach(loaded, cfg, llm=None, gmail=G(), twilio=T(), now=sunday, drafts_dir=tmp_path)
    assert counts["email_sent"] == len(sent_mail) > 0
    assert counts["sms_sent"] == 0 and counts["sms_skipped_window"] > 0 and not sent_sms
    assert loaded.execute("SELECT COUNT(*) FROM outreach WHERE channel='sms' AND status='queued'").fetchone()[0] == counts["sms_skipped_window"]
    assert loaded.execute("SELECT COUNT(*) FROM threads WHERE channel='email'").fetchone()[0] == counts["email_sent"]
    # flush inside the window sends the queued texts
    from titan.outreach.send import flush_queued_sms
    tuesday = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)   # 11am ET
    n = flush_queued_sms(loaded, cfg, T(), now=tuesday)
    assert n == counts["sms_skipped_window"] == len(sent_sms)
    assert loaded.execute("SELECT COUNT(*) FROM threads WHERE channel='sms'").fetchone()[0] == n
