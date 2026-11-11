"""Asana email approvals (Rodrigo, 2026-10-24): cards → feedback rewrites → approved emails sent in the window only."""
import json
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

    def create_task_html(self, project, section, name, html_notes):
        gid = self.create_task(project, section, name, "")["gid"]
        self.update_html_notes(gid, html_notes)
        return {"gid": gid}

    def update_notes(self, task, notes): self.tasks[task]["notes"] = notes
    def update_html_notes(self, task, html):            # Asana renders html_notes to plain notes: tags off, text and lines kept
        import html as h, re
        self.tasks[task]["html_notes"] = html
        self.tasks[task]["notes"] = h.unescape(re.sub(r"<hr/>", "-" * 40, re.sub(r"</?(?!hr)[a-z]+(?: [^>]*)?/?>", "", html))).strip()
    def rename(self, task, name): self.tasks[task]["name"] = name
    def update_card(self, task, name, html): self.rename(task, name); self.update_html_notes(task, html)
    def delete_story(self, gid): self.deleted = getattr(self, "deleted", []) + [gid]
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
    _ids = itertools.count(1)                    # unique ids across instances, like the real Gmail
    def __init__(self): self.sent = []
    def send(self, to, subject, body, **kw):
        self.sent.append((to, subject, body)); n = next(self._ids); return {"message_id": f"m{n}", "thread_id": f"t{n}"}
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
    assert asana.where[gid] == asana.ensure_section(None, "Needs Me")  # rewrite landed → Needs Me, his decision (Rodrigo, 2026-10-27)
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
        # Rodrigo, 2026-11-02: a bare drag to Needs Revision goes straight back to Needs Me (that column is only for commented cards)
        assert asana.where[gid3] == asana.ensure_section(None, "Needs Me") and "back in Needs Me" in asana.stories[gid3][-1]
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
    assert card["reply_to"] and card["outreach_id"] == oid and card["subject"].startswith("Re: ")
    assert asana.where[rgid] == asana.ensure_section(None, "Needs Me")                   # an answer is his to approve
    assert send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 0          # Needs Me never sends
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
    """Rodrigo, 2026-10-27 (Lise, Cheryl) and 2026-11-03 (Thais, one card per lead): he comments on the lead's own card; the
    answer waiting (now the NEXT EMAIL on that same card) must change, not the first email that already went out. A separate
    reply card still on the board is folded into the lead card first."""
    from titan.approvals import post_reply_card
    from titan.answers import pending
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
            if purpose == "card_chat":
                return {"answer": "Will do.", "lead_note": "", "reply_change": ""}
            assert purpose == "revise_email"
            assert u["feedback"] == ["ask her if it's under contract first, we discuss viewing after"]
            assert "showing" in u["body"]                           # it is the answer being rewritten, not the first email
            return {"subject": u["subject"], "body": "Hi Maria,\n\nThanks for letting me know.\n\nIs it already under contract, or are you still working through that offer?\n\nOnce we know where it stands, we can set up a time to see it.\n\nBest regards,\n\nCharles Grant"}
    res = sync_feedback(loaded, cfg, asana, L())
    assert res["folded"] == 1 and asana.tasks[rgid].get("completed")      # the second card is gone
    assert res["revised"] == 1, (res, asana.stories[gid])
    notes = asana.tasks[gid]["notes"]
    card = parse_card(notes)
    assert "under contract" in card["body"] and card["subject"].startswith("Re: ") and card["outreach_id"] == oid
    assert "NEXT EMAIL" in notes and "First email" in notes and first_body.split("\n")[0] in notes   # history stays on the card
    assert pending(loaded, oid)["body"] == card["body"]
    assert loaded.execute("SELECT body FROM outreach WHERE id=?", (oid,)).fetchone()[0] == first_body   # the sent email is untouched
    assert asana.where[gid] == asana.ensure_section(None, "Needs Me")     # rewritten for him → Needs Me (Rodrigo, 2026-10-27)
    assert sync_feedback(loaded, cfg, asana, L())["revised"] == 0          # not processed twice
    # dragging the LEAD card to Approved sends the answer in the agent's thread; the card keeps the history
    asana.move(gid, s["approved"])
    assert send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 1
    assert gm.sent[-1][1].startswith("Re: ") and "under contract" in gm.sent[-1][2]
    assert pending(loaded, oid) is None and any("📤 Sent to" in c for c in asana.stories[gid])
    assert parse_card(asana.tasks[gid]["notes"])["body"] == first_body and asana.where[gid] == s["sent"]
    assert send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 0


