"""END OF DAY REPORT (Rodrigo, 2026-10-29): "at the end of each day send me a report with all of the activity that we had
during that day on the leads, leads that I deleted, the ones that I called ... what's most important is who I moved from
the fresh leads board, all of the people that I moved are because I looked at those properties and either called them or
eliminated them ... and where I am at with my goal: 25 leads a day, emailing or talking to 25 a day."

Where each number comes from:
- Moves: Asana keeps a "moved this task from X to Y" story on every card, with the time. Every card touched today is read,
  so moves made before this was installed (or while the Mac slept) still count.
- Deletes: a deleted card leaves no story, so `track_board` (every check-replies / approvals run) remembers where every card
  sat; a card that vanished and answers 404 was deleted, from the column it was last seen in.
- Calls: Aircall is not connected, so a call is read from what he does on the board: a card he took off Fresh Leads into a
  working column without a comment (his habit: he looked, he called), a comment that says he called / spoke / left a
  voicemail, a 📞 call card. Shown as "best guess" and counted apart from emails.
- Emails, replies, offers: the engine's own database.
Goal = distinct agents emailed or talked to today (an agent emailed and called counts once). Eliminated cards are shown
but don't count: nobody was contacted.

Sent by the first check-replies run after `report.end_of_day.hour_et` (7pm ET), so the Mac needs no new cron line; the
the first run after the Mac wakes catches up if it slept through the evening. `make eod-report` sends one now."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from . import db
from .asana import BOT
from .util import ET, et_day_bounds, iso, log, money, now_utc, parse_iso

STEP = "eod_report"
CALLED_RE = re.compile(r"\b(called|spoke|spoken|talked|voice ?mail|left (?:a |him a |her a |them a )?(?:vm|message|msg)|"
                       r"on the phone|phone call|got (?:him|her|them) on)\b", re.I)
NUMBERS_PREFIX = f"{BOT} NUMBERS"
AUTOPILOT = f"{BOT} Autopilot:"

ELIMINATED, APPROVED, CALLED, PROBABLY_CALLED, COMMENTED = (
    "Eliminated", "Approved to send", "Called (your comment)", "Probably called", "Reviewed, asked for changes")


def _cfg(cfg: Any) -> dict[str, Any]:
    return cfg.get("report.end_of_day", {}) or {}


def goal(cfg: Any) -> int:
    return int(_cfg(cfg).get("daily_goal", 25))


def _a(cfg: Any) -> dict[str, Any]:
    return cfg.get("outreach.asana", {}) or {}


def _section_of(t: dict[str, Any], project: str) -> tuple[str | None, str]:
    for m in t.get("memberships") or []:
        if ((m.get("project") or {}).get("gid") in (project, None)) and m.get("section"):
            return m["section"].get("gid"), m["section"].get("name") or ""
    return None, ""


def _kind(section_name: str) -> str:
    n = (section_name or "").strip().lower()
    if n.startswith("skip"):
        return "skip"
    if n.startswith("approved"):
        return "approved"
    if n in ("fresh leads", "to review"):
        return "fresh"
    return "work"


# ---------- remember where every card sits (how deletes are noticed) ----------

def track_board(conn, cfg: Any, asana, now: datetime | None = None, max_checks: int = 40) -> dict[str, int]:
    """Record every open card's column; a card that vanished since the last look is checked once: 404 = deleted,
    completed = closed by the engine (an answer card that went out) or by him. Never raises."""
    a = _a(cfg)
    n = {"cards": 0, "deleted": 0, "completed": 0, "baseline": 0}
    if not a.get("project"):
        return n
    stamp = iso(now)
    try:
        tasks = asana.project_tasks(a["project"])
    except Exception as e:  # noqa: BLE001
        log.warning("end of day: board listing failed: %s", e)
        return n
    known = {r["gid"]: dict(r) for r in conn.execute("SELECT * FROM board_cards WHERE gone_at IS NULL").fetchall()}
    first = not conn.execute("SELECT 1 FROM board_cards LIMIT 1").fetchone()
    seen = set()
    with db.tx(conn):
        for t in tasks:
            sec_gid, sec_name = _section_of(t, a["project"])
            seen.add(t["gid"])
            conn.execute("INSERT INTO board_cards(gid,name,section_gid,section_name,first_seen,last_seen) VALUES(?,?,?,?,?,?) "
                         "ON CONFLICT(gid) DO UPDATE SET name=excluded.name, section_gid=excluded.section_gid, "
                         "section_name=excluded.section_name, last_seen=excluded.last_seen, gone_at=NULL, gone_how=NULL",
                         (t["gid"], t.get("name"), sec_gid, sec_name, stamp, stamp))
    n["cards"] = len(seen)
    if first:
        n["baseline"] = len(seen)
        return n
    missing = [g for g in known if g not in seen]
    if not tasks or len(missing) > max(max_checks, len(known) // 2):
        log.warning("end of day: %d of %d cards missing from the listing; not checking (listing looks incomplete)", len(missing), len(known))
        return n
    for gid in missing:
        try:
            st = asana.task_state(gid)
        except Exception as e:  # noqa: BLE001
            log.warning("end of day: checking card %s: %s", gid, e)
            continue
        if st is None:
            how = "deleted"
        elif st.get("completed"):
            how = "completed"
        elif not any((m.get("project") or {}).get("gid") == a["project"] for m in st.get("memberships") or []):
            how = "removed"
        else:
            continue
        conn.execute("UPDATE board_cards SET gone_at=?, gone_how=? WHERE gid=?", (stamp, how, gid))
        n[how] = n.get(how, 0) + 1
    return n


# ---------- what happened today ----------

def _between(ts: str | None, start: datetime, end: datetime) -> bool:
    d = parse_iso(ts)
    return bool(d and start <= d < end)


def _et_time(ts: str | None) -> str:
    d = parse_iso(ts)
    return d.astimezone(ET).strftime("%-I:%M %p").lower() if d else ""


def card_url(gid: str) -> str:
    return f"https://app.asana.com/0/0/{gid}/f"


def _agent_for_card(conn, gid: str) -> int | None:
    r = conn.execute("SELECT agent_id FROM outreach WHERE asana_task_id=? ORDER BY id LIMIT 1", (gid,)).fetchone()
    return r[0] if r else None


def _streets(conn, property_ids: str | None) -> str:
    try:
        pids = json.loads(property_ids or "[]")
    except ValueError:
        return ""
    rows = conn.execute(f"SELECT street, city FROM properties WHERE id IN ({','.join('?' * len(pids)) or 'NULL'})", pids).fetchall()
    return "; ".join(f"{r['street']}, {r['city']}" for r in rows)


def emails_sent(conn, start_s: str, end_s: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT o.*, a.name AS agent_name FROM outreach o LEFT JOIN agents a ON a.id=o.agent_id "
                        "WHERE o.status='sent' AND o.channel='email' AND o.sent_at>=? AND o.sent_at<? ORDER BY o.sent_at",
                        (start_s, end_s)).fetchall()
    return [{"agent_id": r["agent_id"], "agent": r["agent_name"] or r["to_address"], "houses": _streets(conn, r["property_ids"]),
             "kind": "first" if (r["kind"] or "first_touch") == "first_touch" else "answer", "at": r["sent_at"],
             "gid": r["asana_task_id"]} for r in rows]


def replies_in(conn, start_s: str, end_s: str) -> list[dict[str, Any]]:
    from .conversations import NOT_AN_ANSWER
    rows = conn.execute("SELECT m.body, m.classification, m.created_at, t.agent_id, t.property_ids, a.name AS agent_name FROM messages m "
                        "JOIN threads t ON t.id=m.thread_id LEFT JOIN agents a ON a.id=t.agent_id WHERE m.direction='in' "
                        "AND m.created_at>=? AND m.created_at<? ORDER BY m.created_at", (start_s, end_s)).fetchall()
    out = []
    for r in rows:
        if (r["classification"] or "") in NOT_AN_ANSWER:
            continue
        body = " ".join((r["body"] or "").split())
        out.append({"agent_id": r["agent_id"], "agent": r["agent_name"] or "agent", "houses": _streets(conn, r["property_ids"]),
                    "class": (r["classification"] or "").replace("_", " ").lower(), "said": body[:160] + ("…" if len(body) > 160 else ""),
                    "at": r["created_at"]})
    return out


def offers_sent(conn, start_s: str, end_s: str) -> list[dict[str, Any]]:
    try:
        rows = conn.execute("SELECT street, city, price, list_price, sent_at FROM offers WHERE sent_at>=? AND sent_at<? ORDER BY sent_at",
                            (start_s, end_s)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [dict(r) for r in rows]


def board_activity(conn, cfg: Any, asana, start: datetime, end: datetime, max_cards: int = 300) -> dict[str, Any]:
    """Moves, comments, numbers and call cards from today's Asana history."""
    a = _a(cfg)
    out: dict[str, Any] = {"moves": [], "comments": [], "numbers": [], "call_cards": [], "board": {}, "ok": False}
    tasks = asana.project_tasks(a["project"], completed_since=iso(start))
    for t in tasks:
        if not t.get("completed"):
            name = _section_of(t, a["project"])[1] or "?"
            out["board"][name] = out["board"].get(name, 0) + 1
    touched = [t for t in tasks if _between(t.get("modified_at"), start, end + timedelta(hours=12))][:max_cards]
    for t in touched:
        try:
            stories = asana.stories(t["gid"])
        except Exception as e:  # noqa: BLE001
            log.warning("end of day: history of %s: %s", t["gid"], e)
            continue
        today = [s for s in stories if _between(s.get("created_at"), start, end)]
        mine = [s for s in today if (s.get("type") == "comment" or s.get("resource_subtype") == "comment_added")
                and not (s.get("text") or "").startswith(BOT) and (s.get("text") or "").strip()]
        for s in mine:
            out["comments"].append({"gid": t["gid"], "name": t.get("name"), "text": " ".join(s["text"].split())[:220], "at": s["created_at"]})
        if any((s.get("text") or "").startswith(NUMBERS_PREFIX) for s in today):
            out["numbers"].append({"gid": t["gid"], "name": t.get("name")})
        moves = [s for s in today if s.get("resource_subtype") == "section_changed"]
        if not moves:
            continue
        autopilot = any((s.get("text") or "").startswith(AUTOPILOT) for s in today)
        left_fresh = [s for s in moves if _kind((s.get("old_section") or {}).get("name", "")) == "fresh"]
        to_skip = [s for s in moves if _kind((s.get("new_section") or {}).get("name", "")) == "skip"]
        if not (left_fresh or to_skip) or (autopilot and not to_skip):
            continue
        first = (left_fresh or to_skip)[0]
        now_in = _section_of(t, a["project"])[1] if not t.get("completed") else "Completed"
        said = " ".join(s["text"] for s in mine)
        if _kind(now_in) == "skip" or to_skip:
            verdict = ELIMINATED
        elif CALLED_RE.search(said):
            verdict = CALLED
        elif _kind(now_in) == "approved" or now_in in ("Sent · no answer yet", "Completed"):
            verdict = APPROVED
        elif _kind(now_in) == "fresh":
            continue                       # out and back again: nothing decided
        elif mine:
            verdict = COMMENTED
        else:
            verdict = PROBABLY_CALLED
        out["moves"].append({"gid": t["gid"], "name": t.get("name"), "from": (first.get("old_section") or {}).get("name") or "?",
                             "to": now_in, "at": first.get("created_at"), "verdict": verdict, "agent_id": _agent_for_card(conn, t["gid"])})
    # 📞 call cards booked today (comment "call tomorrow" / "mark hot")
    if a.get("needs_rodrigo"):
        try:
            for t in asana.section_tasks(a["needs_rodrigo"]):
                if (t.get("name") or "").startswith("📞") and _between(t.get("created_at"), start, end):
                    out["call_cards"].append(t.get("name"))
        except Exception as e:  # noqa: BLE001
            log.warning("end of day: call cards: %s", e)
    out["ok"] = True
    return out


