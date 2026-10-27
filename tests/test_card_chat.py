"""Chat on the cards (Rodrigo, 2026-10-27: "I asked for input and got nothing back"): a question gets an answer, input about a
lead is kept for every later draft, a change on a lead card reaches the drafted answer, nothing moves or sends."""
import json

from titan.approvals import BOT, parse_card, post_reply_card, send_approved, sync_feedback
from titan.card_chat import FAILED, FORWARD_PREFIX, is_question, lead_notes
from titan.outreach.send import log_message
from tests.test_approvals import SAT_10AM_ET, FakeAsana, FakeGmail, _cards, _sections


class ChatLLM:
    available = True

    def __init__(self, out):
        self.out, self.calls = out, []

    def structured(self, purpose, system, user, schema, **k):
        self.calls.append((purpose, json.loads(user)))
        return self.out if purpose == "card_chat" else None


class DownLLM:
    available, last_error = False, None


def test_questions_are_told_apart():
    assert is_question(["can you be more clear on the rehab number?"]) and is_question(["why is the ARV so low"])
    assert is_question(["explain the lane"]) and not is_question(["don't send it yet"])


def test_a_question_on_a_to_review_card_is_answered_and_nothing_moves(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    gid = next(iter(asana.tasks))
    before, where = asana.tasks[gid]["notes"], asana.where[gid]
    asana.stories[gid].append("is this approved?")
    llm = ChatLLM({"answer": "Not yet: it sits in To Review.", "lead_note": "", "reply_change": ""})
    sync_feedback(loaded, cfg, asana, llm)
    last = asana.stories[gid][-1]
    assert last.startswith(f"{BOT} 💬 Not yet") and "Holding it" in last
    assert asana.tasks[gid]["notes"] == before and asana.where[gid] == where
    ctx = llm.calls[0][1]
    assert ctx["rodrigo_says"] == ["is this approved?"] and ctx["properties"] and "card_text" in ctx
    sync_feedback(loaded, cfg, asana, llm)
    assert len(llm.calls) == 1                                    # answered once, not every round


def _sent_lead(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s = _sections(cfg)
    g1 = list(asana.tasks)[0]
    asana.move(g1, s["approved"])
    send_approved(loaded, cfg, asana, FakeGmail(), now=SAT_10AM_ET)
    o1 = parse_card(asana.tasks[g1]["notes"])["outreach_id"]
    a1 = loaded.execute("SELECT agent_id FROM outreach WHERE id=?", (o1,)).fetchone()[0]
    t1 = loaded.execute("SELECT id FROM threads WHERE agent_id=?", (a1,)).fetchone()[0]
    log_message(loaded, t1, "in", "Bring your best offer.", external_id="c1", classification="ASK_FOR_OFFER", confidence=0.9)
    asana._sections = {s["sent"]: "Sent", **getattr(asana, "_sections", {})}
    return asana, g1, o1, a1


def test_input_on_a_lead_card_is_answered_kept_and_passed_to_the_drafted_answer(loaded, cfg, tmp_path, monkeypatch):
    asana, g1, o1, a1 = _sent_lead(loaded, cfg, tmp_path, monkeypatch)
    reply = post_reply_card(loaded, cfg, asana, o1, "Hi Lise,\n\nWorking on it, back to you today.\n\nCharles")
    asana.stories[g1].append("I spoke with her, the seller inherited it and wants out fast. Make the answer mention a 30 day close")
    llm = ChatLLM({"answer": "Got it: an inherited house and a seller in a hurry.", "lead_note": "Seller inherited it and wants out fast.",
                   "reply_change": "mention a 30 day close"})
    n = sync_feedback(loaded, cfg, asana, llm)
    assert n["chat"] == 1 and asana.stories[g1][-1].startswith(f"{BOT} 💬 Got it")
    assert lead_notes(loaded, a1) == ["Seller inherited it and wants out fast."]
    rgid = reply["gid"] if isinstance(reply, dict) else reply
    assert any(x.startswith(FORWARD_PREFIX) for x in asana.stories[rgid])        # read as his feedback on the reply card
    assert any(p == "revise_email" for p, _ in llm.calls)                        # and the reply card was rewritten from it


def test_claude_unreachable_holds_once_and_keeps_the_comment(loaded, cfg, tmp_path, monkeypatch):
    asana, g1, *_ = _sent_lead(loaded, cfg, tmp_path, monkeypatch)
    asana.stories[g1].append("what do you think about this one?")
    sync_feedback(loaded, cfg, asana, DownLLM())
    sync_feedback(loaded, cfg, asana, DownLLM())
    assert sum(FAILED in x for x in asana.stories[g1]) == 1
    llm = ChatLLM({"answer": "Worth a call.", "lead_note": "", "reply_change": ""})
    sync_feedback(loaded, cfg, asana, llm)
    assert asana.stories[g1][-1].startswith(f"{BOT} 💬 Worth a call")


def test_a_question_that_also_rewrites_gets_a_chat_answer(loaded, cfg, tmp_path, monkeypatch):
    """Rodrigo, 2026-10-27 (David Carlton / Alex Castro): 'take a look and tell me what you're seeing' got a new email and no answer."""
    from tests.test_approvals import _cards
    from titan.approvals import parse_card, sync_feedback
    from titan.card_chat import is_question
    assert is_question(["Take a look at it and tell me what you're seeing that i am not"])
    assert is_question(["knowing this data give me an analysis and feedback on where the price should be"])
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    gid = next(iter(asana.tasks))
    card = parse_card(asana.tasks[gid]["notes"])
    asana.stories[gid].append("Something's off with this one, it's priced cheap and not selling. Take a look and tell me what you're seeing, and make the email more direct.")

    class L:
        available = True
        def structured(self, purpose, system, user, schema, max_tokens=1024):
            if purpose == "card_chat":
                return {"answer": "The comps are townhouses, not houses; the ask is market for that cluster.", "lead_note": "", "reply_change": ""}
            return {"subject": card["subject"], "body": card["body"]}
    res = sync_feedback(loaded, cfg, asana, L())
    assert res["revised"] == 1
    assert any("💬" in c and "townhouses" in c for c in asana.stories[gid])
