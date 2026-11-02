"""Rodrigo, 2026-11-02: clean cards. Agent footers off the mirrored replies, repeated engine comments deleted, a flat "no"
read as a decline."""
from titan.asana import BOT
from titan.replies.classifier import classify_rules
from titan.replies.clean import clean_reply, one_line
from titan.tidy import superseded, tidy_comments

RICHARD = ("Hi Charles,\n\nIt depends, what is the range you have in mind?\n\nBest regards,\n\nRichard M. DiPietro\nFL. Licensed Broker Associate\n"
           "License # BK3659026\n\nReal Broker, LLC\nonereal.com/richardreal\n\nP: 347-693-2995\nO: 855-450-0442")
EDWARD = "No they are not. Thank you. \nFeel free to text/call me, anytime.\n\nEdward A Martinez\n954-303-3032"
LISE = ("Hi Charles, it's a gut.  Needs a roof.\n\nI'd rather be transparent.\n\nOn Sat, Oct 24, 2026 at 12:44 PM Charles Grant <\n"
        "Charles@titanrealtyinvestments.com> wrote:\n\n> hello\n\n-- \n\n Lise Freeman\n561-907-0095")


def test_footers_and_quoted_history_are_dropped():
    assert clean_reply(RICHARD) == "It depends, what is the range you have in mind?"
    assert clean_reply(EDWARD) == "No they are not. Thank you.\nFeel free to text/call me, anytime."
    assert clean_reply(LISE) == "Hi Charles, it's a gut.  Needs a roof.\n\nI'd rather be transparent."
    assert clean_reply("Yes") == "Yes" and clean_reply("Sure, call me tomorrow.\n\nSent from my iPhone") == "Sure, call me tomorrow."
    assert one_line(RICHARD, 30) == "It depends, what is the range…"


def test_a_flat_no_is_a_decline():
    assert classify_rules(EDWARD, 339000).label == "NOT_INTERESTED"
    for t in ("Unfortunately the seller isn't interested in investor pricing.", "No, he is firm on the price", "Not at this time, thank you",
              "They won't come down. Call me anytime."):
        assert classify_rules(t, 300000).label == "NOT_INTERESTED", t
    assert classify_rules("They are not firm, make an offer", 300000).label == "ASK_FOR_OFFER"
    assert classify_rules("Yes, the seller is open to offers, call me", 300000).label == "INTERESTED"


def _c(gid, text):
    return {"gid": gid, "text": text}


def test_superseded_engine_comments_are_removed_but_never_rodrigos_or_the_last():
    comments = [_c("1", f"{BOT} Rewritten under the new outreach protocol."), _c("2", f"{BOT} Rewritten under the new outreach protocol."),
                _c("3", "Ask him if it is rented"), _c("4", f"{BOT} I couldn't rewrite this one automatically (no rewrite)."),
                _c("5", f"{BOT} Held: you left a comment after approving."), _c("6", f"{BOT} Revised based on your comment."),
                _c("7", f"{BOT} Sent Sun Oct 25, 03:41 PM from Charles@."), _c("8", f"{BOT} 📩 Richard replied · Mon Oct 26"),
                _c("9", f"{BOT} Comps re-checked (2026-10-28): ARV up."), _c("10", f"{BOT} Moved to Needs Me: the numbers are on this card."),
                _c("11", f"{BOT} Comps re-checked (2026-10-29): ARV down."), _c("12", "re check these comps"),
                _c("13", f"{BOT} Holding it: nothing sends from here.")]
    gone = [c["gid"] for c in superseded(comments)]
    assert gone == ["1", "4", "5", "9", "10"]        # older Rewritten, the two transients, yesterday's comps, the stale move

    class A:
        deleted = []
        def delete_story(self, gid): self.deleted.append(gid)
    a = A()
    assert tidy_comments(a, "t", comments) == 4 and a.deleted == ["1", "4", "5", "9"]     # bounded per round
    assert [c["gid"] for c in comments] == ["2", "3", "6", "7", "8", "10", "11", "12", "13"]
    assert superseded([_c("1", f"{BOT} Holding it."), _c("2", "my comment")]) == []          # the last engine comment always stays
