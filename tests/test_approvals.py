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
    def comment_raw(self, task, text): self.stories[task].append(text)
    def move(self, task, section): self.where[task] = section
    def complete(self, task): self.tasks[task]["completed"] = True
    def task(self, task): return dict(self.tasks[task])
    def rename_section(self, section, name):
        secs = getattr(self, "_sections", {}); secs[section] = name; self._sections = secs
    def sections(self, project): return [{"gid": g, "name": n} for g, n in getattr(self, "_sections", {}).items()]
    def ensure_section(self, project, name, insert_before=None):
        secs = getattr(self, "_sections", {})
        for g, n in secs.items():
            if n == name: return g
        gid = f"sec-{name.lower().replace(' ', '-')}"; secs[gid] = name; self._sections = secs
        return gid


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
    assert parse_card(n) == {"to": "a@b.com", "subject": "Your listing on NW 11th St", "body": "Hey Maria,\n\nBody?\n\nCharles", "outreach_id": 7, "reply_to": False, "cc": []}


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


def test_feedback_on_an_approved_card_holds_the_send(loaded, cfg, tmp_path, monkeypatch):
    """Rodrigo commented on Thais's card after approving it and the old sender sent it anyway. Never again."""
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s, gm = _sections(cfg), FakeGmail()
    gid = next(iter(asana.tasks))
    asana.move(gid, s["approved"])
    asana.stories[gid].append("take out the last part, and shorten it")

    class L:
        available = True
        def structured(self, purpose, system, user, schema, max_tokens=1024):
            import json
            c = json.loads(user)
            return {"subject": c["subject"], "body": c["body"].replace("Hope you're doing well. ", "")}
    res = send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET, llm=L())
    assert res["sent"] == 0 and res["held_for_feedback"] == 1 and not gm.sent
    assert any("Held" in c for c in asana.stories[gid]) and any("Revised" in c for c in asana.stories[gid])   # applied right away
    assert asana.where[gid] == s["to_review"]                           # rewrite landed → back to To Review for his decision
    # same thing when Claude cannot rewrite: the card parks in Needs Revision, never in Approved, never in To Review
    gid2 = list(asana.tasks)[1]
    asana.move(gid2, s["approved"]); asana.stories[gid2].append("mention the pool")
    res = send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET, llm=None)
    nr = asana.ensure_section(None, "Needs Revision")
    assert res["held_for_feedback"] == 1 and not gm.sent and asana.where[gid2] == nr
    assert any(c.startswith(f"{BOT} I couldn't rewrite") for c in asana.stories[gid2])
    # nothing sends from Needs Revision, and a bare drag there gets one prompt
    gid3 = list(asana.tasks)[2] if len(asana.tasks) > 2 else None
    if gid3:
        asana.move(gid3, nr)
        sync_feedback(loaded, cfg, asana, None)
        sync_feedback(loaded, cfg, asana, None)
        assert [c for c in asana.stories[gid3] if "Tell me what to change" in c].__len__() == 1 and asana.where[gid3] == nr
    assert send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET, llm=None)["sent"] == 0 and not gm.sent


def test_phone_is_added_to_older_cards(loaded, cfg, tmp_path, monkeypatch):
    from titan.approvals import agent_line
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    gid = next(iter(asana.tasks))
    oid = parse_card(asana.tasks[gid]["notes"])["outreach_id"]
    loaded.execute("UPDATE agents SET phone='+13055550123' WHERE id=(SELECT agent_id FROM outreach WHERE id=?)", (oid,))
    import re
    asana.tasks[gid]["notes"] = re.sub(r"^Agent: .*$", "Agent: Old Card · old@example.com · Brokerage", asana.tasks[gid]["notes"], flags=re.M)
    n = sync_feedback(loaded, cfg, asana, None)
    assert n["phones_added"] >= 1 and "(305) 555-0123" in asana.tasks[gid]["notes"]
    assert "(305) 555-0123" in agent_line({"name": "X", "phone": "+13055550123", "email": "x@y.com"})