def collect(conn, cfg: Any, asana, run_date: str) -> dict[str, Any]:
    start_s, end_s = et_day_bounds(run_date)
    start, end = parse_iso(start_s), parse_iso(end_s)
    d: dict[str, Any] = {"date": run_date, "goal": goal(cfg), "emails": emails_sent(conn, start_s, end_s),
                         "replies": replies_in(conn, start_s, end_s), "offers": offers_sent(conn, start_s, end_s),
                         "moves": [], "comments": [], "numbers": [], "call_cards": [], "board": {}, "asana_ok": False, "deleted": []}
    if asana is not None and _a(cfg).get("project"):
        track_board(conn, cfg, asana)
        try:
            b = board_activity(conn, cfg, asana, start, end)
            d.update({k: b[k] for k in ("moves", "comments", "numbers", "call_cards", "board")}, asana_ok=b["ok"])
        except Exception as e:  # noqa: BLE001 — the report still goes out with the engine's own numbers
            log.warning("end of day: Asana history failed: %s", e)
            d["asana_error"] = str(e)[:200]
    d["deleted"] = [{"gid": r["gid"], "name": r["name"], "from": r["section_name"] or "?", "at": r["gone_at"],
                     "agent_id": _agent_for_card(conn, r["gid"])}
                    for r in conn.execute("SELECT * FROM board_cards WHERE gone_how='deleted' AND gone_at>=? AND gone_at<? ORDER BY gone_at",
                                          (start_s, end_s)).fetchall()]
    r = conn.execute("SELECT MIN(first_seen) FROM board_cards").fetchone()
    d["tracking_since"] = r[0] if r else None

    def key(x: dict[str, Any]) -> str:
        return f"a{x['agent_id']}" if x.get("agent_id") else f"c{x.get('gid')}"
    emailed = {key(x) for x in d["emails"]}
    talked_moves = [m for m in d["moves"] if m["verdict"] in (CALLED, PROBABLY_CALLED)]
    talked = {key(m) for m in talked_moves} - emailed
    d["counts"] = {"emailed": len(emailed), "first_emails": sum(1 for x in d["emails"] if x["kind"] == "first"),
                   "answers": sum(1 for x in d["emails"] if x["kind"] == "answer"), "talked": len(talked),
                   "touched": len(emailed | talked), "eliminated": sum(1 for m in d["moves"] if m["verdict"] == ELIMINATED) + len(d["deleted"]),
                   "off_fresh": sum(1 for m in d["moves"] if _kind(m["from"]) == "fresh") + sum(1 for x in d["deleted"] if _kind(x["from"]) == "fresh"),
                   "replies": len(d["replies"]), "offers": len(d["offers"]), "comments": len(d["comments"]),
                   "numbers": len(d["numbers"]), "call_cards": len(d["call_cards"])}
    d["history"] = history(conn, cfg, run_date, d["counts"])
    return d