def test_comment_on_a_lead_card_with_no_answer_waiting_writes_the_answer_on_that_card(loaded, cfg, tmp_path, monkeypatch):
    """Rodrigo, 2026-11-03: "I ask it to rewrite an email and it just doesn't do it and sends it back to Needs Me". A change
    asked on a lead card with nothing drafted yet gets Charles's answer written on that card; with Claude down the comment is
    held once and retried, never lost."""
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s, gm = _sections(cfg), FakeGmail()
    gid = next(iter(asana.tasks)); asana.move(gid, s["approved"])
    send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)
    oid = parse_card(asana.tasks[gid]["notes"])["outreach_id"]
    from titan.approvals import needs_revision_section
    asana.move(gid, needs_revision_section(cfg, asana))
    asana.stories[gid].append("ask her if the seller would hear a cash proposal this week")
    before = len(asana.tasks)
    res = sync_feedback(loaded, cfg, asana, None)
    assert res["revised"] == 0 and res["revise_failed"] == 1 and len(asana.tasks) == before
    assert asana.stories[gid][-1].startswith(f"{BOT} I couldn't rewrite") and asana.where[gid] == asana.ensure_section(None, "Needs Me")
    assert sync_feedback(loaded, cfg, asana, None)["revise_failed"] == 1 and sum("couldn't rewrite" in c for c in asana.stories[gid]) == 1

    class L:
        available = True
        def structured(self, purpose, system, user, schema, max_tokens=1024):
            import json
            if purpose == "card_chat":
                return {"answer": "On it.", "lead_note": "", "reply_change": ""}
            assert purpose == "draft_reply" and "cash proposal" in json.loads(user)["rodrigo_instruction"]
            return {"read": "x", "body": "Hi Maria,\n\nQuick one on the house.\n\nWould the seller hear a cash proposal this week?\n\n" + json.loads(user)["signature_block"]}
    res = sync_feedback(loaded, cfg, asana, L())
    assert res["revised"] == 1 and len(asana.tasks) == before                 # no second card
    assert "cash proposal" in parse_card(asana.tasks[gid]["notes"])["body"] and "NEXT EMAIL" in asana.tasks[gid]["notes"]
    assert asana.where[gid] == asana.ensure_section(None, "Needs Me")


def test_approvals_tick_rewrites_cards_that_ask_how_much_work_a_photographed_listing_needs(loaded, cfg, tmp_path, monkeypatch):
    """Rodrigo, 2026-10-27: waiting cards that still ask about the work on a listing with 4+ photos get rewritten on the tick."""
    from titan.approvals import rewrite_pending
    from titan.outreach.compose import signature
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    loaded.execute("UPDATE properties SET photos_count=18")
    gids = list(asana.tasks)
    oid0 = parse_card(asana.tasks[gids[0]]["notes"])["outreach_id"]
    pid = json.loads(loaded.execute("SELECT property_ids FROM outreach WHERE id=?", (oid0,)).fetchone()[0])[0]
    street = loaded.execute("SELECT street FROM properties WHERE id=?", (pid,)).fetchone()[0]
    bad = (f"Hey Maria,\n\nCharles with Titan Realty Investments. I saw your listing on {street}. It could work for us.\n\n"
           f"How much work would you say it really needs? And is the seller open to some flexibility?\n\n{signature(cfg)}")
    loaded.execute("UPDATE outreach SET body=? WHERE id=?", (bad, oid0))
    n = rewrite_pending(loaded, cfg, asana, None, violating_only=True)
    assert n["rewritten"] == 1                                   # only the violating card; the others are left as they are
    new_body = loaded.execute("SELECT body FROM outreach WHERE id=?", (oid0,)).fetchone()[0]
    assert "How much work would you say" not in new_body and "18 photos" in asana.stories[gids[0]][-1]
    assert rewrite_pending(loaded, cfg, asana, None, violating_only=True)["rewritten"] == 0   # done once, not every tick

def test_dictated_answer_on_a_lead_card_becomes_its_next_email_and_sends_from_approved(loaded, cfg, tmp_path, monkeypatch):
    """Rodrigo, 2026-10-27 (Richard) and 2026-11-03 (Thais): 'Answer the email with the following: Hey Richard, ...' on the lead
    card, then dragged to Approved, must send that answer from that same card, not bounce it back with 'needs me'."""
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
    before = len(asana.tasks)
    res = send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET, llm=None)
    assert res["sent"] == 1 and len(gm.sent) == 2 and len(asana.tasks) == before          # no second card, ever
    body = gm.sent[-1][2]
    assert body.startswith("Hey Maria,\n\n") and "substantial discount" in body and "Charles Grant" in body
    assert any("📤 Sent to" in c and "substantial discount" in c for c in asana.stories[gid])  # the lead card carries it
    assert asana.where[gid] == s["sent"]
    assert send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 0 and len(gm.sent) == 2