def test_send_me_a_test_sends_a_copy_to_rodrigo_only(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s, gm = _sections(cfg), FakeGmail()
    gid = next(iter(asana.tasks))
    asana.stories[gid].append("send me a test email to see what it looks like: rodrigo@titanrealtyinvestments.com")
    out = sync_feedback(loaded, cfg, asana, None, gm)
    assert out["tests_sent"] == 1 and len(gm.sent) == 1
    to, subj, body = gm.sent[0]
    assert to == "rodrigo@titanrealtyinvestments.com" and subj.startswith("[TEST]") and "Test copy" in body
    assert asana.where[gid] == s["to_review"] and any("Test copy sent" in c for c in asana.stories[gid])
    assert sync_feedback(loaded, cfg, asana, None, gm)["tests_sent"] == 0       # not re-sent next tick


def test_first_paragraph_introduces_who_we_are(cfg):
    from titan.outreach.compose import compose_email, validate_email
    for seed in range(12):
        d = compose_email(None, cfg, {"name": "Maria Lopez"}, [{"id": 1, "street": "101 NW 11th St", "city": "Miami", "remarks": "", "days_on_market": 90, "price_cuts": 0, "year_built": 1990}], seed)
        greeting, first = d["body"].split("\n\n")[:2]
        assert greeting in ("Hey Maria,", "Maria,")                                    # greeting alone on its own line
        assert "Titan Realty Investments" in first and "NW 11th St" in first
        assert not any(x in d["body"] for x in ("stood out", "surrounding area", "keep this short"))
        assert not validate_email(d["subject"], d["body"], [{"street": "101 NW 11th St"}])
    p = [{"street": "101 NW 11th St"}]
    bare = "Hey Maria, quick question on NW 11th St. Is it mostly cosmetic or something bigger? How much work would you say it really needs, and is the seller open to some flexibility on price at this point?\n\nBest regards,\n\nCharles Grant"
    assert any("no intro" in x for x in validate_email("Quick question on NW 11th St", bare, p))


def test_both_listed_streets_must_appear(cfg):
    from titan.outreach.compose import validate_email
    props = [{"street": "202 S A St"}, {"street": "1 NW 75th Way"}]
    body = "Hey Dror, Charles here with Titan Realty Investments, we're looking for properties around Lake Worth. Quick question on NW 75th Way, is the tenant on a lease and how long does it run? If the rest is in decent shape there may be something we can work with.\n\nBest regards,\n\nCharles Grant"
    assert any("missing from the body" in x for x in validate_email("Quick question", body, props))


def test_failed_rewrite_keeps_the_feedback_pending(loaded, cfg, tmp_path, monkeypatch):
    from titan.approvals import FAILED_PREFIX, pending_feedback
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    gid = next(iter(asana.tasks))
    asana.stories[gid].append("mention the pool")
    class NoClaude:
        available = True
        last_error = "Not logged in"
        def structured(self, *a, **k): return None
    for _ in range(2):
        sync_feedback(loaded, cfg, asana, NoClaude(), FakeGmail())
    fails = [t for t in asana.stories[gid] if t.startswith(f"{BOT} {FAILED_PREFIX}")]
    assert len(fails) == 1 and "make claude-login" in fails[0]            # said once, not every tick
    assert pending_feedback(asana, gid)[0] == ["mention the pool"]      # still pending for the next round
    asana.comment(gid, "Held: you left a comment after approving, so this went back to To Review.")
    assert pending_feedback(asana, gid)[0] == ["mention the pool"]      # a Held note doesn't swallow it either


def test_rewrite_pending_leaves_feedback_cards_alone_and_survives_deleted_cards(loaded, cfg, tmp_path, monkeypatch):
    from titan.approvals import rewrite_pending
    from titan.asana import AsanaError
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    gids = list(asana.tasks)
    asana.stories[gids[0]].append(f"{BOT} Revised based on your comment (picture v1 attached).")
    class Asana2(FakeAsana):
        def comments(self, task):
            if task == gids[1]:
                raise AsanaError("GET: HTTP 404 Not a recognized ID")
            return FakeAsana.comments(self, task)
    a2 = Asana2(); a2.__dict__.update(asana.__dict__)
    n = rewrite_pending(loaded, cfg, a2, None, only_old_style=False)
    assert n["left_to_feedback"] == 1 and n["card_deleted"] == 1
    oid = parse_card(asana.tasks[gids[1]]["notes"])["outreach_id"]
    assert loaded.execute("SELECT status FROM outreach WHERE id=?", (oid,)).fetchone()[0] == "skipped"


def test_reply_card_sends_in_the_same_thread(loaded, cfg, tmp_path, monkeypatch):
    """An answer to an agent is a card ending '(reply to outreach #N)': approved → sent inside that email's Gmail thread."""
    from titan.approvals import post_reply_card
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s, gm = _sections(cfg), FakeGmail()
    gid = next(iter(asana.tasks)); asana.move(gid, s["approved"])
    assert send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 1
    oid = parse_card(asana.tasks[gid]["notes"])["outreach_id"]
    loaded.execute("UPDATE outreach SET gmail_thread_id='thr-1' WHERE id=?", (oid,))
    rgid = post_reply_card(loaded, cfg, asana, oid, "Hi Maria,\n\nThanks, that helps. Where does the seller realistically need to be?\n\nBest regards,\n\nCharles Grant")
    card = parse_card(asana.tasks[rgid]["notes"])
    assert card["reply_to"] and card["outreach_id"] == oid and card["subject"].startswith("Re: ") and asana.where[rgid] == s["to_review"]
    assert send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 0          # To Review never sends
    asana.move(rgid, s["approved"])
    res = send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)
    assert res["sent"] == 1 and gm.sent[-1][0] == card["to"] and gm.sent[-1][1] == card["subject"]
    assert asana.where[rgid] == s["sent"]
    row = loaded.execute("SELECT kind, status, gmail_thread_id FROM outreach WHERE asana_task_id=?", (rgid,)).fetchone()
    assert row["kind"] == "reply:manual" and row["status"] == "sent"
    assert loaded.execute("SELECT COUNT(*) FROM messages WHERE direction='out' AND action='reply:manual'").fetchone()[0] == 1
    # the original stays sent and is not re-sent; the reply card is not sent twice
    assert send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 0 and len(gm.sent) == 2