def history(conn, cfg: Any, run_date: str, today: dict[str, int], days: int = 7) -> list[dict[str, Any]]:
    """The last `days` days vs the goal, newest first. Emails come from the database; calls only from the days an
    end-of-day report was built (they live in its saved counts)."""
    out = []
    day0 = datetime.strptime(run_date, "%Y-%m-%d")
    for i in range(days):
        ds = (day0 - timedelta(days=i)).strftime("%Y-%m-%d")
        if i == 0:
            out.append({"date": ds, "emailed": today["emailed"], "talked": today["talked"], "touched": today["touched"], "tracked": True})
            continue
        s, e = et_day_bounds(ds)
        emailed = conn.execute("SELECT COUNT(DISTINCT COALESCE(agent_id, to_address)) FROM outreach WHERE status='sent' AND channel='email' "
                               "AND sent_at>=? AND sent_at<?", (s, e)).fetchone()[0]
        r = conn.execute("SELECT counts FROM daily_runs WHERE step=? AND run_date=? AND status='ok' ORDER BY id DESC LIMIT 1", (STEP, ds)).fetchone()
        c = json.loads(r[0]) if r and r[0] else None
        talked = (c or {}).get("talked", 0)
        out.append({"date": ds, "emailed": emailed, "talked": talked, "touched": (c or {}).get("touched", emailed + talked), "tracked": bool(c)})
    return out


