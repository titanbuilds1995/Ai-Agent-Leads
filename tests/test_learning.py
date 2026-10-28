"""Trust ladder (Rodrigo, 2026-10-26): decisions recorded, comments become the knowledge base, autopilot only with his go-ahead,
and the lead board (Answered / Sent · no answer yet)."""
import json
from datetime import timedelta

from titan import db
from titan.approvals import BOT, parse_card, send_approved, sync_feedback
from titan.conversations import ANSWERED, NO_ANSWER, STAND_START, lead_board
from titan.learning import (APPROVED_AS_IS, APPROVED_EDITED, AUTO, CONTROL_PREFIX, SKIPPED, autopilot_tick, build_knowledge,
                            knowledge_text, ladder, learn_tick, rate, record_decision)
from titan.outreach.send import ensure_thread, log_message
from tests.test_approvals import SAT_10AM_ET, FakeGmail, _cards, _sections


class _LLM:
    available = True
    def structured(self, purpose, system, user, schema, **kw):
        import json
        body = json.loads(user)["body"]
        return {"subject": "Quick question on the house", "body": body.replace("Hey", "Hi", 1)}


def _decide(conn, n_as_is, n_edited=0, n_skipped=0, kind="first_touch"):
    i = 0
    for dec, k in ((APPROVED_AS_IS, n_as_is), (APPROVED_EDITED, n_edited), (SKIPPED, n_skipped)):
        for _ in range(k):
            i += 1
            comments = [{"text": "shorter please"}] if dec == APPROVED_EDITED else []
            record_decision(conn, f"g{kind}{i}", None, kind, APPROVED_AS_IS if dec != SKIPPED else SKIPPED, comments, "Hi body", "Hi body")


def test_decisions_classify_as_is_edited_hand_edited_and_auto(conn):
    assert record_decision(conn, "a", None, "first_touch", APPROVED_AS_IS, [], "body", "body")
    assert not record_decision(conn, "a", None, "first_touch", APPROVED_AS_IS, [], "body", "body")      # first decision wins
    record_decision(conn, "b", None, "first_touch", APPROVED_AS_IS, [{"text": "say who we are first"}], "x", "x")
    record_decision(conn, "c", None, "first_touch", APPROVED_AS_IS, [], "his own words", "engine words")
    record_decision(conn, "d", None, "first_touch", APPROVED_AS_IS, [{"text": f"{BOT} Autopilot: moved"}], "x", "x")
    record_decision(conn, "e", None, "first_touch", APPROVED_AS_IS, [{"text": "send me a test"}], "x", "x")   # a test request is not an edit
    got = dict(conn.execute("SELECT card_gid, decision FROM card_decisions").fetchall())
    assert got == {"a": APPROVED_AS_IS, "b": APPROVED_EDITED, "c": APPROVED_EDITED, "d": AUTO, "e": APPROVED_AS_IS}
    r = rate(conn, "first_touch", 30)
    assert r["decided"] == 4 and r["as_is"] == 2          # the autopilot send is not a vote


def test_knowledge_base_carries_his_comments_and_clean_approvals(conn, cfg, tmp_path):
    cfg.root = tmp_path
    assert build_knowledge(conn, cfg) == ""
    record_decision(conn, "a", None, "first_touch", APPROVED_AS_IS, [{"text": "Never say we buy, we're looking for properties"}], "x", "x")
    record_decision(conn, "b", None, "first_touch", APPROVED_AS_IS, [], "Hi Maria, this is the clean one?", "Hi Maria, this is the clean one?")
    learn_tick(conn, cfg, type("A", (), {"section_tasks": lambda s, x: [], "sections": lambda s, p: []})())
    k = knowledge_text(cfg)
    assert "Never say we buy" in k and "this is the clean one" in k


