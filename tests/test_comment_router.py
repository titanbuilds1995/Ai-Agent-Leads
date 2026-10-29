"""Comments do what they ask (Rodrigo, 2026-10-26: 'why would it not give me what I am asking?'): numbers come back as a
snapshot in Lise's layout with the comps, ARV, exit and where we need to be, ON THE CARD HE ASKED ON and nowhere else
(Rodrigo, 2026-10-27, Richard: no second card); 'send it' sends the text as is; 'call him tomorrow' puts a call card on his
board; wording comments still rewrite."""
from titan import comps_report
from titan.approvals import parse_card, sync_feedback
from titan.comment_router import classify

from tests.test_approvals import FakeAsana, _cards, _sections

# His real comments on 2026-10-26 (Asana, Email Approvals board)
CARLTON = ("Hey for this property I need us to figure out before we email him what the numbers look like I want to see how far off "
           "or how close off he is from from where we need to be at and also look at the numbers for baba baba new construction and")
BALTHAZAR = ("Hey this is a really good email I want us to stay on top of this lead and Mark him as hot cuz I think this could be a hot "
             "potential so send him the email and follow up with him if you don't hear from him by the end of the day and then put it "
             "on my on my boards to call tomorrow after you send them the emails")
MONTAS = ("Hey this property is a cash flow property, so with what it generates we know more or less where we need to be, let's do the "
          "following I want you to comp this property and I want to see what the products in the area are selling for and where we "
          "woudl need to be at for this. I think we'll straight send him an offer or a range since it's really a cash cow..")
KARA = ("Hey I approve this email I actually really like it and what I want you to do is I want you to send it email and then give me "
        "this property back I want to look at the comparables in the area for what we could potentially who sell the land for and also "
        "let me know if homes like this are being renovated it seems like it has subfloors so do that research for me take a look at it "
        "if it does have subfloors that's an issue and if they are being renovated what's after a pair value of properties like this "
        "actually give that to me before we send him an email let's comp it")
FOLAND = ("Hey I didn't see that 28th Court is their listing so I want to just double check with that confirm on that and the precise "
          "with our communication we can't have that type of incongruency")
DIPIETRO = ("Hey two things here first of all one of the properties that we got from this guy was renovated the one in Riviera Beach, "
            "for the home that he's talking to us about the 2500 and 2000 a month that's $4,500 a month per for the property I want us "
            "to run comparables on that and see what could we who sell this property for what would be a good offer for them")
WORDING = "take out I'll keep this short, people don't say that. Just say we saw your new listing"


def test_his_six_comments_are_sorted_right():
    assert "numbers" in classify([CARLTON]) and "send" not in classify([CARLTON])
    b = classify([BALTHAZAR])
    assert {"send", "call"} <= b and "numbers" not in b and "rewrite" not in b
    assert "numbers" in classify([MONTAS])
    assert "numbers" in classify([KARA])           # "approve ... send it" loses to "before we send him an email let's comp it"
    f = classify([FOLAND])
    assert "check" in f and "numbers" not in f and "send" not in f      # checked listings go into the rewrite
    assert "numbers" in classify([DIPIETRO])
    assert classify([WORDING]) == {"rewrite"}
    assert classify(["looks good, send it"]) == {"send"}
    assert "rewrite" in classify(["send it but change the opening to Hey Mike"])


class HtmlAsana(FakeAsana):
    def __init__(self):
        super().__init__()
        self.html = {}

    def create_task_html(self, project, section, name, html_notes):
        r = self.create_task(project, section, name, "")
        self.html[r["gid"]] = html_notes
        return r

    def comment_html(self, task, html_text):
        assert html_text.startswith("<body>🤖")
        self.stories[task].append(html_text[len("<body>"):])


def _fake_comps(p):
    base = {"property_type": p.get("property_type"), "lat": p.get("lat"), "lng": p.get("lng"), "zip": p.get("zip")}
    sq = p.get("sqft") or 1500
    out = []
    for i, (price, cond) in enumerate([(410000, "Fully renovated, new kitchen"), (395000, "Remodeled 2024"), (420000, "Turnkey home"),
                                       (300000, "Sold as-is, needs work"), (330000, ""), (405000, "Renovated with pool"), (280000, "TLC, cash only")]):
        out.append({**base, "address": f"{100 + i} Test St", "sold_price": price, "sqft": sq, "sold_date": f"2026-0{i + 1}-15",
                    "remarks": cond, "pool": "pool" in cond.lower() or None, "mls": f"F100{i}"})
    return out