def test_gmail_send_falls_back_to_a_fresh_message_when_the_thread_is_gone():
    from titan.outreach.gmail_client import GmailClient
    class Resp: status = 404
    class NotFound(Exception):
        resp = Resp()
    calls = []
    class Msgs:
        def send(self, userId, body):
            calls.append(dict(body))
            class Ex:
                def execute(_self):
                    if "threadId" in body:
                        raise NotFound("Requested entity was not found.")
                    return {"id": "m9", "threadId": "t-new"}
            return Ex()
    class Users:
        def messages(self): return Msgs()
    class Svc:
        def users(self): return Users()
    g = GmailClient.__new__(GmailClient)
    g.svc, g.sender, g.sender_name = Svc(), "charles@example.com", "Charles Grant"
    out = g.send("lise@example.com", "Re: Quick question", "Hi Lise", thread_id="stale-thread")
    assert out == {"message_id": "m9", "thread_id": "t-new"}
    assert "threadId" in calls[0] and "threadId" not in calls[-1]


def test_gmail_client_refuses_the_wrong_mailbox(tmp_path, monkeypatch):
    """Rodrigo, 2026-10-26: an agent got an email from his own mailbox signed Charles. The logged-in account must be the sender."""
    import json
    from titan.outreach import gmail_client as gc
    tok = tmp_path / "tok.json"; tok.write_text(json.dumps({"token": "t", "refresh_token": "r", "client_id": "c", "client_secret": "s", "token_uri": "u"}))
    class Creds:
        expired, valid, refresh_token = False, True, "r"
        @staticmethod
        def from_authorized_user_file(path, scopes): return Creds()
    class Svc:
        def __init__(self, addr): self.addr = addr
        def users(self): return self
        def getProfile(self, userId): return self
        def execute(self): return {"emailAddress": self.addr}
    import google.oauth2.credentials as goc, googleapiclient.discovery as gd
    monkeypatch.setattr(goc, "Credentials", Creds)
    monkeypatch.setattr(gd, "build", lambda *a, **k: Svc("rodrigo@example.com"))
    import pytest
    with pytest.raises(gc.GmailUnavailable, match="logged in as rodrigo@example.com, not charles@example.com"):
        gc.GmailClient(tmp_path / "oauth.json", tok, "charles@example.com", "Charles Grant")
    monkeypatch.setattr(gd, "build", lambda *a, **k: Svc("Charles@example.com"))
    assert gc.GmailClient(tmp_path / "oauth.json", tok, "charles@example.com", "Charles Grant").account == "charles@example.com"


