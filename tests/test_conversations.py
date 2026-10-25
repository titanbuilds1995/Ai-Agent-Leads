"""Conversations mirror: every inbound reply lands on the agent's Asana card with the engine's read and draft answer."""
from titan import db
from titan.approvals import parse_card, send_approved
from titan.conversations import NO_CARD, PREFIX, mirror_replies
from titan.outreach.send import ensure_thread, log_message
from titan.replies.inbox import handle_inbound
from tests.test_approvals import SAT_10AM_ET, FakeAsana, FakeGmail, _cards, _sections


class _Gmail(FakeGmail):
    def create_draft(self, to, subject, body, **kw): return {"draft_id": "d1", "thread_id": kw.get("thread_id")}


def _sent_card(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    gid = next(iter(asana.tasks))
    asana.move(gid, _sections(cfg)["approved"])
    assert send_approved(loaded, cfg, asana, _Gmail(), now=SAT_10AM_ET)["sent"] == 1
    oid = parse_card(asana.tasks[gid]["notes"])["outreach_id"]
    o = loaded.execute("SELECT agent_id, property_ids, gmail_thread_id FROM outreach WHERE id=?", (oid,)).fetchone()
    return asana, gid, o


def test_reply_is_posted_once_with_read_and_draft(loaded, cfg, tmp_path, monkeypatch):
    asana, gid, o = _sent_card(loaded, cfg, tmp_path, monkeypatch)
    thread = db.row_to_dict(loaded.execute("SELECT * FROM threads WHERE agent_id=? AND channel='email'", (o["agent_id"],)).fetchone())
    before = len(asana.stories[gid])
    res = handle_inbound(loaded, cfg, thread, "Roof is from 2015, the rest is original. Seller is open to offers.", "gm-reply-1", "email", gmail=_Gmail())
    assert res["classification"] != "BOUNCE"
    n = mirror_replies(loaded, cfg, asana)
    assert n == {"posted": 1, "no_card": 0, "failed": 0}
    new = asana.stories[gid][before:]
    assert len(new) == 1 and PREFIX in new[0] and "Roof is from 2015" in new[0]
    assert "Engine read: " in new[0] and "Conversation state:" in new[0] and "House: " in new[0]
    if res.get("reply"):
        assert "NOT sent" in new[0] and res["reply"][:40] in new[0]
    # idempotent: the second tick posts nothing new
    assert mirror_replies(loaded, cfg, asana) == {"posted": 0, "no_card": 0, "failed": 0}
    assert len(asana.stories[gid]) == before + 1


def test_reply_without_a_card_is_skipped_and_a_dead_card_is_retried(loaded, cfg, tmp_path, monkeypatch):
    asana, gid, o = _sent_card(loaded, cfg, tmp_path, monkeypatch)
    # a thread for an agent who never had a card
    a = loaded.execute("SELECT id FROM agents WHERE id<>? LIMIT 1", (o["agent_id"],)).fetchone()
    pid = loaded.execute("SELECT id FROM properties LIMIT 1").fetchone()[0]
    tid = ensure_thread(loaded, a["id"], "sms", "+13055550199", [pid])
    log_message(loaded, tid, "in", "Who is this?", external_id="sms-1", classification="QUESTION", confidence=0.5)
    assert mirror_replies(loaded, cfg, asana) == {"posted": 0, "no_card": 1, "failed": 0}
    assert loaded.execute("SELECT mirrored_at FROM messages WHERE external_id='sms-1'").fetchone()[0] == NO_CARD
    # Asana failing leaves the message pending for the next tick
    thread = db.row_to_dict(loaded.execute("SELECT * FROM threads WHERE agent_id=? AND channel='email'", (o["agent_id"],)).fetchone())
    log_message(loaded, thread["id"], "in", "Call me.", external_id="gm-2", classification="QUESTION", confidence=0.5)

    class Broken(FakeAsana):
        def comment(self, task, text): raise RuntimeError("HTTP 404")
    b = Broken(); b.__dict__.update(asana.__dict__)
    assert mirror_replies(loaded, cfg, b) == {"posted": 0, "no_card": 0, "failed": 1}
    assert loaded.execute("SELECT mirrored_at FROM messages WHERE external_id='gm-2'").fetchone()[0] is None
    assert mirror_replies(loaded, cfg, asana)["posted"] == 1


def test_old_database_gets_the_column(tmp_path):
    import sqlite3
    c = db.connect(tmp_path / "old.db")
    c.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL, direction TEXT NOT NULL, body TEXT, external_id TEXT UNIQUE, "
              "classification TEXT, confidence REAL, action TEXT, created_at TEXT NOT NULL)")
    db.init_db(c)
    assert "mirrored_at" in [r[1] for r in c.execute("PRAGMA table_info(messages)").fetchall()]