def _setup(loaded, cfg, tmp_path, monkeypatch, comment):
    asana0, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    asana = HtmlAsana()
    asana.tasks, asana.where, asana.stories, asana._ids = asana0.tasks, asana0.where, asana0.stories, asana0._ids
    gid = next(iter(asana.tasks))
    asana.stories[gid].append(comment)
    import titan.approvals as ap
    monkeypatch.setattr(ap, "_comps_fn", lambda cfg, conn, cache: _fake_comps)
    return asana, gid


class NoLLM:
    available = True

    def structured(self, *a, **k):
        raise AssertionError("a numbers / send / call comment must not trigger a rewrite")


def test_numbers_comment_posts_the_snapshot_and_holds_the_email(loaded, cfg, tmp_path, monkeypatch):
    asana, gid = _setup(loaded, cfg, tmp_path, monkeypatch, DIPIETRO)
    before = asana.tasks[gid]["notes"]
    n = sync_feedback(loaded, cfg, asana, NoLLM())
    assert n["numbers"] >= 1 and asana.tasks[gid]["notes"] == before          # text untouched
    assert asana.where[gid] != _sections(cfg)["approved"]                      # held, never sent
    assert asana.where[gid] == asana.ensure_section(None, "Needs Me")          # done, waiting on Rodrigo (2026-10-27)
    snap = [s for s in asana.stories[gid] if "NUMBERS" in s][0]
    assert "Where we need to be" in snap and "The comps" in snap and "ARV" in snap and "An investor pays" in snap and "Exit: resale after rehab" in snap
    assert not asana.html and not [t for t in asana.tasks.values() if t["name"].startswith("NUMBERS")]   # no second card anywhere
    assert "Claude Updates" not in asana.stories[gid][-1] and "Full snapshot" not in snap
    assert (tmp_path / "reports" / "comps").exists()
    # handled: the next round does not redo it
    assert sync_feedback(loaded, cfg, asana, NoLLM())["numbers"] == 0


def test_send_it_moves_to_approved_untouched_and_books_the_call(loaded, cfg, tmp_path, monkeypatch):
    asana, gid = _setup(loaded, cfg, tmp_path, monkeypatch, BALTHAZAR)
    before = asana.tasks[gid]["notes"]
    n = sync_feedback(loaded, cfg, asana, NoLLM())
    s = _sections(cfg)
    assert asana.where[gid] == s["approved"] and asana.tasks[gid]["notes"] == before and n["sent_as_is"] == 1
    calls = [t for g, t in asana.tasks.items() if t["name"].startswith("📞 Call")]
    assert len(calls) == 1 and asana.where[calls[0]["gid"]] == s["needs_rodrigo"]
    oid = parse_card(before)["outreach_id"]
    pid = loaded.execute("SELECT json_extract(property_ids,'$[0]') FROM outreach WHERE id=?", (oid,)).fetchone()[0]
    assert loaded.execute("SELECT lead_status FROM properties WHERE id=?", (pid,)).fetchone()[0] == "NEEDS_CALL"


def test_snapshot_math_and_compact_table(loaded, cfg, tmp_path, monkeypatch):
    cfg.root = tmp_path
    pid = loaded.execute("SELECT id FROM properties WHERE sqft > 0 AND property_type != 'land' AND lead_status != 'EXCLUDED' LIMIT 1").fetchone()[0]
    s = comps_report.build(loaded, cfg, pid, _fake_comps, asked="comp it, what's the ARV and the land value")
    assert 6 <= len(s["comps"]) <= 10
    n = s["numbers"]
    sq = s["p"]["sqft"]
    assert "renovated" in n["arv_basis"] and n["arv"] == round(407500 / sq * sq, -3)      # median of the 4 renovated sales (395k 405k 410k 420k)
    if n.get("exit") is not None:
        assert n["ours"] == n["flipper"] - n["fee"] and n["flipper"] == round(n["arv"] * n["ratio"] - n["repairs"], -3)
    assert any(k == "Lot sales nearby" for k, _ in s["extra"])                                  # he asked about land
    txt = comps_report.to_text(s)
    assert "Where we need to be" in txt and len(txt.split()) < 600                              # compact (Rodrigo, 2026-09-14)


