"""Two agents on every answer (Rodrigo, 2026-11-12): "one answers, one reads the whole thing and reads the email we have and pushes
back on the actual logic and if it makes sense on what we're sending"."""
import json

from titan import second_read
from titan.approvals import parse_card
from titan.replies.writer import draft_reply_cards
from titan.util import iso
from tests.test_approvals import SAT_10AM_ET
from tests.test_reply_writer import GOOD, _setup, _LLM

BAD = "Hi Maria,\n\nThanks, that helps a lot.\n\nHow old is the roof, and is the seller living there now?"
SIG = "Charles Grant\nJunior Acquisitions Agent\nTitan Realty Investments\nOffice Line: +1 (561) 405 7379"


class Reader:
    """A writer (delegate) plus a scripted second reader: one verdict per read, the last one repeats."""
    available = True

    def __init__(self, writer, verdicts):
        self.writer, self.verdicts, self.reads, self.rewrites = writer, list(verdicts), [], []

    def structured(self, purpose, system, user, schema, **kw):
        if purpose == "second_read":
            self.reads.append(json.loads(user))
            v = self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]
            return v
        if "second_reader_pushback" in user or "second reader" in user:
            self.rewrites.append(user)
        return self.writer(purpose, user)


def SEND():
    return {"verdict": "SEND", "problems": [], "fix": "", "why": "It answers her and asks the one open thing."}


def FIX(p="Asks how old the roof is; she already said 2015 in her last message."):
    return {"verdict": "FIX", "problems": [p], "fix": "Drop the roof question; ask about occupancy.", "why": p}


def HOLD(p="He asked for a call; an email asking for the books is the wrong move."):
    return {"verdict": "HOLD", "problems": [p], "fix": "", "why": p}


def test_loop_rewrites_until_the_reader_agrees_and_stops_after_max_rounds(cfg):
    cfg["outreach"]["second_read"].update(enabled=True, max_rounds=2)
    drafts = []
    llm = Reader(None, [FIX(), SEND()])
    out, rv = second_read.run(llm, cfg, {"thread": []}, "v1", lambda pb: drafts.append(pb) or "v2")
    assert out == "v2" and rv["verdict"] == "SEND" and rv["rounds"] == 1 and "roof" in drafts[0][0] and second_read.passed(rv)
    assert "agrees after 1 rewrite" in second_read.line(rv) and "roof" in second_read.line(rv)
    # never satisfied: two rewrites, then the objection stands and it does not pass
    llm = Reader(None, [FIX()])
    out, rv = second_read.run(llm, cfg, {"thread": []}, "v1", lambda pb: "again")
    assert rv["verdict"] == "FIX" and rv["rounds"] == 2 and len(llm.reads) == 3 and not second_read.passed(rv)
    assert "still objects after 2 rewrites" in second_read.line(rv)
    # a fixed text is read once; an unusable answer from the reader = not read = does not pass
    assert second_read.run(Reader(None, [HOLD()]), cfg, {}, "fixed")[1]["verdict"] == "HOLD"
    out, rv = second_read.run(Reader(None, [{"verdict": "FITS"}]), cfg, {}, "x", lambda pb: "y")
    assert out == "x" and rv["verdict"] is None and not second_read.passed(rv) and "not done" in second_read.line(rv)
    cfg["outreach"]["second_read"]["enabled"] = False
    assert second_read.run(llm, cfg, {}, "x") == ("x", None) and second_read.passed(None)


def test_house_reply_is_read_against_the_whole_thread_and_rewritten(loaded, cfg, tmp_path, monkeypatch):
    cfg["outreach"]["second_read"]["enabled"] = True
    asana, s, oid = _setup(loaded, cfg, tmp_path, monkeypatch)

    def writer(purpose, user):
        return {"read": "x", "body": (GOOD if "second_reader_pushback" in user else BAD) + "\n\n" + _LLM.sig}
    llm = Reader(writer, [FIX(), SEND()])
    out = draft_reply_cards(loaded, cfg, asana, llm)
    assert out["drafted"] == 1 and out["second_read"] == 1 and "second_read_objects" not in out
    # the reader saw both sides of the conversation and the draft it was judging
    case = llm.reads[0]
    assert {m["from"] for m in case["thread"]} == {"agent", "Charles"} and "Roof is from 2015" in json.dumps(case["thread"])
    assert "How old is the roof" in case["draft_to_check"]
    # the writer got the pushback, and what is on the card is the rewrite, with the reader's line in the comment
    assert len(llm.rewrites) == 1 and "already said 2015" in llm.rewrites[0]
    gid = loaded.execute("SELECT asana_task_id FROM outreach WHERE id=?", (oid,)).fetchone()[0]
    assert "How old is the roof" not in parse_card(asana.tasks[gid]["notes"])["body"]
    assert any("Second read: agrees after 1 rewrite" in c for c in asana.stories[gid])