def test_the_email_as_one_comment_and_send_it_as_the_next_is_sent_from_the_lead_card(loaded, cfg, tmp_path, monkeypatch):
    """Thais Guevara, 2026-11-02: Rodrigo wrote the email in one comment and "I wrote a message up top … put that into an email
    and send it to her" in the next, then dragged the card to Approved. The engine forwarded it to a reply card and bounced the
    lead card to Needs Me. Now: his words (cleaned up by Claude) become the NEXT EMAIL on the lead card, in Approved, and send."""
    from titan.answers import dictation, pending, wants_send
    email = ("Hey no problem if we can chat tomorrow that's fine too for now let me ask you in terms of the Code Compliance is that the "
             "property has what are they exactly does the city know about the the structure that he has in the back is there a violation "
             "and will the property be sold free and clear of any liens and violations? We like the area and we'd be willing to move fast on "
             "something. Also do you have any more updated pictures of the structures")
    ask = ("I wrote a message up top what I want us to do is I want us to put that into an email and send it to her what I what I wrote "
           "there because that's the main concerns about the property and she emailed us back so this is a hot lead")
    d = dictation([email, ask])
    assert d and d[0] == email and wants_send(d[1])
    assert dictation([ask]) is None and dictation(["ask her about the roof"]) is None
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s, gm = _sections(cfg), FakeGmail()
    gid = next(iter(asana.tasks)); asana.move(gid, s["approved"])
    send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)
    oid = parse_card(asana.tasks[gid]["notes"])["outreach_id"]
    asana.move(gid, s["approved"])
    asana.stories[gid] += [email, ask]
    tidy = ("Hey Maria,\n\nNo problem, if we can chat tomorrow that is fine too.\n\nOn the code compliance, what exactly has the city flagged? "
            "Does the city know about the structure in the back, is there a violation, and will the property be sold free and clear of any liens "
            "and violations?\n\nWe like the area and would move fast on something. Do you have any updated pictures of the structures?")

    class L:
        available = True
        def structured(self, purpose, system, user, schema, max_tokens=1024):
            import json
            if purpose == "tidy_dictation":
                return {"body": tidy + "\n\n" + json.loads(user)["signature_block"]}
            return {"answer": "ok", "lead_note": "", "reply_change": ""}
    before = len(asana.tasks)
    res = send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET, llm=L())
    assert res["sent"] == 1 and gm.sent[-1][2].startswith(tidy)
    assert not any(t["name"].startswith("Reply ·") for t in asana.tasks.values())      # no second card on the approvals board
    assert [t["name"] for t in asana.tasks.values() if t["name"].startswith("📞")]      # "this is a hot lead": the call card he asked for
    assert pending(loaded, oid) is None and asana.where[gid] == s["sent"]
    assert any("📤 Sent to" in c and "free and clear" in c for c in asana.stories[gid])
    assert any("NEXT EMAIL" in c and "goes out at the next send round" in c for c in asana.stories[gid])


def test_send_her_the_email_on_a_lead_card_in_approved_keeps_it_there(loaded, cfg, tmp_path, monkeypatch):
    """Thais, 2026-11-02: "i ALREADY SENT THIS TO APPROVED MULTIPLE TIMES WHY IS IT COMING BACK TO NEEDS ME. sEND HER THE EMAIL."
    was read as a question, answered in chat, and the card went back to Needs Me. A lead card with an answer on it, in Approved,
    stays there unless he changes the text or says hold."""
    from titan.answers import set_answer
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s, gm = _sections(cfg), FakeGmail()
    gid = next(iter(asana.tasks)); asana.move(gid, s["approved"])
    send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)
    oid = parse_card(asana.tasks[gid]["notes"])["outreach_id"]
    set_answer(loaded, cfg, asana, oid, "Hey Maria,\n\nIs the seller open to a conversation?\n\nBest regards,\n\nCharles Grant")
    assert asana.where[gid] == asana.ensure_section(None, "Needs Me") and "NEXT EMAIL" in asana.tasks[gid]["notes"]
    asana.move(gid, s["approved"])
    asana.stories[gid].append("Hey i ALREADY SENT THIS TO APPROVED MULTIPLE TIMES WHY IS IT COMING BACK TO NEEDS ME. sEND HER THE EMAIL.")
    res = send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET, llm=None)
    assert res["sent"] == 1 and "open to a conversation" in gm.sent[-1][2] and asana.where[gid] == s["sent"]
    # and a bare "hold" on an approved answer does take it out
    set_answer(loaded, cfg, asana, oid, "Hey Maria,\n\nOne more question on the roof.\n\nBest regards,\n\nCharles Grant", approved=True)
    assert asana.where[gid] == s["approved"]
    asana.stories[gid].append("hold, wait on this one")
    assert send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET, llm=None)["sent"] == 0
    assert asana.where[gid] == asana.ensure_section(None, "Needs Me")


def test_to_review_is_renamed_fresh_leads_once(loaded, cfg, tmp_path, monkeypatch):
    """Rodrigo, 2026-10-27: "To Review is only properties I've not touched yet, and they're fresh … name it fresh leads"."""
    from titan.approvals import FRESH_LEADS, ensure_fresh_leads_name
    asana = FakeAsana()
    s = _sections(cfg)
    asana._sections = {s["to_review"]: "To Review", s["approved"]: "Approved"}
    ensure_fresh_leads_name(cfg, asana)
    assert asana._sections[s["to_review"]] == FRESH_LEADS and asana._sections[s["approved"]] == "Approved"
    asana._sections[s["to_review"]] = "To Review"
    ensure_fresh_leads_name(cfg, asana)           # once per process: not renamed again this run
    assert asana._sections[s["to_review"]] == "To Review"