def test_rent_is_read_from_the_agents_reply():
    assert comps_report.rent_from(["Polk Street unit A is rented for $2,500, unit B $2,000 monthly."]) == 4500
    assert comps_report.rent_from(["Beautiful home, no rent stated"]) is None


# A false send is the worst mistake: none of these may move a card to Approved (reviewer, 2026-10-26).
NEVER_SEND = [
    "don't send it yet", "do not send this", "hold off, don't send it", "not approved", "I don't approve this",
    "this is not a good email", "looks good but hold it", "why did you send this", "is this approved?", "good to go?",
    "looks good, I will call him myself, don't send", "send it after I talk to him", "send it Friday after 9", "send it tomorrow",
    "send him an offer at 250k", "let's send them 240", "send it but cc me", "send it and attach the photo",
    "send her the email in spanish", "send it to the other listing agent too", "good email. Is this a duplex?",
    "approved for now, but double check the address first", "don't follow up", "do not mark him hot", "ask a follow-up question",
    "send it when he calls back", "if he answers send it", "wait on this one", "hold it", "looks good, but comp it first",
    "good email but what's the ARV?", "send it once Lise answers", "never send this", "no need to send", "send it later",
    "send it next week", "good email, change the opening to Hey Mike", "send it, but say we saw the listing on Zillow",
    "did you send this?", "have you sent it?", "send it at 9am", "looks good. how far off is he from where we need to be",
    "approve after you comp it", "send it, also send it to his partner", "good email, mention the pool",
    # round 2 (reviewer)
    "send it, call him tomorrow and do not send the email until I talk to him", "looks good, follow up with him but hold the email",
    "send it after you send the other one, never mind hold it",
    "looks good send it, if you don't hear from him I'll handle it, scrap that, don't send", "looks good overall, a couple of tweaks",
    "good email, a few tweaks", "looks good just fix the typo", "looks good minus the last line", "looks good except the greeting",
    "looks good, swap Hey for Hi", "looks good, switch the subject line", "good email sign it from me", "looks good, use my signature",
    "put my name on it", "send it with Rodrigo's name", "send it to Lisa", "send to Lisa", "send it to the seller", "send it from rodrigo",
    "send this one to my personal email", "send it via text", "send it to every agent", "send them all", "looks good, wrong agent though",
    "looks good wrong address", "send it, it's pending now", "looks good, he is a jerk skip him", "I approve this. Skip", "send it - DNC",
    "send it, nope", "send it. lol jk", "ok send it lmao no",
    # round 3 (reviewer)
    "send it ❌", "send it 🛑", "send it 🚫", "send it ✋", "send it 🤔", "looks good 👎", "send it -", "send it, you",
    "Approved. Hold.", "✅ hold", "send it to rodrigo",
    # round 4 (reviewer)
    "send it actually", "send it. actually", "perfect", "great", "perfect, thank you", "thank you", "go",
]
PLAIN_SEND = ["send it", "looks good, send it", "approved", "Approved!", "good email", "ship it", "go ahead and send",
              "this is a really good email, send it", "yes send", "approve and send", "send", "👍", "I approve this email, I really like it, send it", BALTHAZAR,
              "approve", "send it rodrigo", "✅", "send it 👍"]


def test_no_false_sends():
    for c in NEVER_SEND:
        assert "send" not in classify([c]), c
    for c in PLAIN_SEND:
        assert "send" in classify([c]), c


def test_hold_comment_rewrites_nothing_and_parks_the_card_in_needs_me(loaded, cfg, tmp_path, monkeypatch):
    """A hold never touches the text and never sends. The card leaves Fresh Leads: he commented, so it is his now
    (Rodrigo, 2026-10-27)."""
    asana, gid = _setup(loaded, cfg, tmp_path, monkeypatch, "don't send it yet")
    before, where = asana.tasks[gid]["notes"], asana.where[gid]
    sync_feedback(loaded, cfg, asana, NoLLM())
    assert asana.tasks[gid]["notes"] == before and "Holding it" in asana.stories[gid][-1]
    assert where == _sections(cfg)["to_review"] and asana.where[gid] == asana.ensure_section(None, "Needs Me")