def test_house_reply_the_reader_still_doubts_says_so_on_the_card(loaded, cfg, tmp_path, monkeypatch):
    cfg["outreach"]["second_read"]["enabled"] = True
    asana, s, oid = _setup(loaded, cfg, tmp_path, monkeypatch)
    llm = Reader(lambda purpose, user: {"read": "x", "body": BAD + "\n\n" + _LLM.sig}, [FIX()])
    out = draft_reply_cards(loaded, cfg, asana, llm)
    assert out["drafted"] == 1 and out["second_read_objects"] == 1
    gid = loaded.execute("SELECT asana_task_id FROM outreach WHERE id=?", (oid,)).fetchone()[0]
    assert asana.where[gid] == asana.ensure_section(None, "Needs Me")
    assert any("Second read still objects after 2 rewrites" in c and "already said 2015" in c for c in asana.stories[gid])


def _om(conn, cfg, tmp_path):
    from titan.commercial.outreach import send_om_requests
    from tests.test_commercial import FakeOMGmail, _om_board
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    cfg.secrets["REPORT_TO"] = "rodrigo@titanrealtyinvestments.com"
    gm = FakeOMGmail()
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    conn.execute("UPDATE commercial_threads SET sent_at=?", (iso(SAT_10AM_ET),))
    return asana, gm, gid


def _read(asana, gid):
    return [c for c in asana.stories[gid] if "READ FOR YOU" in c][-1]


def _review(nda=False):
    return {"verdict": "CAN'T TELL YET", "what_they_said": "Adam has a question.", "books_say": "No documents yet.", "numbers": [], "red_flags": [],
            "needs_rodrigo": False, "next_step": "Charles answers.", "other_deals": [],
            "reply_draft": "Hey Adam,\n\nThanks. Can you send the rent roll?\n\n" + SIG}


def test_broker_reply_the_reader_stops_waits_for_rodrigo_and_a_cleared_one_goes(conn, cfg, tmp_path):
    from titan.commercial.books_review import review_pending, send_approved_replies
    from tests.test_commercial import _answer
    cfg["outreach"]["second_read"]["enabled"] = True
    asana, gm, gid = _om(conn, cfg, tmp_path)
    sent0 = len(gm.sent)

    def writer(purpose, user):
        if purpose == "commercial_reply_rewrite":
            return {"reply": "Hey Adam,\n\nThanks for the rent roll. Is the tenant on a triple net lease?\n\n" + SIG}
        return _review()
    # 1) the reader pushes back, Charles rewrites, the reader agrees: the rewrite is on the card and goes on its own
    _answer(conn, cfg, asana, gm, "The rent roll is in my last email. What is your question on the lease?", "r1", 1)
    llm = Reader(writer, [FIX("Asks for the rent roll; he says he already sent it."), SEND()])
    review_pending(conn, cfg, asana, llm)
    read = _read(asana, gid)
    assert "Second read: agrees after 1 rewrite" in read and "triple net" in read and "Can you send the rent roll?" not in read
    assert "goes out at the next round on its own" in read
    case = llm.reads[0]
    assert [m["from"] for m in case["thread"]][0].startswith("Charles (our first email") and case["thread"][-1]["from"] == "broker"
    assert send_approved_replies(conn, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 1 and "triple net" in gm.sent[-1][2]
    # 2) the reader says HOLD: the draft stays on the card, marked for Rodrigo, and does not go until he says so
    conn.execute("DELETE FROM outreach WHERE kind='om_reply'")          # (so the once-per-thread rule is not what holds it)
    _answer(conn, cfg, asana, gm, "What is your question on the roof, exactly?", "r2", 2)
    review_pending(conn, cfg, asana, Reader(writer, [HOLD("His question needs a number only Rodrigo can give.")]))
    read = _read(asana, gid)
    assert "Second read says HOLD" in read and "NEEDS YOU" in read and "not sent; comment 'send it'" in read
    assert send_approved_replies(conn, cfg, asana, gm, now=SAT_10AM_ET)["waiting"] == 1 and len(gm.sent) == sent0 + 1
    asana.comment_raw(gid, "send it")
    assert send_approved_replies(conn, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 1


def test_fixed_reply_that_does_not_fit_the_thread_is_held(conn, cfg, tmp_path):
    """The fixed "can you please send us the books" is Rodrigo's wording, so it cannot be rewritten: the reader only says
    whether it is the right email for this broker now."""
    from titan.commercial.books_review import review_pending, send_approved_replies
    from tests.test_commercial import _answer
    cfg["outreach"]["second_read"]["enabled"] = True
    asana, gm, gid = _om(conn, cfg, tmp_path)
    sent0 = len(gm.sent)
    _answer(conn, cfg, asana, gm, "Thanks for reaching out. It is a strong asset.", "r1", 1)
    llm = Reader(lambda purpose, user: {**_review(), "reply_draft": ""}, [FIX("He said nothing about the books; fine, but he is not the listing broker.")])
    out = review_pending(conn, cfg, asana, llm)
    assert out["needs_rodrigo"] == 1 and "fixed_text" in llm.reads[0] and not llm.rewrites
    read = _read(asana, gid)
    assert "Second read says HOLD" in read and "not sent; comment 'send it'" in read
    assert send_approved_replies(conn, cfg, asana, gm, now=SAT_10AM_ET)["waiting"] == 1 and len(gm.sent) == sent0
