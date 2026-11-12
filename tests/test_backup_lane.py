"""The backup lane (Rodrigo, 2026-11-12): houses that fit and are under contract get a backup email (a card he approves), a
check-in sequence in the same thread, and a watch that lights up when the house comes back on the market."""
from datetime import timedelta

from titan import backup_lane as bl
from titan.approvals import needs_me_section, parse_card, send_approved
from titan.board_clean import ACTIVE, PENDING, SOLD
from titan.follow_up import validate_follow_up
from titan.outreach.compose import format_paragraphs, signature
from titan.outreach.send import candidate_rows, ensure_thread, log_message, run_outreach
from titan.util import iso, now_utc
from tests.test_approvals import SAT_10AM_ET, FakeAsana, FakeGmail, _sections


def _setup(loaded, cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("TITAN_MODE", "DRAFT_FOR_APPROVAL")
    cfg.root = tmp_path
    cfg["outreach"]["backup_lane"]["pause_seconds"] = 0
    loaded.execute("UPDATE agents SET email=lower(replace(name,' ','.'))||'@example.com', email_confidence='high' WHERE email IS NULL")
    rows = candidate_rows(loaded, cfg)
    assert rows
    return rows[0]


def _lookup(status_by_id, default=ACTIVE):
    return lambda p: status_by_id.get(p["id"], default)


def _age(conn, pid, days, col="pending_since"):
    conn.execute(f"UPDATE backup_leads SET {col}=? WHERE property_id=?", (iso(now_utc() - timedelta(days=days)), pid))


def test_templates_pass_the_house_rules(cfg):
    p = {"street": "1420 NW 11th St", "city": "Hialeah", "photos_count": 20}
    for told in (False, True):
        for seed in range(len(bl.TEMPLATES)):
            s, b, src = bl.write_backup(None, cfg, {"name": "Maria Lopez"}, p, {"source": "the agent told us" if told else "realtor.com"}, seed)
            assert src == "template" and bl.validate_backup(s, b, [p]) == [], (s, b, bl.validate_backup(s, b, [p]))
    for t in bl.CHECKINS:
        body = format_paragraphs(t.format(first="Maria", street="NW 11th St") + "\n\n" + signature(cfg))
        assert validate_follow_up(body, [p]) == [], (body, validate_follow_up(body, [p]))


def test_pending_house_enters_the_lane_and_leaves_the_normal_one(loaded, cfg, tmp_path, monkeypatch):
    p = _setup(loaded, cfg, tmp_path, monkeypatch)
    asana = FakeAsana()
    out = bl.run(loaded, cfg, asana, None, lookup=_lookup({p["id"]: PENDING}))
    assert out["found"]["found_pending"] == 1 and out["lane"] == {bl.WATCHING: 1}
    assert out["emails"]["waiting_days"] == 1 and not asana.tasks                   # pending for a day: too early, nobody emailed
    assert bl.run(loaded, cfg, asana, None, lookup=_lookup({}), dry_run=True)["lane"] == {bl.WATCHING: 1}      # --list changes nothing
    assert p["id"] not in [r["id"] for r in candidate_rows(loaded, cfg)]            # the normal first email is never written for it
    run_outreach(loaded, cfg, None, FakeGmail(), None, asana=asana)
    assert not [1 for t in asana.tasks.values() if p["street"] in t["name"]]


def test_backup_email_card_then_check_in_then_back_on_market(loaded, cfg, tmp_path, monkeypatch):
    p = _setup(loaded, cfg, tmp_path, monkeypatch)
    pid, asana, gm, s = p["id"], FakeAsana(), FakeGmail(), _sections(cfg)
    look = {pid: PENDING}
    bl.run(loaded, cfg, asana, None, lookup=_lookup(look))
    _age(loaded, pid, 8)
    out = bl.run(loaded, cfg, asana, None, lookup=_lookup(look))
    assert out["emails"]["cards"] == 1 and len(asana.tasks) == 1
    gid = next(iter(asana.tasks))
    card = parse_card(asana.tasks[gid]["notes"])
    row = loaded.execute("SELECT * FROM outreach WHERE id=?", (card["outreach_id"],)).fetchone()
    assert asana.where[gid] == s["to_review"] and row["kind"] == "backup_touch" and row["status"] == "pending_approval"
    assert "under contract" in card["body"] and "$" not in card["body"] and asana.tasks[gid]["name"].startswith("⏳ PENDING")
    assert bl.run(loaded, cfg, asana, None, lookup=_lookup(look))["emails"]["cards"] == 0      # one card per house
    assert send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 0                 # nothing goes without his drag
    asana.move(gid, s["approved"])
    assert send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 1
    # ten days later, still pending, no answer: a check-in is drafted on the same card, in Needs Me, not sent
    loaded.execute("UPDATE outreach SET sent_at=? WHERE id=?", (iso(now_utc() - timedelta(days=11)), row["id"]))
    _age(loaded, pid, 11, "last_touch_at")
    _age(loaded, pid, 3, "last_check_at")
    out = bl.run(loaded, cfg, asana, None, lookup=_lookup(look))
    assert out["check_ins"]["posted"] == 1 and out["watch"]["still_pending"] == 1
    draft = loaded.execute("SELECT answer_draft FROM outreach WHERE id=?", (row["id"],)).fetchone()[0]
    assert draft.startswith("Good morning") and asana.where[gid] == needs_me_section(cfg, asana) and len(gm.sent) == 1
    assert bl.run(loaded, cfg, asana, None, lookup=_lookup(look))["check_ins"]["posted"] == 0   # not again while one waits
    # the deal falls through: the house is for sale again
    _age(loaded, pid, 3, "last_check_at")
    out = bl.run(loaded, cfg, asana, None, lookup=_lookup({pid: ACTIVE}))
    assert out["watch"]["back_on_market"][0]["street"] == p["street"]
    assert loaded.execute("SELECT lead_status, back_on_market FROM properties WHERE id=?", (pid,)).fetchone()[:] == ("NEEDS_CALL", 1)
    assert any("BACK ON THE MARKET" in c for c in asana.stories[gid]) and asana.where[gid] == needs_me_section(cfg, asana)
    assert any("BACK ON THE MARKET" in x for x in bl.summary_lines(loaded, cfg))


def test_sold_stops_everything_and_an_answer_ends_the_sequence(loaded, cfg, tmp_path, monkeypatch):
    p = _setup(loaded, cfg, tmp_path, monkeypatch)
    pid, asana, s = p["id"], FakeAsana(), _sections(cfg)
    bl.run(loaded, cfg, asana, None, lookup=_lookup({pid: PENDING}))
    _age(loaded, pid, 8)
    bl.run(loaded, cfg, asana, None, lookup=_lookup({pid: PENDING}))
    gid = next(iter(asana.tasks))
    oid = parse_card(asana.tasks[gid]["notes"])["outreach_id"]
    _age(loaded, pid, 3, "last_check_at")
    out = bl.run(loaded, cfg, asana, None, lookup=_lookup({pid: SOLD}))
    assert out["watch"]["closed"] == 1 and asana.where[gid] == s["skip"]
    assert loaded.execute("SELECT status FROM outreach WHERE id=?", (oid,)).fetchone()[0] == "skipped"
    assert loaded.execute("SELECT lead_status FROM properties WHERE id=?", (pid,)).fetchone()[0] == "EXCLUDED"
    # a second house: the email went out and the agent wrote back → the normal reply engine has it, no check-ins
    loaded.execute("UPDATE backup_leads SET state=?, outreach_id=? WHERE property_id=?", (bl.CARD, oid, pid))
    loaded.execute("UPDATE outreach SET status='sent', sent_at=? WHERE id=?", (iso(now_utc() - timedelta(days=12)), oid))
    tid = ensure_thread(loaded, p["a_id"], "email", "th-backup", [pid])
    log_message(loaded, tid, "in", "Still pending but the buyer is shaky, I'll let you know.", "m-b1", "INTERESTED")
    out = bl.check_ins(loaded, cfg, asana, None)
    assert out["answered"] == 1 and out["posted"] == 0


def test_agent_who_told_us_it_went_under_contract_is_in_the_lane(loaded, cfg, tmp_path, monkeypatch):
    p = _setup(loaded, cfg, tmp_path, monkeypatch)
    tid = ensure_thread(loaded, p["a_id"], "email", "th-1", [p["id"]])
    log_message(loaded, tid, "in", "Thanks, but we just went under contract on this one.", "m1", "SOLD_OR_PENDING")
    tid2 = ensure_thread(loaded, p["a_id"], "email", "th-2", [p["id"] + 1000])
    log_message(loaded, tid2, "in", "It sold last week.", "m2", "SOLD_OR_PENDING")
    out = bl.run(loaded, cfg, FakeAsana(), None, lookups=False)
    assert out["found"]["from_agents"] == 1
    lead = loaded.execute("SELECT * FROM backup_leads").fetchone()
    assert lead["property_id"] == p["id"] and lead["source"] == "the agent told us"
    s, b, _ = bl.write_backup(None, cfg, {"name": p["a_name"]}, p, dict(lead))
    assert "You mentioned" in b and bl.validate_backup(s, b, [p]) == []


class _Reader:
    available = True
    def __init__(self, verdict):
        self.verdict, self.read = verdict, []
    def structured(self, purpose, system, user, schema, **kw):
        if purpose != "second_read":
            return None                                    # the writer falls back to the template
        self.read.append(user)
        return {"verdict": self.verdict, "problems": [], "fix": "", "why": "checked the note against what we know about the house"}


def test_every_backup_email_is_read_by_the_second_reader(loaded, cfg, tmp_path, monkeypatch):
    p = _setup(loaded, cfg, tmp_path, monkeypatch)
    cfg["outreach"]["second_read"]["enabled"] = True
    pid, asana = p["id"], FakeAsana()
    bl.run(loaded, cfg, asana, None, lookup=_lookup({pid: PENDING}))
    _age(loaded, pid, 8)
    hold = _Reader("HOLD")
    out = bl.run(loaded, cfg, asana, hold, lookup=_lookup({pid: PENDING}))
    assert out["emails"]["held"] == 1 and not asana.tasks and hold.read          # the reader said no: nothing reaches the board
    loaded.execute("UPDATE backup_leads SET last_touch_at=NULL WHERE property_id=?", (pid,))
    ok = _Reader("SEND")
    out = bl.run(loaded, cfg, asana, ok, lookup=_lookup({pid: PENDING}))
    gid = next(iter(asana.tasks))
    assert out["emails"]["cards"] == 1 and any("Second read" in c for c in asana.stories[gid])