def test_failed_snapshot_holds_without_consuming_the_comment(loaded, cfg, tmp_path, monkeypatch):
    asana, gid = _setup(loaded, cfg, tmp_path, monkeypatch, CARLTON)
    def boom(*a, **k):
        raise RuntimeError("no network")
    monkeypatch.setattr(comps_report, "build", boom)
    sync_feedback(loaded, cfg, asana, NoLLM())
    assert asana.where[gid] != _sections(cfg)["approved"] and "Held: couldn't build the numbers" in asana.stories[gid][-1]
    sync_feedback(loaded, cfg, asana, NoLLM())                      # said once, not every tick
    assert sum("Held: couldn't build" in x for x in asana.stories[gid]) == 1
    from titan.approvals import pending_feedback
    assert pending_feedback(asana, gid)[0] == [CARLTON]             # still pending: retried next round


def _pid(loaded, where="sqft > 0 AND property_type != 'land'"):
    return loaded.execute(f"SELECT id FROM properties WHERE {where} AND lead_status != 'EXCLUDED' LIMIT 1").fetchone()[0]


def test_land_is_valued_from_lot_sales_not_house_math(loaded, cfg, tmp_path):
    cfg.root = tmp_path
    pid = _pid(loaded)
    loaded.execute("UPDATE properties SET property_type='land', lot_sqft=10000, sqft=NULL WHERE id=?", (pid,))
    loaded.execute("UPDATE valuations SET arv=2000000, arv_method='land_default_suspect' WHERE property_id=?", (pid,))

    def lots(p):
        return [{"address": f"{i} Lot Rd", "sold_price": 100000 + i * 10000, "lot_sqft": 10000, "property_type": "land",
                 "lat": p.get("lat"), "lng": p.get("lng"), "sold_date": "2026-05-01"} for i in range(6)]
    s = comps_report.build(loaded, cfg, pid, lots)
    n = s["numbers"]
    assert n["land"] and n["repairs"] == 0 and n["arv"] == 125000 and "lot sf" in n["arv_basis"]
    assert "2,000,000" not in comps_report.to_text(s)                       # the suspect stored value is never used


def test_missing_size_still_shows_the_nearest_sales(loaded, cfg, tmp_path):
    cfg.root = tmp_path
    pid = _pid(loaded)
    loaded.execute("UPDATE properties SET sqft=NULL WHERE id=?", (pid,))
    s = comps_report.build(loaded, cfg, pid, _fake_comps)
    assert len(s["comps"]) >= 6 and any("size (sq ft) is unknown" in x for x in s["still_open"])


def test_stored_comps_are_labeled_as_stored(loaded, cfg, tmp_path):
    cfg.root = tmp_path
    s = comps_report.build(loaded, cfg, _pid(loaded), None)
    assert "stored" in s["period"] and "stored" in s["source"]


def test_rent_from_his_comment_gives_cap_rate_prices(loaded, cfg, tmp_path):
    cfg.root = tmp_path
    s = comps_report.build(loaded, cfg, _pid(loaded), _fake_comps, asked=DIPIETRO)
    n = s["numbers"]
    assert n["rent"] == 4500 and n["noi"] == int(4500 * 12 * 0.6)
    assert dict(n["cap_prices"])[0.07] == round(4500 * 12 * 0.6 / 0.07, -3)
    assert "Price at a 7% cap" in comps_report.to_text(s)


def test_old_comps_are_dropped():
    from titan.comps_report import recent
    assert [c["a"] for c in recent([{"a": 1, "sold_date": "2020-01-01"}, {"a": 2, "sold_date": "2026-09-01"}, {"a": 3}])] == [2, 3]


def test_failed_numbers_retried_books_one_call_card(loaded, cfg, tmp_path, monkeypatch):
    asana, gid = _setup(loaded, cfg, tmp_path, monkeypatch, "mark him hot and call him tomorrow. comp it first")

    def boom(*a, **k):
        raise RuntimeError("no network")
    monkeypatch.setattr(comps_report, "build", boom)
    for _ in range(3):
        sync_feedback(loaded, cfg, asana, NoLLM())
    assert sum(t["name"].startswith("📞 Call") for t in asana.tasks.values()) == 1


def test_rent_reading_edge_cases():
    r = comps_report.rent_from
    assert r(["it rents for about $1,800"]) == 1800
    assert r(["taxes are $900 a month"]) is None
    assert r(["rent roll: $1,200 $1,200 $1,200"]) == 3600
    assert r(["unit A $2,000, unit B $2,000"]) == 4000