def test_comment_on_a_lead_card_rewrites_the_waiting_answer(loaded, cfg, tmp_path, monkeypatch):
    """Rodrigo, 2026-10-27 (Lise, Cheryl): he comments on the lead's own card; the answer waiting in To Review must change,
    not the first email that already went out."""
    from titan.approvals import post_reply_card
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s, gm = _sections(cfg), FakeGmail()
    gid = next(iter(asana.tasks)); asana.move(gid, s["approved"])
    send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)
    oid = parse_card(asana.tasks[gid]["notes"])["outreach_id"]
    first_body = parse_card(asana.tasks[gid]["notes"])["body"]
    rgid = post_reply_card(loaded, cfg, asana, oid, "Hi Maria,\n\nThanks for letting me know.\n\nWhat day works for a showing?\n\nBest regards,\n\nCharles Grant")
    from titan.conversations import board_sections
    asana.move(gid, board_sections(cfg, asana)[1])
    asana.stories[gid].append("ask her if it's under contract first, we discuss viewing after")

    class L:
        available = True
        def structured(self, purpose, system, user, schema, max_tokens=1024):
            import json
            u = json.loads(user)
            assert u["feedback"] == ["ask her if it's under contract first, we discuss viewing after"]
            assert "showing" in u["body"]                           # it is the answer being rewritten, not the first email
            return {"subject": u["subject"], "body": "Hi Maria,\n\nThanks for letting me know.\n\nIs it already under contract, or are you still working through that offer?\n\nOnce we know where it stands, we can set up a time to see it.\n\nBest regards,\n\nCharles Grant"}
    res = sync_feedback(loaded, cfg, asana, L())
    assert res["revised"] == 1, (res, asana.stories[gid], asana.stories[rgid])
    assert "under contract" in parse_card(asana.tasks[rgid]["notes"])["body"]
    assert parse_card(asana.tasks[gid]["notes"])["body"] == first_body
    assert any("went to the answer" in c for c in asana.stories[gid])
    assert asana.where[rgid] == s["to_review"]
    assert sync_feedback(loaded, cfg, asana, L())["revised"] == 0          # not processed twice


def test_comment_on_a_lead_card_with_no_answer_waiting_says_so(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s, gm = _sections(cfg), FakeGmail()
    gid = next(iter(asana.tasks)); asana.move(gid, s["approved"])
    send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)
    from titan.approvals import needs_revision_section
    asana.move(gid, needs_revision_section(cfg, asana))
    asana.stories[gid].append("make it shorter")
    res = sync_feedback(loaded, cfg, asana, None)
    assert res["revised"] == 0 and res["revise_failed"] == 0
    assert any("nothing to rewrite" in c for c in asana.stories[gid])


def test_dictated_answer_on_a_lead_card_becomes_the_reply_card_and_sends_from_approved(loaded, cfg, tmp_path, monkeypatch):
    """Rodrigo, 2026-10-27 (Richard): 'Answer the email with the following: Hey Richard, ...' on the lead card, then dragged to
    Approved, must send that answer, not bounce the card back with 'Holding it'."""
    from titan.approvals import dictated_answer
    assert dictated_answer(["make it shorter"], cfg) is None
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s, gm = _sections(cfg), FakeGmail()
    gid = next(iter(asana.tasks)); asana.move(gid, s["approved"])
    send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)
    oid = parse_card(asana.tasks[gid]["notes"])["outreach_id"]
    loaded.execute("UPDATE outreach SET gmail_thread_id='thr-9' WHERE id=?", (oid,))
    asana.move(gid, s["approved"])
    asana.stories[gid].append("Answer the email with the following: Hey Maria, thanks for getting back to us, we're mostly interested in "
                              "the Hollywood property but it seems we're quite a bit away from the asking price. Is the seller willing to take a substantial discount on it?")
    res = send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET, llm=None)
    assert res["sent"] == 1 and len(gm.sent) == 2
    body = gm.sent[-1][2]
    assert body.startswith("Hey Maria,\n\n") and "substantial discount" in body and "Charles Grant" in body
    rgid = next(g for g, t in asana.tasks.items() if t["name"].startswith("Reply · "))
    assert asana.tasks[rgid].get("completed") and asana.where[rgid] == s["sent"]           # answer card closed after the send
    assert any("📤 Sent to" in c and "substantial discount" in c for c in asana.stories[gid])  # the lead card carries it
    assert asana.where[gid] != s["approved"]
    assert send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 0 and len(gm.sent) == 2
