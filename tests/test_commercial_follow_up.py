"""One follow-up to every 1031 broker who never answered the first email (Rodrigo, 2026-11-17)."""
from datetime import datetime, timedelta, timezone

from tests.test_approvals import SAT_10AM_ET
from tests.test_commercial import FakeOMGmail, _om_board
from titan.commercial.follow_up import candidates, follow_up_body, send_follow_ups
from titan.commercial.om_request import validate_om_request
from titan.commercial.outreach import send_om_requests


def test_follow_up_wording_passes_the_house_rules(cfg):
    for shows_cap, several in ((True, False), (False, False), (False, True)):
        body = follow_up_body("Adam", "840 SW 81st Ave in Miami", shows_cap, several, cfg)
        assert validate_om_request("Re: 840 SW 81st Ave, Miami on LoopNet", body) == []
        assert body.startswith("Hey Adam,\n\n") and "1031" in body and "Charles Grant" in body


def _first_email_out(conn, cfg, tmp_path):
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    gm = FakeOMGmail()
    assert send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 1
    cfg.raw["commercial"]["follow_up"] = {"enabled": True, "per_round": 3, "min_days": 3}
    return asana, secs, gid, gm


def test_follow_up_waits_min_days_then_goes_once_in_the_same_thread(conn, cfg, tmp_path):
    asana, secs, gid, gm = _first_email_out(conn, cfg, tmp_path)
    # the day after the first email: too recent
    out = send_follow_ups(conn, cfg, asana, gm, now=SAT_10AM_ET + timedelta(days=1))
    assert out["sent"] == 0 and out["skipped"]["too_recent"] == 1 and len(gm.sent) == 1
    # six days later, inside the window: one follow-up, as a reply in the first email's thread, card stays put, remembered
    later = SAT_10AM_ET + timedelta(days=6)
    out = send_follow_ups(conn, cfg, asana, gm, now=later)
    assert out["sent"] == 1 and len(gm.sent) == 2
    to, subject, body = gm.sent[1]
    assert to == "adam@trustrealtyusa.com" and subject.startswith("Re: ") and gm.kw[1]["thread_id"] == "t1"
    assert "following up on my note" in body and validate_om_request(subject, body) == []
    assert asana.where[gid] == secs["Sent · no answer yet"] and any("Follow-up sent" in c for c in asana.stories[gid])
    assert conn.execute("SELECT COUNT(*) FROM outreach WHERE kind='om_follow_up' AND status='sent'").fetchone()[0] == 1
    assert conn.execute("SELECT value FROM commercial_kv WHERE key='follow_up:adam@trustrealtyusa.com'").fetchone()
    # never twice
    out = send_follow_ups(conn, cfg, asana, gm, now=later + timedelta(days=1))
    assert out["sent"] == 0 and out["skipped"]["already_sent"] == 1 and len(gm.sent) == 2


def test_no_follow_up_when_the_broker_answered_or_rodrigo_is_talking(conn, cfg, tmp_path):
    asana, secs, gid, gm = _first_email_out(conn, cfg, tmp_path)
    later = SAT_10AM_ET + timedelta(days=6)
    # the broker answered in Gmail but the database has not seen it yet: Gmail is checked before sending
    gm.threads["t1"].append({"id": "m-b", "from": "Adam <adam@trustrealtyusa.com>", "body": "Here you go", "internal_ts": 1_700_000_500, "label_ids": [], "attachments": []})
    out = send_follow_ups(conn, cfg, asana, gm, now=later)
    assert out["sent"] == 0 and out["skipped"]["answered"] == 1 and len(gm.sent) == 1
    conn.execute("DELETE FROM commercial_kv")
    gm.threads["t1"].pop()
    # Rodrigo wrote in the thread: his conversation
    conn.execute("UPDATE commercial_threads SET rodrigo_at='2026-11-10T00:00:00Z'")
    out = send_follow_ups(conn, cfg, asana, gm, now=later)
    assert out["sent"] == 0 and out["skipped"]["rodrigo_is_talking"] == 1
    conn.execute("UPDATE commercial_threads SET rodrigo_at=NULL")
    # the card sits in one of his columns
    asana.move(gid, secs["Offer out"])
    out = send_follow_ups(conn, cfg, asana, gm, now=later)
    assert out["sent"] == 0 and out["skipped"]["rodrigo_is_talking"] == 1 and len(gm.sent) == 1
    # a bounce is not a candidate; disabled sends nothing
    asana.move(gid, secs["Sent · no answer yet"])
    conn.execute("DELETE FROM commercial_kv")
    conn.execute("UPDATE commercial_threads SET bounced=1")
    assert candidates(conn, cfg, later)[1]["bounced_or_closed"] == 1
    conn.execute("UPDATE commercial_threads SET bounced=0")
    cfg.raw["commercial"]["follow_up"]["enabled"] = False
    assert send_follow_ups(conn, cfg, asana, gm, now=later)["sent"] == 0 and len(gm.sent) == 1


def test_follow_ups_respect_the_window_and_the_shared_daily_cap(conn, cfg, tmp_path):
    asana, secs, gid, gm = _first_email_out(conn, cfg, tmp_path)
    night = datetime(2026, 11, 15, 3, 0, tzinfo=timezone.utc)
    assert send_follow_ups(conn, cfg, asana, gm, now=night)["reason"] == "outside the send window"
    cfg.raw["commercial"]["outreach"]["daily_cap"] = 1
    later = SAT_10AM_ET + timedelta(days=6)
    conn.execute("UPDATE outreach SET sent_at=?, run_date=?", (later.strftime("%Y-%m-%dT%H:%M:%SZ"), later.strftime("%Y-%m-%d")))
    assert send_follow_ups(conn, cfg, asana, gm, now=later)["reason"] == "daily cap reached" and len(gm.sent) == 1
