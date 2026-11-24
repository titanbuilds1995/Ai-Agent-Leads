"""Rodrigo, 2026-11-24 (Alberto Pascual): his comment on a card holding the $540,000 offer sent the offer to the no-price
reply rewriter, which replaced it with a holding email; and the contact guard held it although Alberto had answered."""
from titan.answers import pending, set_answer
from titan.approvals import parse_card, send_approved, sync_feedback, tells_to_send
from titan.contact_guard import ContactHold
from titan.outreach.send import log_message
from tests.test_approvals import SAT_10AM_ET, FakeGmail, _sections
from tests.test_follow_up import _sent

OFFER = ("Hey Alberto,\n\nThanks for getting back to me.\n\nOur offer:\n• $540,000, cash\n• As is\n• 15 day inspection period\n"
         "• $5,000 escrow deposit\n• Close in 30 days\n\nBest regards,\n\nCharles Grant")
ASK = ("What email did we send them? If we sent them something that it is not the actual offer email, let's send them an email "
       "again this time with the offer email.")


class LLM:          # a writer that would wreck the offer if it were ever asked
    available = True
    def structured(self, *a, **k): raise AssertionError("an offer draft must never reach a rewriter")


def _lead(loaded, cfg, tmp_path, monkeypatch):
    asana, s = _sent(loaded, cfg, tmp_path, monkeypatch)
    gid = next(g for g, t in asana.tasks.items() if parse_card(t["notes"]))
    oid = parse_card(asana.tasks[gid]["notes"])["outreach_id"]
    set_answer(loaded, cfg, asana, oid, OFFER, picture=False)
    return asana, s, gid, oid


def test_send_words():
    assert tells_to_send(ASK) and tells_to_send("send it") and not tells_to_send("did we send it?") and not tells_to_send("don't send it yet")


def test_comment_never_rewrites_an_offer_and_send_approves_it(loaded, cfg, tmp_path, monkeypatch):
    asana, s, gid, oid = _lead(loaded, cfg, tmp_path, monkeypatch)
    asana.comment_raw(gid, "make it friendlier")
    sync_feedback(loaded, cfg, asana, LLM())
    assert pending(loaded, oid)["body"] == OFFER and "unchanged" in asana.stories[gid][-1]
    asana.comment_raw(gid, "Let's send them the offer email.")
    sync_feedback(loaded, cfg, asana, LLM())
    assert pending(loaded, oid)["body"] == OFFER and asana.where[gid] == _sections(cfg)["approved"]


def test_guard_does_not_hold_when_our_records_show_the_answer(loaded, cfg, tmp_path, monkeypatch):
    asana, s, gid, oid = _lead(loaded, cfg, tmp_path, monkeypatch)
    aid = loaded.execute("SELECT agent_id FROM outreach WHERE id=?", (oid,)).fetchone()[0]

    class Guarded(FakeGmail):
        def send(self, to, subject, body, **kw):
            if kw.get("thread_id") and not kw.get("confirmed"):
                raise ContactHold("We already emailed them 1 time in the last 24 hours and they have not answered since. Another email now is bombarding them.")
            return super().send(to, subject, body, **kw)
    asana.move(gid, s["approved"])
    gm = Guarded()
    assert send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 0                # no answer on file: held
    tid = loaded.execute("SELECT id FROM threads WHERE agent_id=?", (aid,)).fetchone()[0]
    log_message(loaded, tid, "in", "How serious will your offer be? Try it.", external_id="in-a", classification="ASK_FOR_OFFER")
    loaded.execute("UPDATE messages SET created_at=datetime('now') WHERE external_id='in-a'")
    asana.move(gid, s["approved"])
    assert send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 1 and "$540,000" in gm.sent[-1][2]
