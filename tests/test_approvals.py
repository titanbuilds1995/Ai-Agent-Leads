"""Asana email approvals (Rodrigo, 2026-10-24): cards → feedback rewrites → approved emails sent in the window only."""
import itertools
from datetime import datetime, timezone

from titan import db
from titan.approvals import BOT, card_notes, in_window, parse_card, send_approved, sync_feedback
from titan.outreach.send import run_outreach

SAT_10AM_ET = datetime(2026, 10, 24, 14, 0, tzinfo=timezone.utc)
SAT_7PM_ET = datetime(2026, 10, 24, 23, 0, tzinfo=timezone.utc)


class FakeAsana:
    def __init__(self):
        self.tasks, self.where, self.stories, self.attachments, self._ids = {}, {}, {}, [], itertools.count(1)

    def create_task(self, project, section, name, notes):
        gid = str(next(self._ids))
        self.tasks[gid] = {"gid": gid, "name": name, "notes": notes, "created_at": f"2026-10-24T0{gid}:00:00Z"}
        self.where[gid], self.stories[gid] = section, []
        return {"gid": gid}

    def update_notes(self, task, notes): self.tasks[task]["notes"] = notes
    def attach(self, task, path, name=None): self.attachments.append((task, name, path.exists()))
    def section_tasks(self, section): return [dict(t) for g, t in self.tasks.items() if self.where[g] == section]
    def comments(self, task): return [{"text": t} for t in self.stories[task]]
    def comment(self, task, text): self.stories[task].append(f"{BOT} {text}")
    def move(self, task, section): self.where[task] = section


class FakeGmail:
    def __init__(self): self.sent = []
    def send(self, to, subject, body, **kw):
        self.sent.append((to, subject, body)); return {"message_id": f"m{len(self.sent)}", "thread_id": f"t{len(self.sent)}"}
    def create_draft(self, *a, **k): raise AssertionError("approval mode must not make Gmail drafts")


def _sections(cfg):
    return cfg["outreach"]["asana"]


def _cards(loaded, cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("TITAN_MODE", "DRAFT_FOR_APPROVAL")
    cfg.root = tmp_path
    loaded.execute("UPDATE agents SET email=lower(replace(name,' ','.'))||'@example.com', email_confidence='high' WHERE email IS NULL")
    asana = FakeAsana()
    out = run_outreach(loaded, cfg, None, FakeGmail(), None, asana=asana)
    return asana, out


def test_card_round_trip():
    n = card_notes("a@b.com", "Your listing on NW 11th St", "Hey Maria,\n\nBody?\n\nCharles", 7, "Listing: ...")
    assert parse_card(n) == {"to": "a@b.com", "subject": "Your listing on NW 11th St", "body": "Hey Maria,\n\nBody?\n\nCharles", "outreach_id": 7}


def test_outreach_posts_cards_with_a_picture_and_sends_nothing(loaded, cfg, tmp_path, monkeypatch):
    asana, out = _cards(loaded, cfg, tmp_path, monkeypatch)
    assert out["email_cards"] >= 1 and out["email_sent"] == 0
    s = _sections(cfg)
    assert all(asana.where[g] == s["to_review"] for g in asana.tasks)
    assert len(asana.attachments) == len(asana.tasks) and all(ok for _, _, ok in asana.attachments)
    assert loaded.execute("SELECT COUNT(*) FROM outreach WHERE status='pending_approval' AND asana_task_id IS NOT NULL").fetchone()[0] == len(asana.tasks)
    # next day's run must not card the same properties again
    again = run_outreach(loaded, cfg, None, FakeGmail(), None, asana=asana)
    assert again.get("email_cards", 0) == 0


def test_only_approved_cards_are_sent_inside_the_window(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s, gm = _sections(cfg), FakeGmail()
    assert send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 0 and not gm.sent      # nothing approved yet
    gid = next(iter(asana.tasks))
    asana.move(gid, s["approved"])
    assert send_approved(loaded, cfg, asana, gm, now=SAT_7PM_ET)["sent"] == 0                       # outside 8:30-5:30
    res = send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)
    assert res["sent"] == 1 and len(gm.sent) == 1 and asana.where[gid] == s["sent"]
    assert any("Sent " in c for c in asana.stories[gid])
    oid = parse_card(asana.tasks[gid]["notes"])["outreach_id"]
    assert loaded.execute("SELECT status FROM outreach WHERE id=?", (oid,)).fetchone()[0] == "sent"
    assert loaded.execute("SELECT COUNT(*) FROM threads WHERE channel='email'").fetchone()[0] >= 1          # replies get tracked


def test_edits_on_the_card_are_what_gets_sent(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s, gm = _sections(cfg), FakeGmail()
    gid = next(iter(asana.tasks))
    asana.tasks[gid]["notes"] = asana.tasks[gid]["notes"].replace("Hope you're doing well.", "Happy Saturday!")
    asana.move(gid, s["approved"])
    send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)
    assert gm.sent and ("Happy Saturday!" in gm.sent[0][2] or "Hope you're doing well." not in gm.sent[0][2])


def test_feedback_comment_rewrites_the_card(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    gid = next(iter(asana.tasks))
    card = parse_card(asana.tasks[gid]["notes"])
    asana.stories[gid].append("make it shorter and mention the big lot")

    class L:
        available = True
        def structured(self, purpose, system, user, schema, max_tokens=1024):
            import json
            u = json.loads(user)
            assert u["feedback"] == ["make it shorter and mention the big lot"]
            street = card["body"].split("listing on ")[-1].split(",")[0] if "listing on " in card["body"] else ""
            return {"subject": card["subject"], "body": card["body"].replace("Hope you're doing well. ", "")}
    res = sync_feedback(loaded, cfg, asana, L())
    assert res["revised"] == 1 and any("Revised" in c for c in asana.stories[gid])
    assert sync_feedback(loaded, cfg, asana, L())["revised"] == 0        # same comment is not processed twice


def test_window_and_skip(cfg, loaded, tmp_path, monkeypatch):
    assert in_window(cfg, SAT_10AM_ET) and not in_window(cfg, SAT_7PM_ET)
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    gid = next(iter(asana.tasks))
    asana.move(gid, _sections(cfg)["skip"])
    assert sync_feedback(loaded, cfg, asana, None)["skipped"] == 1
