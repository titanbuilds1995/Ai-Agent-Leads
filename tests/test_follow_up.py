"""Rodrigo, 2026-11-02: a "Good morning" second email to every agent who never answered the first one."""
from datetime import datetime, timedelta, timezone

from titan.approvals import parse_card, send_approved
from titan.follow_up import candidates, post_follow_ups, validate_follow_up, write_follow_up
from titan.outreach.compose import signature
from titan.outreach.send import log_message
from titan.util import iso
from tests.test_approvals import SAT_10AM_ET, FakeGmail, _cards, _sections


def _sent(loaded, cfg, tmp_path, monkeypatch, days_ago=5):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s = _sections(cfg)
    for gid in list(asana.tasks):
        asana.move(gid, s["approved"])
    cfg.raw["outreach"]["asana"]["per_tick"] = 50
    cfg.raw["outreach"]["daily_email_cap"] = 50
    send_approved(loaded, cfg, asana, FakeGmail(), now=SAT_10AM_ET)
    old = iso(datetime.now(timezone.utc) - timedelta(days=days_ago))
    loaded.execute("UPDATE outreach SET sent_at=? WHERE status='sent'", (old,))
    return asana, s


def test_only_unanswered_first_emails_get_a_follow_up_and_only_once(loaded, cfg, tmp_path, monkeypatch):
    asana, s = _sent(loaded, cfg, tmp_path, monkeypatch)
    rows = loaded.execute("SELECT id, agent_id FROM outreach WHERE status='sent'").fetchall()
    assert len(rows) >= 2
    answered, bounced = rows[0], rows[1]
    tid = loaded.execute("SELECT id FROM threads WHERE agent_id=?", (answered["agent_id"],)).fetchone()[0]
    log_message(loaded, tid, "in", "Roof is 2015, seller is flexible.", external_id="in-1", classification="INTERESTED")
    loaded.execute("UPDATE agents SET email_confidence='bounced' WHERE id=?", (bounced["agent_id"],))
    cands = candidates(loaded, cfg, asana)
    ids = {c["outreach"]["id"] for c in cands}
    assert answered["id"] not in ids and bounced["id"] not in ids and len(ids) == len(rows) - 2
    out = post_follow_ups(loaded, cfg, asana, None)
    assert out["posted"] == len(ids)
    cards = [t for t in asana.tasks.values() if parse_card(t["notes"]) and parse_card(t["notes"])["reply_to"]]
    assert len(cards) == len(ids) and all(asana.where[t["gid"]] == asana.ensure_section(None, "Needs Me") for t in cards)
    body = parse_card(cards[0]["notes"])["body"]
    assert body.startswith("Good morning ") and "\n\n" in body and "Titan Realty Investments" in body
    assert "FOLLOW-UP" in cards[0]["notes"] and "Drag to Approved" in cards[0]["notes"]
    assert post_follow_ups(loaded, cfg, asana, None)["posted"] == 0          # never twice
    # approved → sent in the same thread, and the follow-up counts as an outbound so a third email isn't drafted
    asana.move(cards[0]["gid"], s["approved"])
    gm = FakeGmail()
    assert send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 1
    assert gm.sent[0][1].startswith("Re: ") and gm.sent[0][2] == body
    loaded.execute("DELETE FROM follow_ups")
    assert all(c["outreach"]["id"] != parse_card(cards[0]["notes"])["outreach_id"] for c in candidates(loaded, cfg, asana))


def test_too_fresh_waits_and_parked_leads_are_skipped(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _sent(loaded, cfg, tmp_path, monkeypatch, days_ago=1)
    assert candidates(loaded, cfg, asana) == []                       # after_days is 3
    assert candidates(loaded, cfg, asana, after_days=0)
    aid = loaded.execute("SELECT agent_id FROM outreach WHERE status='sent' LIMIT 1").fetchone()[0]
    loaded.execute("INSERT INTO lead_notes(agent_id, text, created_at) VALUES(?,?,?)", (aid, "too fresh, wait 45 days before contacting them", iso()))
    assert aid not in {c["agent"]["id"] for c in candidates(loaded, cfg, asana, after_days=0)}


def test_approve_flag_puts_cards_in_approved(loaded, cfg, tmp_path, monkeypatch):
    asana, s = _sent(loaded, cfg, tmp_path, monkeypatch)
    out = post_follow_ups(loaded, cfg, asana, None, approve=True, limit=2)
    assert out["posted"] == 2 and all(asana.where[c["card"]] == s["approved"] for c in out["cards"])


def test_validator_and_claude_path(cfg):
    props = [{"street": "671 SW 10th St", "city": "Florida City", "photos_count": 19}]
    sig = signature(cfg)
    good = "Good morning Veronica,\n\nCharles with Titan Realty Investments. I emailed you last week about SW 10th St and wanted to check back.\n\nIs the seller still open to a conversation on it?\n\n" + sig
    assert validate_follow_up(good, props) == []
    assert validate_follow_up("Hey Veronica,\n\nJust following up on SW 10th St. We could do $200k.\n\n" + sig, props)
    assert validate_follow_up(good.replace("open to a conversation on it", "flexible, and how much work does it need"), props)

    class LLM:
        available = True
        def structured(self, *a, **k): return {"body": good}
    body, source = write_follow_up(LLM(), cfg, {"name": "Veronica Zavaleta"}, props, "first email")
    assert source == "claude" and body == good

    class Bad(LLM):
        def structured(self, *a, **k): return {"body": "Hey,\n\nJust bumping this. $300k?\n\n" + sig}
    body, source = write_follow_up(Bad(), cfg, {"name": "Veronica Zavaleta"}, props, "first email", seed=1)
    assert source == "template" and body.startswith("Good morning Veronica,") and validate_follow_up(body, props) == []
