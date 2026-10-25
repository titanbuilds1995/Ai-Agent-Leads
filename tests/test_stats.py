"""Scoreboard: reply / answer rates, daily series and the conversation board (Rodrigo, 2026-10-25)."""
from titan import db
from titan.approvals import parse_card, send_approved
from titan.outreach.send import log_message
from titan.replies.inbox import handle_inbound
from titan.stats import compute_stats
from tests.test_approvals import SAT_10AM_ET, FakeGmail, _cards, _sections


class _G(FakeGmail):
    def create_draft(self, to, subject, body, **kw): return {"draft_id": "d", "thread_id": kw.get("thread_id")}


def _two_sent(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    gids = list(asana.tasks)[:2]
    for g in gids:
        asana.move(g, _sections(cfg)["approved"])
    assert send_approved(loaded, cfg, asana, _G(), now=SAT_10AM_ET)["sent"] == 2
    return asana, [parse_card(asana.tasks[g]["notes"])["outreach_id"] for g in gids]


def test_rates_board_and_daily(loaded, cfg, tmp_path, monkeypatch):
    asana, oids = _two_sent(loaded, cfg, tmp_path, monkeypatch)
    s0 = compute_stats(loaded, cfg, asana=asana)
    t = s0["totals"]
    assert t["emails_sent"] == 2 and t["agents_emailed"] == 2 and t["agents_replied"] == 0 and t["reply_rate_pct"] == 0.0
    assert t["no_reply_yet"] == 2 and t["awaiting_our_answer"] == 0 and s0["asana"]["sent"] == 2
    # one agent answers with a condition rundown → engine drafts an answer (draft mode) → ball is back on the agent's side
    a1 = loaded.execute("SELECT agent_id FROM outreach WHERE id=?", (oids[0],)).fetchone()[0]
    thread = db.row_to_dict(loaded.execute("SELECT * FROM threads WHERE agent_id=? AND channel='email'", (a1,)).fetchone())
    handle_inbound(loaded, cfg, thread, "Roof is 2015, kitchen original, seller inherited it.", "gm-1", "email", gmail=_G())
    s1 = compute_stats(loaded, cfg, asana=asana)
    t = s1["totals"]
    assert t["agents_replied"] == 1 and t["reply_rate_pct"] == 50.0 and t["conversations"] == 1
    assert t["awaiting_our_answer"] == 1 and t["answered"] == 0 and t["answer_rate_pct"] == 0.0     # a draft is not an answer
    assert s1["board"]["needs_answer"][0]["agent"]["id"] == a1 and "Roof is 2015" in s1["board"]["needs_answer"][0]["last_snippet"]
    # Charles's answer goes out → answered
    log_message(loaded, thread["id"], "out", "Thanks, where does the seller need to be?", external_id="gm-out-1", action="reply:manual")
    t = compute_stats(loaded, cfg, asana=asana)["totals"]
    assert t["answered"] == 1 and t["answer_rate_pct"] == 100.0 and t["awaiting_our_answer"] == 0 and t["waiting_on_agent"] == 1
    # a bounce is never a reply
    a2 = loaded.execute("SELECT agent_id FROM outreach WHERE id=?", (oids[1],)).fetchone()[0]
    th2 = db.row_to_dict(loaded.execute("SELECT * FROM threads WHERE agent_id=? AND channel='email'", (a2,)).fetchone())
    handle_inbound(loaded, cfg, th2, "Delivery Status Notification (Failure): Recipient inbox full", "gm-b", "email", gmail=_G())
    s3 = compute_stats(loaded, cfg, asana=asana)
    assert s3["totals"]["agents_replied"] == 1 and s3["totals"]["bounces"] == 1 and s3["totals"]["closed"] == 1
    d = s3["daily"]
    assert len(d) == 14 and d[-1]["date"] > d[0]["date"] and sum(x["new_properties"] for x in d) >= 1 and sum(x["replies"] for x in d) == 1
    assert s3["totals"]["median_hours_to_first_reply"] is not None


def test_stats_never_raise(cfg):
    class Broken:
        def execute(self, *a, **k): raise RuntimeError("db gone")
    s = compute_stats(Broken(), cfg, with_asana=False)
    assert "error" in s and s["totals"] == {} and s["daily"] == []


def test_snapshot_carries_stats(loaded, cfg):
    from titan.dashboard_sync import build_snapshot
    snap = build_snapshot(loaded, cfg, include_asana=False)
    assert "stats" in snap and "totals" in snap["stats"] and snap["stats"]["asana"] is None


def test_offer_requests_land_in_needs_rodrigo(loaded, cfg, tmp_path, monkeypatch):
    asana, oids = _two_sent(loaded, cfg, tmp_path, monkeypatch)
    a1 = loaded.execute("SELECT agent_id FROM outreach WHERE id=?", (oids[0],)).fetchone()[0]
    thread = db.row_to_dict(loaded.execute("SELECT * FROM threads WHERE agent_id=? AND channel='email'", (a1,)).fetchone())
    res = handle_inbound(loaded, cfg, thread, "Just send me your best offer.", "gm-offer", "email", gmail=_G())
    assert res["state"] == "ESCALATED"
    s = compute_stats(loaded, cfg, with_asana=False)
    assert s["totals"]["needs_rodrigo"] == 1 and s["board"]["needs_rodrigo"][0]["agent"]["id"] == a1 and s["totals"]["awaiting_our_answer"] == 0
