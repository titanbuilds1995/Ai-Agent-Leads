"""End-of-day report (Rodrigo, 2026-10-29): who he moved off Fresh Leads, what he deleted, calls (best guess), emails,
replies, numbers, and where he stands against 25 leads a day."""
from datetime import datetime, timezone

from titan import end_of_day as eod
from titan.end_of_day import CALLED, COMMENTED, ELIMINATED, PROBABLY_CALLED, collect, due, maybe_send, track_board

FRESH, SKIP, NEEDS_ME, APPROVED = "1219847260413153", "1219846985445292", "nm1", "1219847113107056"
NAMES = {FRESH: "Fresh Leads", SKIP: "Skip", NEEDS_ME: "Needs Me", APPROVED: "Approved"}
DAY = "2026-10-29"
T = "2026-10-29T{}:00.000Z"        # 14:xx UTC = 10:xx am ET


class FakeAsana:
    def __init__(self):
        self.cards: dict[str, dict] = {}
        self.history: dict[str, list] = {}
        self.deleted: set[str] = set()
        self.needs_rodrigo: list[dict] = []

    def add(self, gid, name, section, modified=T.format("15:00")):
        self.cards[gid] = {"gid": gid, "name": name, "completed": False, "modified_at": modified,
                           "memberships": [{"project": {"gid": "1219847112999805"}, "section": {"gid": section, "name": NAMES[section]}}]}

    def move(self, gid, section, at, by_hand=True):
        old = self.cards[gid]["memberships"][0]["section"]
        self.history.setdefault(gid, []).append({"resource_subtype": "section_changed", "type": "system", "created_at": at,
                                                 "old_section": dict(old), "new_section": {"gid": section, "name": NAMES[section]}})
        self.cards[gid]["memberships"][0]["section"] = {"gid": section, "name": NAMES[section]}

    def say(self, gid, text, at):
        self.history.setdefault(gid, []).append({"type": "comment", "resource_subtype": "comment_added", "text": text, "created_at": at})

    def delete(self, gid):
        self.cards.pop(gid)
        self.deleted.add(gid)

    def project_tasks(self, project, completed_since="now"):
        return [dict(c) for c in self.cards.values()]

    def stories(self, gid):
        return self.history.get(gid, [])

    def task_state(self, gid):
        return None if gid in self.deleted else {"completed": True, "memberships": []}

    def section_tasks(self, section):
        return self.needs_rodrigo


def board():
    a = FakeAsana()
    for gid, name in (("c1", "Ana Ruiz · 1 Oak St"), ("c2", "Bo Lee · 2 Elm St"), ("c3", "Cy Diaz · 3 Pine St"),
                      ("c4", "Di Fox · 4 Bay St"), ("c5", "Ed Kim · 5 Sea St"), ("c6", "Flo Ng · 6 Sun St")):
        a.add(gid, name, FRESH)
    return a


def test_moves_off_fresh_leads_are_read_as_calls_or_eliminations(conn, cfg):
    a = board()
    track_board(conn, cfg, a)                              # baseline (the first look records, never reports)
    a.move("c1", NEEDS_ME, T.format("14:05"))              # looked, moved, no comment: he called
    a.move("c2", SKIP, T.format("14:10"))                  # eliminated
    a.move("c3", NEEDS_ME, T.format("14:20")); a.say("c3", "Spoke to her, seller wants 30 days", T.format("14:19"))
    a.move("c4", NEEDS_ME, T.format("14:30")); a.say("c4", "make it shorter", T.format("14:29"))
    a.delete("c5")
    track_board(conn, cfg, a)
    conn.execute("UPDATE board_cards SET gone_at=? WHERE gid='c5'", (T.format("14:40").replace(".000", ""),))
    d = collect(conn, cfg, a, DAY)
    got = {m["gid"]: m["verdict"] for m in d["moves"]}
    assert got == {"c1": PROBABLY_CALLED, "c2": ELIMINATED, "c3": CALLED, "c4": COMMENTED}
    assert [x["gid"] for x in d["deleted"]] == ["c5"] and d["deleted"][0]["from"] == "Fresh Leads"
    c = d["counts"]
    assert c["talked"] == 2 and c["eliminated"] == 2 and c["off_fresh"] == 5 and c["touched"] == 2 and c["comments"] == 2
    assert d["board"] == {"Fresh Leads": 1, "Needs Me": 3, "Skip": 1}