def test_headline_shows_cash_flow_when_rent_is_stated(loaded, cfg, tmp_path):
    cfg.root = tmp_path
    s = comps_report.build(loaded, cfg, _pid(loaded), _fake_comps, asked=DIPIETRO)
    h = comps_report.headline(s)
    assert "Flip math" in h and "Cash-flow math" in h


def test_hold_words_after_a_go_ahead_hold_not_rewrite():
    assert classify(["Approved. Hold."]) == {"hold"} and classify(["✅ hold"]) == {"hold"}


def test_a_new_call_comment_books_again(loaded, cfg, tmp_path, monkeypatch):
    from titan.comment_router import call_card
    asana, gid = _setup(loaded, cfg, tmp_path, monkeypatch, "x")
    card = parse_card(asana.tasks[gid]["notes"])
    assert call_card(loaded, cfg, asana, card, ["call him tomorrow"])
    assert not call_card(loaded, cfg, asana, card, ["call him tomorrow"])          # same comment retried
    assert call_card(loaded, cfg, asana, card, ["mark him hot, call him tomorrow again"])   # a new ask books again


def test_praise_alone_holds_without_a_rewrite():
    for c in ("perfect", "great", "perfect, thank you", "thank you", "go"):
        assert classify([c]) == {"hold"}, c


def test_same_sale_listed_twice_counts_once():
    """Carlton's card showed 1424 NE 16th Ave and 1533 NE 16th Ave twice each (MLS row + public-record row)."""
    rows = [{"address": "1424 NE 16th Ave, Fort Lauderdale, FL", "sold_price": 1170000, "mls": None},
            {"address": "1424 NE 16th Ave, Fort Lauderdale, FL 33304", "sold_price": 1170000, "mls": "B26063497"},
            {"address": "1533 NE 16th Ave, Fort Lauderdale", "sold_price": 2290000},
            {"address": "1533 NE 16th Ave, Fort Lauderdale", "sold_price": 2290000},
            {"address": "1533 NE 16th Ave, Fort Lauderdale", "sold_price": 1500000}]
    out = comps_report.dedupe(rows)
    assert len(out) == 3 and out[0]["mls"] == "B26063497"