# ---------- the email ----------

def subject(d: dict[str, Any]) -> str:
    day = datetime.strptime(d["date"], "%Y-%m-%d").strftime("%a %b %-d")
    return f"Titan end of day {day} · {d['counts']['touched']} of {d['goal']} leads"


def build_text(d: dict[str, Any]) -> str:
    c = d["counts"]
    day = datetime.strptime(d["date"], "%Y-%m-%d").strftime("%A, %B %-d")
    left = d["goal"] - c["touched"]
    lines = [f"# Titan end of day — {day}", "",
             f"**Goal: {c['touched']} of {d['goal']} leads emailed or talked to** · " + (f"{left} short" if left > 0 else "goal hit"),
             f"- Emailed {c['emailed']} agents ({c['first_emails']} first emails, {c['answers']} answers) · talked to {c['talked']} more (best guess, see below)",
             f"- Off Fresh Leads today: {c['off_fresh']} · eliminated {c['eliminated']} (Skip + deleted) · replies in {c['replies']} · offers sent {c['offers']}",
             f"- Your comments {c['comments']} · numbers posted {c['numbers']} · call cards {c['call_cards']}", ""]
    if not d.get("asana_ok"):
        lines += ["_Asana could not be read tonight, so moves, deletes and calls are missing: " + (d.get("asana_error") or "no ASANA_TOKEN") + "_", ""]
    lines += ["## Who you moved", ""]
    lines += [f"- {_et_time(m['at'])} · {m['name']} · {m['from']} → {m['to']} · **{m['verdict']}**" for m in d["moves"]] or ["- nobody"]
    if d["deleted"]:
        lines += ["", "## Deleted", ""] + [f"- {x['name']} (was in {x['from']})" for x in d["deleted"]]
    lines += ["", "## Emails out", ""]
    lines += [f"- {_et_time(x['at'])} · {x['agent']} · {x['houses']} · {'first email' if x['kind'] == 'first' else 'answer'}" for x in d["emails"]] or ["- none"]
    lines += ["", "## Replies in", ""]
    lines += [f"- {_et_time(x['at'])} · {x['agent']} ({x['class']}): {x['said']}" for x in d["replies"]] or ["- none"]
    if d["offers"] or d["numbers"]:
        lines += ["", "## Numbers and offers", ""]
        lines += [f"- Offer {money(x['price'])} on {x['street']}, {x['city']} (asking {money(x['list_price'])})" for x in d["offers"]]
        lines += [f"- Numbers posted on {x['name']}" for x in d["numbers"]]
    if d["comments"]:
        lines += ["", "## Your comments", ""] + [f"- {_et_time(x['at'])} · {x['name']}: {x['text']}" for x in d["comments"]]
    lines += ["", "## Last 7 days", ""]
    lines += [f"- {h['date']}: {h['touched']} of {d['goal']} ({h['emailed']} emailed" + (f", {h['talked']} talked)" if h["tracked"] else ", calls not tracked)")
              for h in d["history"]]
    lines += ["", "Calls are read from the board (Aircall is not connected): a card you took off Fresh Leads into a working column "
              "with no comment counts as a call, so does a comment that says you called, spoke or left a voicemail."]
    return "\n".join(lines)