def test_knowledge_reaches_the_revise_prompt_and_comments_become_lessons(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    record_decision(loaded, "old", None, "first_touch", APPROVED_AS_IS, [{"text": "mention the street twice"}], "x", "x")
    learn_tick(loaded, cfg, asana)
    seen = {}

    class Spy(_LLM):
        def structured(self, purpose, system, user, schema, **kw):
            seen["system"] = system
            return super().structured(purpose, system, user, schema)
    gid = next(iter(asana.tasks))
    asana.stories[gid].append("make it warmer")
    sync_feedback(loaded, cfg, asana, Spy())
    assert "mention the street twice" in seen["system"]
    assert loaded.execute("SELECT COUNT(*) FROM lessons WHERE text='make it warmer'").fetchone()[0] == 1


def test_sent_card_records_a_decision_and_hand_edits_count(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s = _sections(cfg)
    g1, g2 = list(asana.tasks)[:2]
    asana.tasks[g2]["notes"] = asana.tasks[g2]["notes"].replace("-----EMAIL-----\n", "-----EMAIL-----\nRodrigo added this line.\n")
    asana.move(g1, s["approved"]); asana.move(g2, s["approved"])
    assert send_approved(loaded, cfg, asana, FakeGmail(), now=SAT_10AM_ET)["sent"] == 2
    got = dict(loaded.execute("SELECT card_gid, decision FROM card_decisions").fetchall())
    assert got == {g1: APPROVED_AS_IS, g2: APPROVED_EDITED}


def test_autopilot_needs_the_gate_and_his_go_ahead(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s = _sections(cfg)
    loaded.execute("UPDATE outreach SET variant='claude'")
    later = SAT_10AM_ET + timedelta(days=3)
    # not enough history: no control card, nothing moves
    assert autopilot_tick(loaded, cfg, asana, later) == {"on": False, "offered": False, "moved": 0, "paused": False}
    _decide(loaded, 26, n_edited=4)                                       # 26/30 = 87% ≥ 80%
    assert next(x for x in ladder(loaded, cfg) if x["kind"] == "first_touch")["ready"]
    assert autopilot_tick(loaded, cfg, asana, later)["offered"]
    ctrl = next(g for g, t in asana.tasks.items() if t["name"].startswith(CONTROL_PREFIX))
    assert asana.where[ctrl] == asana.ensure_section(None, "Needs Me")      # his go-ahead card is his to act on (Rodrigo, 2026-10-27)
    assert autopilot_tick(loaded, cfg, asana, later)["moved"] == 0          # offered, but Rodrigo has not said yes
    asana.move(ctrl, s["approved"])                                       # his go-ahead
    cards = [g for g in asana.tasks if g != ctrl]
    asana.stories[cards[0]].append("hold on, change the opener")
    res = autopilot_tick(loaded, cfg, asana, later)
    assert res["on"] and res["moved"] == len(cards) - 1 and asana.where[cards[0]] == s["to_review"]
    # the control card itself is never "sent"
    gm = FakeGmail()
    out = send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)
    assert out["errors"] == 0 and asana.where[ctrl] == s["approved"]
    assert loaded.execute("SELECT COUNT(*) FROM card_decisions WHERE decision=?", (AUTO,)).fetchone()[0] == out["sent"]


def test_autopilot_turns_itself_off_when_the_rate_drops(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s = _sections(cfg)
    _decide(loaded, 26, n_edited=4)
    autopilot_tick(loaded, cfg, asana, SAT_10AM_ET)
    ctrl = next(g for g, t in asana.tasks.items() if t["name"].startswith(CONTROL_PREFIX))
    asana.move(ctrl, s["approved"])
    loaded.execute("UPDATE card_decisions SET decided_at='2000-01-01'")
    for i in range(15):
        record_decision(loaded, f"bad{i}", None, "first_touch", SKIPPED, [], None, None)
    res = autopilot_tick(loaded, cfg, asana, SAT_10AM_ET)
    assert res["paused"] and asana.where[ctrl] == asana.ensure_section(None, "Needs Me")


def test_lead_board_moves_answered_cards_and_says_where_we_stand(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s = _sections(cfg)
    g1, g2 = list(asana.tasks)[:2]
    asana.move(g1, s["approved"]); asana.move(g2, s["approved"])
    send_approved(loaded, cfg, asana, FakeGmail(), now=SAT_10AM_ET)
    o1 = parse_card(asana.tasks[g1]["notes"])["outreach_id"]
    o2 = parse_card(asana.tasks[g2]["notes"])["outreach_id"]
    a1 = loaded.execute("SELECT agent_id FROM outreach WHERE id=?", (o1,)).fetchone()[0]
    a2 = loaded.execute("SELECT agent_id FROM outreach WHERE id=?", (o2,)).fetchone()[0]
    t1 = loaded.execute("SELECT id FROM threads WHERE agent_id=?", (a1,)).fetchone()[0]
    t2 = loaded.execute("SELECT id FROM threads WHERE agent_id=?", (a2,)).fetchone()[0]
    log_message(loaded, t1, "in", "Bring your best offer, seller needs time to move out.", external_id="x1", classification="ASK_FOR_OFFER", confidence=0.9)
    log_message(loaded, t2, "in", "Delivery failed: mailbox full", external_id="x2", classification="BOUNCE", confidence=0.98)
    asana._sections = {s["sent"]: "Sent", **getattr(asana, "_sections", {})}
    res = lead_board(loaded, cfg, asana)
    answered = next(g for g, n in asana._sections.items() if n == ANSWERED)
    needs_me = next(g for g, n in asana._sections.items() if n == "Needs Me")
    assert asana._sections[s["sent"]] == NO_ANSWER
    # she asked for an offer: the next move is Rodrigo's, so Needs Me (2026-10-27); a bounce is not an answer
    assert res["moved"] == 1 and asana.where[g1] == needs_me and asana.where[g2] == s["sent"]
    notes = asana.tasks[g1]["notes"]
    assert notes.startswith(STAND_START) and "NEEDS YOU: decide a number" in notes and "best offer" in notes
    assert parse_card(notes)["outreach_id"] == o1                       # the card still reads as an email card
    assert lead_board(loaded, cfg, asana) == {"moved": 0, "updated": 0, "to_needs_me": 0, "to_answered": 0, "warm": 1}  # idempotent
    log_message(loaded, t1, "out", "Thanks Lise, I'll get back to you today.", external_id="x3", action="reply:manual")
    lead_board(loaded, cfg, asana)
    assert "WAITING ON THE AGENT" in asana.tasks[g1]["notes"] and asana.tasks[g1]["notes"].count(STAND_START) == 1
    assert asana.where[g1] == s["sent"]                                 # we answered, not the agent: back to "Sent · no answer yet" (Rodrigo, 2026-10-27)
    log_message(loaded, t1, "in", "Seller says no, he is going with another buyer.", external_id="x4", classification="NOT_INTERESTED", confidence=0.9)
    lead_board(loaded, cfg, asana)
    assert asana.where[g1] == answered and "CLOSED" in asana.tasks[g1]["notes"]


def test_lead_card_shows_the_drafted_answer(loaded, cfg, tmp_path, monkeypatch):
    """Rodrigo, 2026-10-26: 'in the card I do not see the answer that you drafted for her'."""
    from titan.approvals import post_reply_card
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s = _sections(cfg)
    g1 = list(asana.tasks)[0]
    asana.move(g1, s["approved"])
    send_approved(loaded, cfg, asana, FakeGmail(), now=SAT_10AM_ET)
    o1 = parse_card(asana.tasks[g1]["notes"])["outreach_id"]
    a1 = loaded.execute("SELECT agent_id FROM outreach WHERE id=?", (o1,)).fetchone()[0]
    t1 = loaded.execute("SELECT id FROM threads WHERE agent_id=?", (a1,)).fetchone()[0]
    log_message(loaded, t1, "in", "Bring your best offer.", external_id="y1", classification="ASK_FOR_OFFER", confidence=0.9)
    post_reply_card(loaded, cfg, asana, o1, "Hi Lise,\n\nPrice: $209,000, cash, as is.\n\nBest regards,\n\nCharles Grant")
    asana._sections = {s["sent"]: "Sent", **getattr(asana, "_sections", {})}
    lead_board(loaded, cfg, asana)
    notes = asana.tasks[g1]["notes"]
    assert "DRAFTED ANSWER (not sent yet)" in notes and "$209,000" in notes
    pc = parse_card(notes)
    assert pc["outreach_id"] == o1 and not pc["subject"].startswith("Re:") and "209,000" not in pc["body"]   # still the original card


def test_offer_card_changes_the_lead_status_and_cc_is_sent(loaded, cfg, tmp_path, monkeypatch):
    """Rodrigo, 2026-10-26: Lise's status still said 'decide a number' after the offer was drafted; and CC him on offers."""
    from titan.approvals import post_reply_card
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s = _sections(cfg)
    g1 = list(asana.tasks)[0]
    asana.move(g1, s["approved"])
    send_approved(loaded, cfg, asana, FakeGmail(), now=SAT_10AM_ET)
    o1 = parse_card(asana.tasks[g1]["notes"])["outreach_id"]
    a1 = loaded.execute("SELECT agent_id FROM outreach WHERE id=?", (o1,)).fetchone()[0]
    t1 = loaded.execute("SELECT id FROM threads WHERE agent_id=?", (a1,)).fetchone()[0]
    log_message(loaded, t1, "in", "Bring your best offer.", external_id="z1", classification="ASK_FOR_OFFER", confidence=0.9)
    gid = post_reply_card(loaded, cfg, asana, o1, "Hi Lise,\n\nPrice: $209,000, cash, as is.\n\nBest regards,\n\nCharles Grant")
    asana.tasks[gid]["notes"] = "Cc: rodrigo@example.com\n" + asana.tasks[gid]["notes"]
    asana._sections = {s["sent"]: "Sent", **getattr(asana, "_sections", {})}
    lead_board(loaded, cfg, asana)
    assert "OFFER DRAFTED ($209,000)" in asana.tasks[g1]["notes"]
    assert parse_card(asana.tasks[gid]["notes"])["cc"] == ["rodrigo@example.com"]
    sent = []
    class G(FakeGmail):
        def send(self, to, subject, body, **kw):
            sent.append(kw); return super().send(to, subject, body)
    asana.move(gid, s["approved"])
    send_approved(loaded, cfg, asana, G(), now=SAT_10AM_ET)
    assert sent and sent[-1].get("cc") == ["rodrigo@example.com"]


def test_warm_leads_board_gets_answered_and_hot_cards_in_the_right_column(loaded, cfg, tmp_path, monkeypatch):
    """Rodrigo, 2026-10-28: a second board for hot and in-contact leads; the engine sends them there (same card, two boards)."""
    from titan.conversations import warm_key
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s = _sections(cfg)
    wb = cfg["outreach"]["asana"]["warm_board"]
    g1, g2, g3 = list(asana.tasks)[:3]
    for g in (g1, g2, g3):
        asana.move(g, s["approved"])
    send_approved(loaded, cfg, asana, FakeGmail(), now=SAT_10AM_ET)
    oids = {g: parse_card(asana.tasks[g]["notes"])["outreach_id"] for g in (g1, g2, g3)}
    sent_gids = [g for g in (g1, g2, g3) if asana.where[g] == s["sent"]]
    assert len(sent_gids) >= 2                                          # per_tick is 2
    g1, g2 = sent_gids[:2]
    g3 = next(g for g in (g1, g2, g3) if g not in sent_gids) if len(sent_gids) < 3 else g3
    aid = {g: loaded.execute("SELECT agent_id FROM outreach WHERE id=?", (oids[g],)).fetchone()[0] for g in (g1, g2)}
    tid = {g: loaded.execute("SELECT id FROM threads WHERE agent_id=?", (aid[g],)).fetchone()[0] for g in (g1, g2)}
    log_message(loaded, tid[g1], "in", "Bring your best offer.", external_id="w1", classification="ASK_FOR_OFFER", confidence=0.9)
    log_message(loaded, tid[g2], "in", "Sure, what do you need to know?", external_id="w2", classification="INTERESTED", confidence=0.9)
    log_message(loaded, tid[g2], "out", "Thanks, is the roof original? Our number would be $209,000 as is.", external_id="w3", action="reply:manual")
    pid3 = json.loads(loaded.execute("SELECT property_ids FROM outreach WHERE id=?", (oids[g3],)).fetchone()[0])[0]
    db.set_lead_status(loaded, pid3, "NEEDS_CALL", "call tomorrow")          # no answer yet, but Rodrigo marked it
    loaded.execute("UPDATE outreach SET status='sent' WHERE id=?", (oids[g3],)); asana.move(g3, s["sent"])
    asana._sections = {s["sent"]: "Sent", **getattr(asana, "_sections", {})}
    res = lead_board(loaded, cfg, asana)
    assert res["warm"] == 3
    assert asana.warm[g1] == (wb["project"], wb["needs_me"])       # she asked for a number: his move
    assert asana.warm[g2] == (wb["project"], wb["offer_out"])      # we sent a figure and wait on the agent
    assert asana.warm[g3] == (wb["project"], wb["needs_me"])       # hot by his comment; stays in Sent on the main board
    assert asana.where[g3] == s["sent"]
    asana.comment_raw(g1, "ask her if the seller would take 30 days post occupancy")   # his comment, not answered yet
    lead_board(loaded, cfg, asana)
    assert asana.warm[g1] == (wb["project"], wb["needs_revision"])  # he can see the engine has his comment
    asana.comment(g1, "Revised based on your comment")
    lead_board(loaded, cfg, asana)
    assert asana.warm[g1] == (wb["project"], wb["needs_me"])         # answered: back to its status column
    assert warm_key("Status: CLOSED: not interested.", False) == "closed"
    assert warm_key("Status: WAITING ON THE AGENT since Mon.", False) == "waiting"
    assert warm_key("Status: OFFER APPROVED ($209,000): goes out next run.", False) == "offer_out"