def test_an_agent_emailed_and_called_counts_once_toward_the_goal(loaded, cfg):
    a = board()
    track_board(loaded, cfg, a)
    agent = loaded.execute("SELECT id FROM agents LIMIT 1").fetchone()[0]
    for i, gid in enumerate(("c1", "c9")):
        loaded.execute("INSERT INTO outreach(agent_id,property_ids,channel,kind,status,to_address,run_date,created_at,sent_at,asana_task_id) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?)", (agent if gid == "c1" else None, "[]", "email", "first_touch", "sent",
                                                       f"x{i}@a.com", DAY, T.format("13:00"), f"2026-10-29T15:0{i}:00Z", gid))
    a.move("c1", NEEDS_ME, T.format("16:00"))
    d = collect(loaded, cfg, a, DAY)
    assert d["counts"]["emailed"] == 2 and d["counts"]["talked"] == 0 and d["counts"]["touched"] == 2
    assert d["history"][0]["touched"] == 2 and len(d["history"]) == 7


def test_autopilot_moves_and_out_and_back_are_not_his(conn, cfg):
    a = board()
    a.move("c1", APPROVED, T.format("14:00")); a.say("c1", "🤖 Autopilot: no comments for 3 hours", T.format("14:00"))
    a.move("c2", NEEDS_ME, T.format("14:00")); a.move("c2", FRESH, T.format("14:05"))
    assert collect(conn, cfg, a, DAY)["moves"] == []


def test_the_email_goes_once_after_7pm_and_shows_the_goal(conn, cfg, monkeypatch, tmp_path):
    cfg.root = tmp_path
    monkeypatch.setitem(cfg.secrets, "REPORT_TO", "rodrigo@example.com")
    a = board()
    track_board(conn, cfg, a, now=datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
    a.move("c2", SKIP, T.format("14:10"))
    sent = []

    class G:
        def send(self, to, subject, body, **kw):
            sent.append((subject, body, kw.get("html")))
            return {}
    at = lambda h: datetime(2026, 10, 29, h, 5, tzinfo=timezone.utc)  # noqa: E731
    assert maybe_send(conn, cfg, G(), a, now=at(21)) is None          # 5pm ET: not yet
    r = maybe_send(conn, cfg, G(), a, now=at(23))                     # 7:05pm ET
    assert r["status"] == "emailed" and r["eliminated"] == 1
    assert maybe_send(conn, cfg, G(), a, now=at(23)) is None          # once a day
    subject, body, html = sent[0]
    assert subject == "Titan end of day Thu Oct 29 · 0 of 25 leads"
    assert "Goal: 0 of 25" in body and "Eliminated" in body
    for part in ("End of Day", "of 25 leads", "Who you moved", "Last 7 days", "#0C1C35", "Eliminated"):
        assert part in html, part


def test_the_morning_catches_up_when_the_mac_slept(conn, cfg):
    a = board()
    track_board(conn, cfg, a, now=datetime(2026, 10, 29, 13, tzinfo=timezone.utc))
    assert due(conn, cfg, now=datetime(2026, 10, 30, 10, tzinfo=timezone.utc)) == DAY     # 6am ET the next day
    assert due(conn, cfg, now=datetime(2026, 10, 30, 18, tzinfo=timezone.utc)) is None    # 2pm ET: too late to catch up


def test_a_report_sent_by_hand_does_not_stop_the_evening_one(conn, cfg, monkeypatch, tmp_path):
    cfg.root = tmp_path
    monkeypatch.setitem(cfg.secrets, "REPORT_TO", "rodrigo@example.com")

    class G:
        def send(self, *a, **k):
            return {}
    eod.run(conn, cfg, G(), None, DAY, by_hand=True)
    assert due(conn, cfg, now=datetime(2026, 10, 29, 23, 30, tzinfo=timezone.utc)) == DAY


def test_a_vanished_listing_is_not_read_as_mass_deletion(conn, cfg):
    a = board()
    track_board(conn, cfg, a)
    a.cards.clear()
    track_board(conn, cfg, a)
    assert conn.execute("SELECT COUNT(*) FROM board_cards WHERE gone_how='deleted'").fetchone()[0] == 0