def build_html(d: dict[str, Any]) -> str:
    from .report_html import (GOLD, GOLD_INK, GOLD_SOFT, GOOD, GOOD_SOFT, INK, LINE, MONO, MUTED, NAVY, PAPER, SANS, SERIF, SUNK,
                              WARN, WARN_SOFT, BAD, BAD_SOFT, FAINT, _hbar, e, label, panel, pill, section, table, tiles)
    c = d["counts"]
    g = d["goal"]
    day = datetime.strptime(d["date"], "%Y-%m-%d").strftime("%A, %B %-d")
    rows: list[str] = []
    rows.append(f'<tr><td style="background:{NAVY};border-bottom:3px solid {GOLD};padding:20px 24px">'
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
                f'<td width="40" valign="middle"><div style="width:32px;height:32px;border-radius:16px;background:{GOLD};color:#1a1205;'
                f'font-family:{SERIF};font-size:18px;line-height:32px;text-align:center">T</div></td>'
                f'<td valign="middle" style="padding-left:10px"><div style="font-family:{MONO};font-size:12px;letter-spacing:.3em;'
                f'text-transform:uppercase;color:#fff">Titan <span style="color:{GOLD};font-weight:700">End of Day</span></div>'
                f'<div style="font-family:{MONO};font-size:10.5px;letter-spacing:.06em;color:rgba(255,255,255,.6);margin-top:3px">'
                f'{e(day)} · what happened on the leads today</div></td></tr></table></td></tr>')

    # the goal
    hit = c["touched"] >= g
    pct = max(0, min(100, round(100 * c["touched"] / max(g, 1))))
    color = GOOD if hit else GOLD
    fill = f'<td width="{pct}%" style="background:{color};height:12px;font-size:0;line-height:0">&nbsp;</td>' if pct else ""
    rest = f'<td style="background:{SUNK};height:12px;font-size:0;line-height:0">&nbsp;</td>' if pct < 100 else ""
    left = g - c["touched"]
    rows.append(f'<tr><td style="padding:26px 24px 0">{label("Today vs the goal")}'
                f'<div style="font-family:{SERIF};font-size:44px;line-height:1;color:{INK}">{c["touched"]}'
                f'<span style="font-size:22px;color:{MUTED}"> of {g} leads</span></div>'
                f'<div style="font-family:{SANS};font-size:14px;color:{MUTED};margin:8px 0 12px">emailed or talked to · '
                + (f'<b style="color:{GOOD}">goal hit</b>' if hit else f'<b style="color:{GOLD_INK}">{left} short</b>') + '</div>'
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-radius:6px;overflow:hidden">'
                f'<tr>{fill}{rest}</tr></table></td></tr>')
    rows.append(f'<tr><td style="padding:18px 24px 0">' + tiles([
        ("Emailed", str(c["emailed"]), f"{c['first_emails']} first emails · {c['answers']} answers"),
        ("Talked to", str(c["talked"]), "best guess from the board"),
        ("Off Fresh Leads", str(c["off_fresh"]), f"eliminated {c['eliminated']} (Skip + deleted)"),
        ("Replies in", str(c["replies"]), f"offers sent {c['offers']} · numbers {c['numbers']}"),
    ]) + "</td></tr>")
    if not d.get("asana_ok"):
        rows.append(f'<tr><td style="padding:0 24px">' + panel(
            f'<span style="color:{WARN}">Asana could not be read tonight, so moves, deletes and calls are missing.</span> '
            f'<span style="color:{MUTED}">{e(d.get("asana_error") or "ASANA_TOKEN is not set on the Mac.")}</span>', accent=WARN) + "</td></tr>")

    # who he moved
    tone = {ELIMINATED: (BAD, BAD_SOFT), APPROVED: (GOOD, GOOD_SOFT), CALLED: (GOLD_INK, GOLD_SOFT),
            PROBABLY_CALLED: (GOLD_INK, GOLD_SOFT), COMMENTED: (MUTED, SUNK)}
    link = lambda gid, name: f'<a href="{e(card_url(gid))}" style="color:{INK};text-decoration:none"><b>{e(name)}</b></a>'  # noqa: E731
    if d["moves"] or d["deleted"]:
        trs = [[link(m["gid"], m["name"]) + f'<br><span style="color:{MUTED};font-size:12px">{e(m["from"])} → {e(m["to"])}</span>',
                pill(m["verdict"], *tone[m["verdict"]]), e(_et_time(m["at"]))] for m in d["moves"]]
        trs += [[f'<b>{e(x["name"])}</b><br><span style="color:{MUTED};font-size:12px">deleted from {e(x["from"])}</span>',
                 pill("Deleted", BAD, BAD_SOFT), e(_et_time(x["at"]))] for x in d["deleted"]]
        body = table(["Card", "Read as", "Time"], trs, right={2})
    else:
        body = panel(f'<span style="color:{MUTED}">You didn\'t move or delete any card today.</span>')
    rows.append(section("Who you moved", body, "Off Fresh Leads, to Skip, deleted"))

    # emails and replies
    if d["emails"]:
        trs = [[f'<b>{e(x["agent"])}</b><br><span style="color:{MUTED};font-size:12px">{e(x["houses"])}</span>',
                e("First email" if x["kind"] == "first" else "Answer"), e(_et_time(x["at"]))] for x in d["emails"]]
        rows.append(section("Emails out", table(["Agent", "", "Time"], trs, right={2}), f"{len(d['emails'])} sent from Charles"))
    else:
        rows.append(section("Emails out", panel(f'<span style="color:{MUTED}">No emails went out today.</span>'), "From Charles"))
    if d["replies"]:
        body = "".join(panel(f'<div style="font-size:14px"><b>{e(x["agent"])}</b> <span style="color:{FAINT};font-family:{MONO};font-size:11px">'
                             f'{e(_et_time(x["at"]))} · {e(x["class"])}</span></div>'
                             f'<div style="font-size:12px;color:{MUTED};margin:2px 0 6px">{e(x["houses"])}</div>'
                             f'<div style="border-left:2px solid {GOLD};padding-left:10px;font-size:13.5px">{e(x["said"])}</div>')
                       for x in d["replies"])
        rows.append(section("Replies in", body, "Agents who wrote back"))

    # numbers and offers
    if d["offers"] or d["numbers"]:
        trs = [[f'<b>{e(x["street"])}</b>, {e(x["city"])}', e(f"Offer {money(x['price'])}"),
                e(f"ask {money(x['list_price'])}")] for x in d["offers"]]
        trs += [[link(x["gid"], x["name"]), e("Numbers posted"), ""] for x in d["numbers"]]
        rows.append(section("Numbers and offers", table(["", "", ""], trs, right={2}), "On the cards"))

    if d["comments"]:
        body = "".join(f'<div style="font-size:13.5px;color:{INK};padding:8px 0;border-bottom:1px solid {LINE}">'
                       f'<span style="font-family:{MONO};font-size:11px;color:{FAINT}">{e(_et_time(x["at"]))}</span> '
                       f'{link(x["gid"], x["name"])}<br>{e(x["text"])}</div>' for x in d["comments"])
        rows.append(section("Your comments", panel(body), f"{len(d['comments'])} today"))

    # the week vs the goal
    top = max([g] + [h["touched"] for h in d["history"]])
    hist = ""
    for i, h in enumerate(d["history"]):
        name = "Today" if i == 0 else datetime.strptime(h["date"], "%Y-%m-%d").strftime("%a %-d")
        note = f"{h['touched']} of {g}" + (f" · {h['emailed']} emailed, {h['talked']} talked" if h["tracked"] else " · emails only")
        hist += (f'<tr><td width="64" style="font-family:{MONO};font-size:11px;color:{INK if i == 0 else MUTED};padding:5px 0">{e(name)}</td>'
                 f'<td style="padding:5px 0">{_hbar(h["touched"], top, GOOD if h["touched"] >= g else (NAVY if i else GOLD), note, f"0 of {g}")}</td></tr>')
    rows.append(section("Last 7 days", f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{hist}</table>',
                        f"Goal: {g} a day"))

    if d["board"]:
        trs = [[e(k), e(v)] for k, v in d["board"].items()]
        rows.append(section("The board tonight", table(["", ""], trs, right={1}), "Cards per column"))

    rows.append(f'<tr><td style="padding:28px 24px 30px;font-family:{MONO};font-size:10.5px;letter-spacing:.06em;color:{FAINT};line-height:1.6">'
                f'Calls are read from the board (Aircall is not connected): a card you took off Fresh Leads into a working column with no '
                f'comment counts as a call; so does a comment that says you called, spoke or left a voicemail. An agent emailed and called '
                f'counts once. Eliminated cards are shown but are not part of the 25.</td></tr>')
    return ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="color-scheme" content="light only"><title>Titan end of day</title></head>'
            f'<body style="margin:0;padding:0;background:{PAPER}">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAPER}"><tr><td align="center" style="padding:0">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;background:{PAPER}">'
            + "".join(rows) + "</table></td></tr></table></body></html>")