def test_agents_rent_stays_on_the_house_his_thread_is_about(loaded, cfg, tmp_path):
    """Richard's $4,500 Polk St rent was applied to his Riviera Beach listing too: with two houses asked about, only the
    thread's own house gets the agent's rent."""
    import json
    from titan import db
    cfg.root = tmp_path
    pid = _pid(loaded)
    agent = loaded.execute("SELECT agent_id FROM properties WHERE id=?", (pid,)).fetchone()[0]
    other = loaded.execute("SELECT id FROM properties WHERE id != ?", (pid,)).fetchone()[0]
    loaded.execute("UPDATE properties SET agent_id=? WHERE id=?", (agent, other))
    now = db.now() if hasattr(db, "now") else "2026-10-26T12:00:00"
    cur = loaded.execute("INSERT INTO threads(agent_id, channel, external_id, property_ids, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                         (agent, "email", "t-rent", json.dumps([pid]), now, now))
    loaded.execute("INSERT INTO messages(thread_id, direction, body, created_at) VALUES (?,?,?,?)",
                   (cur.lastrowid, "in", "Unit A is rented for $2,500 and unit B for $2,000.", now))
    assert comps_report.build(loaded, cfg, pid, _fake_comps, rent_from_comment=False)["numbers"].get("rent") == 4500
    assert not comps_report.build(loaded, cfg, other, _fake_comps, rent_from_comment=False)["numbers"].get("rent")


def test_cards_with_numbers_already_posted_leave_needs_revision(loaded, cfg, tmp_path, monkeypatch):
    """Carlton, Montas, Kara, Dipietro sat in Needs Revision after their numbers were posted (Rodrigo, 2026-10-27:
    'move them to another column so I know we answered')."""
    from titan.asana import BOT
    asana, gid = _setup(loaded, cfg, tmp_path, monkeypatch, DIPIETRO)
    sync_feedback(loaded, cfg, asana, NoLLM())
    nr, nm = asana.ensure_section(None, "Needs Revision"), asana.ensure_section(None, "Needs Me")
    asana.move(gid, nr)                                                         # where the old engine left it
    n = sync_feedback(loaded, cfg, asana, NoLLM())
    assert n.get("to_needs_me") == 1 and asana.where[gid] == nm
    assert sync_feedback(loaded, cfg, asana, NoLLM())["numbers"] == 0 and asana.where[gid] == nm   # stays put, nothing redone


# Rodrigo, 2026-10-27: "I tell it what to write, it rewrites it ... if it can just send that would be great" (only when he says send)
SEND_AFTER = ["say we can close in 30 days and send it", "make it shorter and send it", "Mention the big lot, then send it.",
              "take out the second paragraph and send it out", "tell him we can be flexible on the move out date and send it please"]
NO_SEND_AFTER = ["make it shorter", "make it shorter and send it tomorrow", "say we can do 230k and send it", "send it, but cc me",
                 "make it shorter and send it to Lisa", "change the opener, don't send it", "make it shorter and send it?",
                 "make it shorter and send it after I talk to him", "send it actually", "mention the pool and send it lol jk",
                 "make it shorter. send it if he hasn't answered", "say $240,000 and send it", "make the offer 9 day inspection and send it"]


def test_send_after_rewrite_only_on_a_plain_send_with_a_writing_ask():
    for c in SEND_AFTER:
        w = classify([c])
        assert {"rewrite", "send_after"} <= w and "send" not in w, c
    for c in NO_SEND_AFTER:
        assert "send_after" not in classify([c]), c
    for c in NEVER_SEND + PLAIN_SEND:
        assert "send_after" not in classify([c]) or c in SEND_AFTER, c


class RewriteLLM:
    available = True

    def __init__(self, card):
        self.card = card

    def structured(self, purpose, system, user, schema, **k):
        return {"subject": self.card["subject"], "body": self.card["body"].replace("Hope you're doing well. ", "")}


def test_rewrite_then_send_goes_to_approved_and_out(loaded, cfg, tmp_path, monkeypatch):
    from tests.test_approvals import SAT_10AM_ET, FakeGmail
    from titan.approvals import send_approved
    asana, gid = _setup(loaded, cfg, tmp_path, monkeypatch, "make it shorter and send it")
    sync_feedback(loaded, cfg, asana, RewriteLLM(parse_card(asana.tasks[gid]["notes"])))
    assert asana.where[gid] == _sections(cfg)["approved"] and "You said send it" in asana.stories[gid][-1]
    gm = FakeGmail()
    assert send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 1 and len(gm.sent) == 1


def test_rewrite_without_send_goes_to_needs_me(loaded, cfg, tmp_path, monkeypatch):
    """Rodrigo, 2026-10-27: "once you finish something and send it to me, it never goes into To Review, it goes Needs Me.
    To Review is only properties I've not touched yet" (renamed Fresh Leads)."""
    asana, gid = _setup(loaded, cfg, tmp_path, monkeypatch, "make it shorter")
    sync_feedback(loaded, cfg, asana, RewriteLLM(parse_card(asana.tasks[gid]["notes"])))
    assert asana.where[gid] == asana.ensure_section(None, "Needs Me") and "Moved to Needs Me" in asana.stories[gid][-1]
    assert asana.where[gid] != _sections(cfg)["to_review"]


def test_numbers_question_on_a_lead_card_is_answered_there_and_makes_no_second_card(loaded, cfg, tmp_path, monkeypatch):
    """Rodrigo, 2026-10-27 (Richard Dipietro): 're check these comps ... give me a range' on the lead card made two NUMBERS
    cards on the Claude Updates board and put the numbers + answer on the Reply card. Now: everything on the card he wrote
    on, nothing created anywhere, the drafted answer untouched (held if it sat in Approved)."""
    from titan.approvals import post_reply_card, send_approved
    from tests.test_approvals import SAT_10AM_ET, FakeGmail
    asana, gid = _setup(loaded, cfg, tmp_path, monkeypatch, "")
    asana.stories[gid].clear()
    s = _sections(cfg)
    asana.move(gid, s["approved"])
    send_approved(loaded, cfg, asana, FakeGmail(), now=SAT_10AM_ET)
    oid = parse_card(asana.tasks[gid]["notes"])["outreach_id"]
    rgid = post_reply_card(loaded, cfg, asana, oid, "Hi Richard,\n\nThanks, working on it.\n\nBest regards,\n\nCharles Grant")
    asana.move(rgid, s["approved"])
    asana.stories[gid].append("I want us to re check these comps, i think they are super highly priced, some of the last comps we made "
                              "errors so let's take a close look at this and give me a range of where we need to be at for this property.")
    reply_before, cards_before = asana.tasks[rgid]["notes"], set(asana.tasks)

    class L:
        available = True
        def structured(self, purpose, system, user, schema, max_tokens=1024):
            assert purpose == "card_chat", purpose                       # no rewrite of anything
            return {"answer": "The $3.1M sale is not a comp; on the duplex sales the range is $400k to $470k.", "lead_note": "", "reply_change": ""}
    n = sync_feedback(loaded, cfg, asana, L())
    assert n["numbers"] >= 1 and n["chat"] == 1
    assert set(asana.tasks) == cards_before and not asana.html            # no NUMBERS card, no other card, anywhere
    lead = asana.stories[gid]
    assert any("NUMBERS" in c and "Where we need to be" in c for c in lead) and any("💬" in c and "$400k" in c for c in lead)
    assert asana.where[gid] == asana.ensure_section(None, "Needs Me")
    assert asana.tasks[rgid]["notes"] == reply_before and asana.where[rgid] != s["approved"]   # held, not sent, not rewritten
    assert not any("NUMBERS" in c or "💬" in c for c in asana.stories[rgid])                  # nothing landed on the other card
    assert sync_feedback(loaded, cfg, asana, L())["numbers"] == 0                             # done once


def test_multi_family_comps_drop_the_ppsf_outliers():
    """Richard Dipietro's Polk St duplex (2026-10-27): a $3.1M sale at $977/sf and a $1.23M at $512/sf sat next to
    1942 duplexes at $230-300/sf and pushed the ARV to $845k. Multi-family keeps the sales near the median $/sf."""
    rows = [{"address": a, "sold_price": pr, "sqft": sf} for a, pr, sf in [
        ("1745 Jefferson St", 3100000, 3172), ("2419 Johnson St", 590000, 2287), ("1633 Tyler St", 1230000, 2400),
        ("1826 McKinley St", 525000, 1962), ("2226 Taylor St", 573000, 2030), ("2535 Lincoln St", 990000, 3200),
        ("2459 Pierce St", 965000, 2960), ("2516 Cleveland St", 608000, 2113), ("1960 Rodman St", 572000, 2535),
        ("1931 Pierce St", 862500, 2850)]]
    kept = [c["address"] for c in comps_report.drop_ppsf_outliers(rows)]
    assert "1745 Jefferson St" not in kept and "1633 Tyler St" not in kept and len(kept) == 8
    assert comps_report.drop_ppsf_outliers(rows[:3]) == rows[:3]      # too few priced sales to judge: untouched


def test_numbers_trigger_inside_a_dictated_change_still_rewrites(loaded, cfg, tmp_path, monkeypatch):
    """Rodrigo, 2026-10-29 (Juan Huice): 'change the email and say something more direct like hey Juan … how are the subfloors
    doing' posted the numbers and left the email untouched."""
    from titan.comment_router import DICTATED_RE
    assert DICTATED_RE.search("Hey we need to change the email and say something more direct like hey Juan how are the subfloors doing")
    assert not DICTATED_RE.search(DIPIETRO) and not DICTATED_RE.search(CARLTON) and not DICTATED_RE.search(KARA)
    juan = "Hey we need to change the email and say something more direct like hey Juan how are the subfloors doing is the seller willing to negotiate at all?"
    asana, gid = _setup(loaded, cfg, tmp_path, monkeypatch, juan)
    before = asana.tasks[gid]["notes"]
    class LLM:
        available = True
        def structured(self, purpose, system, user, schema, **kw):
            return {"subject": "Quick question on the house", "body": "Hey Juan,\n\nCharles with Titan Realty Investments. I saw your listing on SW 2nd Pl.\n\nHow are the subfloors holding up, and is the seller willing to negotiate at all?\n\nBest regards,\n\nCharles Grant\nJunior Acquisitions Agent\nTitan Realty Investments\nOffice Line: +1 (561) 405 7379"}
    n = sync_feedback(loaded, cfg, asana, LLM())
    assert n["numbers"] >= 1 and asana.tasks[gid]["notes"] != before and "subfloors holding up" in asana.tasks[gid]["notes"]
    assert asana.where[gid] == asana.ensure_section(None, "Needs Me")
