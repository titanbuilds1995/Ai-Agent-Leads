"""Rodrigo, 2026-11-11: "pause everything right now, no more email sending out"."""
from datetime import datetime, timezone

from titan.approvals import in_window, sending_paused

WED_NOON_ET = datetime(2026, 11, 11, 17, 0, tzinfo=timezone.utc)


def test_pause_closes_the_send_window(cfg):
    assert in_window(cfg, WED_NOON_ET)
    cfg["outreach"]["sending_paused"] = True
    assert sending_paused(cfg)
    assert not in_window(cfg, WED_NOON_ET)                      # every sender waits, as it does outside the window
    assert in_window(cfg, WED_NOON_ET, even_if_paused=True)     # only the one-time delay note asks this way



def _board_with_an_answer(conn, cfg, tmp_path, text="Here is some info, give me a call."):
    from titan.commercial.outreach import send_om_requests
    from titan.util import iso
    from tests.test_approvals import SAT_10AM_ET
    from tests.test_commercial import FakeOMGmail, _answer, _om_board
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    gm = FakeOMGmail()
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    conn.execute("UPDATE commercial_threads SET sent_at=?, broker_name='Adam Smith'", (iso(SAT_10AM_ET),))
    _answer(conn, cfg, asana, gm, text, "r1", 1)
    cfg["commercial"]["delay_note"]["before"] = "9999"
    return asana, secs, gid, gm


def test_paused_nothing_goes_but_one_delay_note_to_a_broker_who_never_heard_back(conn, cfg, tmp_path):
    from titan.commercial.books_review import commits_us, send_approved_replies
    from titan.commercial.closeout import DECLINE_RE, clear_not_fit
    from titan.commercial.delay_note import note_body, send_delay_notes
    from titan.commercial.outreach import send_om_requests
    from tests.test_approvals import SAT_10AM_ET
    asana, secs, gid, gm = _board_with_an_answer(conn, cfg, tmp_path)
    cfg["outreach"]["sending_paused"] = True
    n = len(gm.sent)
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    send_approved_replies(conn, cfg, asana, gm, now=SAT_10AM_ET)
    clear_not_fit(conn, cfg, asana, gm, now=SAT_10AM_ET)
    assert len(gm.sent) == n                                         # paused: no request, no reply, no pass
    out = send_delay_notes(conn, cfg, asana, gm, now=SAT_10AM_ET)
    assert out["sent"] == 1 and len(gm.sent) == n + 1
    to, subject, body = gm.sent[-1]
    assert subject.lower().startswith("re:") and gm.kw[-1].get("thread_id") == "t1"
    assert body.startswith("Hey Adam,") and "sorry for the slow reply" in body and "we will get back with you" in body
    assert not commits_us(body) and not DECLINE_RE.search(body)      # no date, no promise, not a pass
    assert any("Sorry-for-the-delay note sent" in c for c in asana.stories[gid])
    assert send_delay_notes(conn, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 0 and len(gm.sent) == n + 1   # once per broker
    assert not commits_us(note_body("there", "your listings", cfg))


def test_no_delay_note_where_rodrigo_or_charles_already_wrote_or_it_is_not_a_fit(conn, cfg, tmp_path):
    from titan.commercial.delay_note import send_delay_notes
    from tests.test_approvals import SAT_10AM_ET
    # 1) Rodrigo wrote in the thread
    asana, secs, gid, gm = _board_with_an_answer(conn, cfg, tmp_path)
    n = len(gm.sent)
    conn.execute("UPDATE commercial_threads SET rodrigo_at='2026-11-10T00:00:00Z'")
    assert send_delay_notes(conn, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 0
    # 2) someone on our side answered by hand in Gmail (the database does not know)
    conn.execute("UPDATE commercial_threads SET rodrigo_at=NULL")
    gm.threads["t1"].append({"id": "h1", "from": "Charles@titanrealtyinvestments.com", "body": "Thanks", "internal_ts": 1_900_000_000, "label_ids": [], "attachments": []})
    out = send_delay_notes(conn, cfg, asana, gm, now=SAT_10AM_ET)
    assert out["sent"] == 0 and len(gm.sent) == n
    # 3) his card (Offer out), 4) read not a fit, 5) answered after the pause, 6) switched off
    for setup in ("offer", "notfit", "late", "off"):
        conn.execute("DELETE FROM commercial_kv WHERE key LIKE 'delay_note:%'")
        gm.threads["t1"] = [m for m in gm.threads["t1"] if m["id"] != "h1"]
        if setup == "offer":
            asana.move(gid, secs["Offer out"])
        elif setup == "notfit":
            asana.move(gid, secs["In touch with the agent"])
            conn.execute("INSERT INTO commercial_reviews (thread_id, listing_id, verdict, text, created_at) SELECT id, listing_id, 'DOES NOT FIT', 'x', 'now' FROM commercial_threads")
        elif setup == "late":
            conn.execute("DELETE FROM commercial_reviews")
            cfg["commercial"]["delay_note"]["before"] = "2000-01-01T00:00:00Z"
        else:
            cfg["commercial"]["delay_note"]["before"] = "9999"
            cfg["commercial"]["delay_note"]["enabled"] = False
        assert send_delay_notes(conn, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 0, setup
    assert len(gm.sent) == n
    cfg["commercial"]["delay_note"]["enabled"] = True
    assert send_delay_notes(conn, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 1     # the same broker, nothing in the way: it goes