# ---------- when and how it goes out ----------

def already_sent(conn, run_date: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM daily_runs WHERE step=? AND run_date=? AND status='ok' AND notes='emailed'",
                             (STEP, run_date)).fetchone())


def due(conn, cfg: Any, now: datetime | None = None) -> str | None:
    """The day whose report should go out now: yesterday's whenever the Mac slept through last evening (first run after it
    wakes, any hour; only if the board was already being watched yesterday), else today's after the evening hour."""
    t = (now or now_utc()).astimezone(ET)
    today = t.strftime("%Y-%m-%d")
    y = (t - timedelta(days=1)).strftime("%Y-%m-%d")
    first = conn.execute("SELECT MIN(first_seen) FROM board_cards").fetchone()[0]
    if first and first < et_day_bounds(y)[1] and not already_sent(conn, y):
        return y
    if t.hour >= int(_cfg(cfg).get("hour_et", 19)):
        return None if already_sent(conn, today) else today
    return None


def run(conn, cfg: Any, gmail, asana, run_date: str, send: bool = True, by_hand: bool = False) -> dict[str, Any]:
    d = collect(conn, cfg, asana, run_date)
    text, html = build_text(d), None
    try:
        html = build_html(d)
    except Exception as e:  # noqa: BLE001 — a design bug never stops the report: the text goes alone
        log.exception("end of day html failed: %s", e)
    out_dir = cfg.path("reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_date}-end-of-day.md"
    path.write_text(text)
    if html:
        (out_dir / f"{run_date}-end-of-day.html").write_text(html)
    status = "written"
    to = cfg.secret("REPORT_TO")
    if send and gmail is not None and to:
        try:
            gmail.send(to, subject(d), text, html=html)
            status = "emailed (by hand)" if by_hand else "emailed"     # one sent by hand never stops the evening one
        except Exception as e:  # noqa: BLE001
            log.error("end of day email failed: %s", e)
            status = f"email failed: {str(e)[:150]}"
    rid = db.start_run(conn, STEP, run_date)
    db.finish_run(conn, rid, "ok" if status.startswith(("emailed", "written")) else "partial", d["counts"], notes=status)
    return {"date": run_date, "status": status, "path": str(path), **d["counts"]}


def maybe_send(conn, cfg: Any, gmail, asana, now: datetime | None = None) -> dict[str, Any] | None:
    """Called from every check-replies run: sends the end-of-day report once, after the evening hour. Never raises."""
    try:
        day = due(conn, cfg, now)
        if not day or gmail is None or not cfg.secret("REPORT_TO"):
            return None
        return run(conn, cfg, gmail, asana, day)
    except Exception as e:  # noqa: BLE001
        log.exception("end of day report: %s", e)
        return {"error": str(e)[:200]}
