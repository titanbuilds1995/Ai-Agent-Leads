"""Daily learning loop (Rodrigo, 2026-11-12): his skips / edits / approvals, the answers and the offers move the outreach
order and write the playbook; what is his call becomes a card; nothing here sends or touches a price."""
import json

from titan import learn_loop as ll
from titan.learning import APPROVED_AS_IS, SKIPPED, learned_block, record_decision
from titan.outreach.send import candidate_rows, ensure_thread, log_message
from titan.util import iso, now_utc
from datetime import timedelta


def _house(conn, i, city="Hialeah", price=300_000, lane="SITTING", agent=None):
    if agent is None:
        agent = conn.execute("INSERT INTO agents(name,name_key,brokerage,brokerage_key,email,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                             (f"Agent {i}", f"agent{i}", "Small Realty", "small", f"a{i}@x.com", iso(), iso())).lastrowid
    pid = conn.execute("INSERT INTO properties(address_key,street,city,county,list_price,days_on_market,property_type,photos_count,agent_id,"
                       "first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (f"k{i}", f"{i} Main St", city, "Miami-Dade", price, 90, "single_family", 20, agent, iso(), iso())).lastrowid
    conn.execute("INSERT INTO valuations(property_id,arv,mao,list_price,rating,lane,comp_grade,valued_at) VALUES(?,?,?,?,?,?,?,?)",
                 (pid, int(price * 1.3), int(price * 0.8), price, "HOT", lane, "B", iso()))
    conn.execute("INSERT INTO scores(property_id,score,scored_at) VALUES(?,?,?)", (pid, 70, iso()))
    return pid, agent


def _email(conn, i, decision=None, sent_days_ago=None, reply=None, **house):
    pid, agent = _house(conn, i, **house)
    sent_at = iso(now_utc() - timedelta(days=sent_days_ago)) if sent_days_ago is not None else None
    oid = conn.execute("INSERT INTO outreach(agent_id,property_ids,channel,kind,status,subject,body,variant,run_date,created_at,sent_at,asana_task_id) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (agent, json.dumps([pid]), "email", "first_touch", "sent" if sent_at else "pending_approval", "Quick question",
                        "Hey there,\n\nCharles with Titan Realty Investments. I saw your listing on Main St.\n\nHow old is the roof?",
                        "claude", "2026-11-01", iso(), sent_at, f"card{i}")).lastrowid
    if decision:
        record_decision(conn, f"card{i}", oid, "first_touch", decision, [], "body", "body")
    if reply:
        tid = ensure_thread(conn, agent, "email", f"th{i}", [pid])
        log_message(conn, tid, "in", "Sure, the seller is open. Roof is 2019.", f"m{i}", reply)
    return oid, pid


class Asana:
    def __init__(self, approved=()):
        self.created, self.comments, self.moves, self.approved = [], [], [], list(approved)
    def create_task(self, project, section, name, notes):
        self.created.append((section, name, notes))
        return {"gid": f"p{len(self.created)}"}
    def section_tasks(self, section):
        return [{"gid": g} for g in self.approved]
    def comment(self, gid, text):
        self.comments.append((gid, text))
    def move(self, gid, section):
        self.moves.append((gid, section))


class LLM:
    available = True
    def __init__(self, rules, keep=True, check_ok=True):
        self.rules, self.keep, self.check_ok, self.calls = rules, keep, check_ok, []
    def structured(self, purpose, system, user, schema, **kw):
        self.calls.append((purpose, user))
        if purpose == "learn_review":
            return {"summary": "He skips Miami Gardens and answers come from sitting houses.", "retire": [], "proposals": [],
                    "writer_rules": [{"rule": r, "applies_to": "first_email", "evidence": "seen on 4 cards", "evidence_count": 4} for r in self.rules]}
        if not self.check_ok:
            return None
        return {"verdicts": [{"index": i, "keep": self.keep, "reason": "fine"} for i in range(5)]}


def _history(conn):
    """10 Hialeah emails he approved (4 answered), 10 Miami Gardens emails he skipped."""
    for i in range(10):
        _email(conn, i, APPROVED_AS_IS, sent_days_ago=6, reply="INTERESTED" if i < 4 else None, city="Hialeah")
    for i in range(10, 20):
        _email(conn, i, SKIPPED, city="Miami Gardens")


def test_evidence_and_segments_read_decisions_replies_and_deletes(conn, cfg):
    _history(conn)
    oid, _ = _email(conn, 50, city="Doral")                       # no decision on file, but he deleted the card
    conn.execute("INSERT INTO board_cards(gid,first_seen,last_seen,gone_at,gone_how) VALUES('card50',?,?,?,'deleted')", (iso(), iso(), iso()))
    _email(conn, 51, APPROVED_AS_IS, sent_days_ago=5, reply="BOUNCE", city="Doral")      # a bounce is not an answer
    ev = ll.evidence(conn)
    by = {e["outreach_id"]: e for e in ev}
    assert by[oid]["decision"] == SKIPPED
    assert not [e for e in ev if e["city"] == "Doral" and e["replied"]]
    s = ll.segment_stats(ev)
    assert s["segments"][("city", "Hialeah")] == {"decided": 10, "skipped": 0, "as_is": 10, "sent": 10, "replied": 4, "positive": 4}
    assert s["segments"][("city", "Miami Gardens")]["skipped"] == 10


def test_weights_follow_his_votes_but_only_a_step_a_day_and_never_on_thin_data(conn, cfg):
    _history(conn)
    _email(conn, 60, SKIPPED, city="Doral")                        # one card: no weight
    w = ll.compute_weights(ll.segment_stats(ll.evidence(conn)), cfg)
    assert w["city=Hialeah"] == 0.1 and w["city=Miami Gardens"] == -0.1          # capped at max_step on day one
    assert "city=Doral" not in w
    w2 = ll.compute_weights(ll.segment_stats(ll.evidence(conn)), cfg, w)
    assert w2["city=Hialeah"] == 0.2 and -0.5 <= w2["city=Miami Gardens"] < -0.1


def test_run_reorders_outreach_inside_the_tier_and_keeps_hot_first(conn, cfg, tmp_path):
    cfg.root = tmp_path
    _history(conn)
    a, _ = _house(conn, 100, city="Miami Gardens")
    b, _ = _house(conn, 101, city="Hialeah")
    c, _ = _house(conn, 102, city="Hialeah", lane="WATCH")
    conn.execute("UPDATE scores SET score=95 WHERE property_id=?", (a,))            # the engine alone would put Miami Gardens first
    assert [p["id"] for p in candidate_rows(conn, cfg) if p["id"] in (a, b, c)] == [a, b, c]
    for day in ("2026-11-10", "2026-11-11"):
        out = ll.run(conn, cfg, llm=None, asana=None, run_date=day)
    assert out["weights"] and "city = Hialeah" in out["moves"]["up"]
    assert [p["id"] for p in candidate_rows(conn, cfg) if p["id"] in (a, b, c)] == [b, a, c]     # learned order, lane tier kept
    assert ll.run(conn, cfg, run_date="2026-11-11") == {"skipped": "already ran today"}
    cfg["learning_loop"]["apply_to_ranking"] = False
    assert [p["id"] for p in candidate_rows(conn, cfg) if p["id"] in (a, b, c)] == [a, b, c]


def test_playbook_needs_the_second_reader_and_never_touches_money_or_sending(conn, cfg, tmp_path):
    cfg.root = tmp_path
    _history(conn)
    good = "Ask about the roof age when the listing is over ten years old and says nothing about it."
    llm = LLM([good, "Offer $200,000 cash in the first email to get attention.", "When the agent is slow, send the follow-up automatically."])
    out = ll.run(conn, cfg, llm, asana=None, run_date="2026-11-10")
    assert out["rules_added"] == [good] and out["checked"]
    assert {d["rule"][:5] for d in out["rules_dropped"]} == {"Offer", "When "}
    assert "SKIPPED OR DELETED BY RODRIGO" in llm.calls[0][1] and "Miami Gardens" in llm.calls[0][1]
    assert good in learned_block(cfg) and "PLAYBOOK FROM OUR OWN RESULTS" in learned_block(cfg)
    # second reader says no, or cannot be reached: nothing is adopted
    for i, bad in enumerate((LLM(["Lead with the question about why the house is still sitting on the market."], keep=False),
                             LLM(["Name the street once in the subject and once in the first line only."], check_ok=False))):
        out = ll.run(conn, cfg, bad, asana=None, run_date=f"2026-11-1{i + 1}")
        assert out["rules_added"] == [] and len(out["rules_dropped"]) == 1
    assert len(ll.active_rules(conn)) == 1
    assert any("New rule for the writer" in x for x in ll.summary_lines(conn, cfg)) is False     # latest run added none
    assert any("Scorecard" in x for x in ll.summary_lines(conn, cfg))


def test_a_segment_he_always_skips_is_asked_once_and_applied_only_on_his_ok(conn, cfg, tmp_path):
    cfg.root = tmp_path
    _history(conn)
    asana = Asana()
    out = ll.run(conn, cfg, None, asana, run_date="2026-11-10")
    assert out["proposals_posted"] == ["Stop drafting first emails for houses with city = Miami Gardens?"]
    assert asana.created[0][0] == cfg["outreach"]["asana"]["needs_rodrigo"]
    mg, _ = _house(conn, 200, city="Miami Gardens")
    assert mg in [p["id"] for p in candidate_rows(conn, cfg)]                        # asking changes nothing
    ll.run(conn, cfg, None, asana, run_date="2026-11-11")
    assert len(asana.created) == 1                                                   # never asked twice
    asana.approved = ["p1"]                                                          # he dragged the card to Approved
    out = ll.run(conn, cfg, None, asana, run_date="2026-11-12")
    assert out["applied"] == ["city=Miami Gardens"] and asana.moves == [("p1", cfg["learning_loop"]["asana"]["done"])]
    assert mg not in [p["id"] for p in candidate_rows(conn, cfg)]
    assert any("Applied on your OK" in x for x in ll.summary_lines(conn, cfg))


def test_offers_far_from_the_engine_range_become_a_question_not_a_change(conn, cfg, tmp_path):
    cfg.root = tmp_path
    for i in range(5):
        pid, agent = _house(conn, 300 + i)
        conn.execute("INSERT INTO offers(property_id,agent_id,outreach_id,price,list_price,internal_low,internal_high,sent_at,updated_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", (pid, agent, 900 + i, 260_000, 300_000, 190_000, 220_000, iso(), iso()))
    asana = Asana()
    out = ll.run(conn, cfg, None, asana, run_date="2026-11-10")
    assert out["metrics"]["offer_vs_engine_high_pct"] == 118.2 and out["metrics"]["offer_pct_of_list"] == 86.7
    assert out["proposals_posted"] == ["Your offers run 18% above the top of the engine's range. Move the range?"]
    assert ll.load_prefs(cfg).get("blocked") in (None, [])
