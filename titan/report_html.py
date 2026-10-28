"""The morning report as a designed email (Rodrigo, 2026-10-27: "it needs to be a nice design, like all of our other designs").

Same house style as the website (dashboard/public/styles.css) and the flow PDF: navy bar with a gold rule, warm paper,
white panels with hairlines, serif headings, small uppercase mono labels, gold for "do this", green / amber / red for
state. Email-safe: tables and inline styles only (Gmail drops most <style> rules and all CSS grid / variables), one
640px column that also reads on a phone. The plain-text report still goes in the same email (and keeps the data block
the cloud session reads), so this file only changes how the report looks."""
from __future__ import annotations

import html
from datetime import datetime, timedelta
from typing import Any

from . import db
from .util import iso, money, now_utc, today_et

PAPER, SURFACE, SUNK, LINE = "#F4F2EC", "#FFFFFF", "#ECE9E1", "#E1DCD0"
INK, MUTED, FAINT = "#16203A", "#5B6472", "#8A9199"
NAVY, GOLD, GOLD_INK, GOLD_SOFT = "#0C1C35", "#B99455", "#8A6400", "#F3EAD6"
GOOD, GOOD_SOFT, WARN, WARN_SOFT, BAD, BAD_SOFT = "#16834A", "#E7F1EA", "#B4622A", "#F7E8DD", "#A13228", "#F6E0DF"
SERIF = "Georgia,'Times New Roman',serif"
SANS = "-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "Menlo,Consolas,'Courier New',monospace"

STATUS = {   # lead status → (label, text color, background)
    "NEEDS_REVIEW": ("Needs you", GOLD_INK, GOLD_SOFT), "OFFER_DRAFTED": ("Offer drafted", GOLD_INK, GOLD_SOFT),
    "FLEXIBLE": ("Flexible", GOOD, GOOD_SOFT), "HOT_LEAD": ("Hot", GOOD, GOOD_SOFT), "NEEDS_CALL": ("Call", WARN, WARN_SOFT),
}


def e(x: Any) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def label(text: str, color: str = FAINT) -> str:
    return (f'<div style="font-family:{MONO};font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:{color};'
            f'margin:0 0 6px">{e(text)}</div>')


def pill(text: str, color: str, bg: str) -> str:
    return (f'<span style="display:inline-block;font-family:{MONO};font-size:10px;letter-spacing:.12em;text-transform:uppercase;'
            f'color:{color};background:{bg};border:1px solid {color};border-radius:999px;padding:3px 9px">{e(text)}</span>')


def section(title: str, body: str, eyebrow: str = "") -> str:
    return (f'<tr><td style="padding:26px 24px 4px;font-family:{SANS};color:{INK}">{label(eyebrow) if eyebrow else ""}'
            f'<div style="font-family:{SERIF};font-size:22px;line-height:1.2;color:{INK};margin:0 0 12px">{e(title)}</div>'
            f'{body}</td></tr>')


def panel(inner: str, accent: str | None = None) -> str:
    left = f"border-left:3px solid {accent};" if accent else ""
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{SURFACE};border:1px solid {LINE};'
            f'{left}border-radius:10px;margin:0 0 12px"><tr><td style="padding:16px 18px;font-family:{SANS};font-size:14px;'
            f'line-height:1.5;color:{INK}">{inner}</td></tr></table>')


def tiles(items: list[tuple[str, str, str]]) -> str:
    """KPI tiles, two per row (reads on a phone): (label, value, note)."""
    cells = [f'<td width="50%" valign="top" style="padding:0 6px 12px">'
             f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{SURFACE};border:1px solid {LINE};'
             f'border-radius:10px"><tr><td style="padding:14px 16px">{label(l)}'
             f'<div style="font-family:{SERIF};font-size:30px;line-height:1;color:{INK}">{e(v)}</div>'
             f'<div style="font-family:{SANS};font-size:12px;color:{MUTED};margin-top:6px">{e(n)}</div></td></tr></table></td>'
             for l, v, n in items]
    rows = "".join("<tr>" + "".join(cells[i:i + 2]) + ("<td></td>" if len(cells[i:i + 2]) == 1 else "") + "</tr>"
                   for i in range(0, len(cells), 2))
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 -6px">{rows}</table>'


def bar(rate: float | None, unlock: float) -> str:
    pct = 0 if rate is None else max(0, min(100, round(rate * 100)))
    mark = round(unlock * 100)
    color = GOOD if rate is not None and rate >= unlock else GOLD
    fill = f'<td width="{pct}%" style="background:{color};height:8px;font-size:0;line-height:0">&nbsp;</td>' if pct else ""
    rest = f'<td style="background:{SUNK};height:8px;font-size:0;line-height:0">&nbsp;</td>' if pct < 100 else ""
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-radius:4px;overflow:hidden">'
            f'<tr>{fill}{rest}</tr></table>'
            f'<div style="font-family:{MONO};font-size:10px;color:{FAINT};margin-top:4px">{pct}% now · unlocks at {mark}%</div>')


def _hbar(v: int, top: int, color: str, text: str, zero: str) -> str:
    """One horizontal bar with its value written right after it (Gmail and the iPhone draw percent-width cells reliably;
    the old column chart's pixel-high divs did not). Zero draws no bar, just the faint word."""
    if not v:
        return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
                f'<td style="font-family:{SANS};font-size:12px;line-height:14px;color:{FAINT}">{e(zero)}</td></tr></table>')
    pct = max(3, round(72 * v / max(top, 1)))
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td width="{pct}%" style="background:{color};height:12px;font-size:0;line-height:0;border-radius:0 3px 3px 0">&nbsp;</td>'
            f'<td style="padding-left:7px;font-family:{SANS};font-size:12px;line-height:14px;color:{INK};white-space:nowrap">{e(text)}</td>'
            f'</tr></table>')


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def day_chart(series: list[dict[str, Any]], show_days: int = 7) -> str:
    """The last week, one row per day, newest first: a navy bar for emails sent and a gold bar for real agent replies,
    each with its number written out ("4 sent", "2 replies"). Above it the week's totals; below it, the week before, so
    nothing has to be read off an axis (Rodrigo, 2026-10-27: the column chart was unreadable in the email)."""
    recent = series[-show_days:]
    earlier = series[:-show_days]
    top = max([max(d.get("emails_sent", 0), d.get("replies", 0)) for d in recent] + [1])
    sent = sum(d.get("emails_sent", 0) for d in recent)
    replies = sum(d.get("replies", 0) for d in recent)
    last_date = series[-1]["date"] if series else None

    def stat(l: str, v: str) -> str:
        return (f'<td width="33%" valign="top" style="padding:0 10px 0 0">{label(l)}'
                f'<div style="font-family:{SERIF};font-size:24px;line-height:1;color:{INK}">{e(v)}</div></td>')
    rate = f"{round(100 * replies / sent)}%" if sent else "–"
    head = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'{stat("Sent", str(sent))}{stat("Replies", str(replies))}{stat("Reply rate", rate)}</tr></table>')
    key = (f'<div style="font-family:{SANS};font-size:12px;color:{MUTED};margin:14px 0 4px">'
           f'<span style="display:inline-block;width:10px;height:10px;background:{NAVY};border-radius:2px"></span> emails sent &nbsp;&nbsp; '
           f'<span style="display:inline-block;width:10px;height:10px;background:{GOLD};border-radius:2px"></span> agent replies</div>')

    rows = ""
    for d in reversed(recent):
        when = datetime.strptime(d["date"], "%Y-%m-%d")
        today = d["date"] == last_date
        name = "Today" if today else when.strftime("%a %-d")
        tint = f"background:{GOLD_SOFT};" if today else ""
        rows += (f'<tr><td width="62" valign="top" style="{tint}padding:9px 6px 9px 8px;border-top:1px solid {SUNK};font-family:{SANS};'
                 f'font-size:12px;line-height:14px;color:{INK};white-space:nowrap;{"font-weight:700;" if today else ""}">{e(name)}</td>'
                 f'<td valign="top" style="{tint}padding:8px 8px 8px 0;border-top:1px solid {SUNK}">'
                 + _hbar(d.get("emails_sent", 0), top, NAVY, _plural(d.get("emails_sent", 0), "sent", "sent"), "0 sent")
                 + '<div style="height:3px;font-size:0;line-height:0">&nbsp;</div>'
                 + _hbar(d.get("replies", 0), top, GOLD, _plural(d.get("replies", 0), "reply", "replies"), "0 replies")
                 + "</td></tr>")
    body = f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:6px">{rows}</table>'
    if earlier:
        es, er = sum(d.get("emails_sent", 0) for d in earlier), sum(d.get("replies", 0) for d in earlier)
        body += (f'<div style="font-family:{SANS};font-size:12px;color:{MUTED};margin-top:10px;padding-top:10px;border-top:1px solid {SUNK}">'
                 f'The {len(earlier)} days before that: {e(_plural(es, "email"))} sent, {e(_plural(er, "reply", "replies"))}.</div>')
    return panel(head + key + body)


def funnel(steps: list[tuple[str, int]], notes: list[str] | None = None) -> str:
    """How far the emails get, one step per row: the number large, the step's name, a plain sentence on the drop from the
    step above ("3 of 9 wrote back · 33%"), and a bar on one shared scale (share of the first step)."""
    top = steps[0][1] if steps else 0
    if not top:
        return panel(f'<span style="color:{MUTED}">No emails sent yet, so nothing to measure.</span>')
    notes = notes or [""] * len(steps)
    out = ""
    prev = top
    for i, (name, v) in enumerate(steps):
        pct_top = max(0, min(100, round(100 * v / top)))
        if i == 0:
            line = notes[i] or "every first email to a listing agent"
        else:
            share = f"{round(100 * v / prev)}%" if prev else "–"
            line = f"{v} of {prev} {notes[i] or 'from the step above'} · {share}"
        bar_ = (f'<td width="{max(pct_top, 2)}%" style="background:{NAVY};height:10px;font-size:0;line-height:0;border-radius:0 3px 3px 0">&nbsp;</td>'
                if v else "")
        rest = f'<td style="background:{SUNK};height:10px;font-size:0;line-height:0">&nbsp;</td>' if pct_top < 100 else ""
        top_rule = "" if i == 0 else f' style="border-top:1px solid {SUNK}"'      # no backslash inside an f-string (Python 3.11 on `make setup`)
        out += (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"{top_rule}>'
                f'<tr><td width="54" valign="top" style="padding:{0 if i == 0 else 12}px 8px 10px 0;font-family:{SERIF};font-size:28px;line-height:1;color:{INK}">{e(v)}</td>'
                f'<td valign="top" style="padding:{0 if i == 0 else 12}px 0 10px">'
                f'<div style="font-family:{SANS};font-size:14px;line-height:1.2;color:{INK};font-weight:700">{e(name)}</div>'
                f'<div style="font-family:{SANS};font-size:12px;color:{MUTED};margin:3px 0 7px">{e(line)}</div>'
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{bar_}{rest}</tr></table>'
                f'</td></tr></table>')
        prev = v if v else prev
    return panel(out)


def table(head: list[str], rows: list[list[str]], right: set[int] | None = None) -> str:
    """rows are already-escaped HTML cells."""
    right = right or set()
    th = "".join(f'<td style="font-family:{MONO};font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:{FAINT};'
                 f'padding:8px 10px;border-bottom:1px solid {LINE};{"text-align:right;" if i in right else ""}">{e(h)}</td>'
                 for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f'<td style="font-family:{SANS};font-size:13px;color:{INK};padding:8px 10px;border-bottom:1px solid '
                                    f'{SUNK};vertical-align:top;{"text-align:right;white-space:nowrap;" if i in right else ""}">{c}</td>'
                                    for i, c in enumerate(r)) + "</tr>" for r in rows)
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{SURFACE};border:1px solid {LINE};'
            f'border-radius:10px;border-collapse:separate">{"<tr>" + th + "</tr>" if any(head) else ""}{body}</table>')


def asana_card(conn, property_id: int) -> str | None:
    try:
        r = conn.execute("SELECT o.asana_task_id FROM outreach o, json_each(o.property_ids) j WHERE j.value=? AND o.asana_task_id IS NOT NULL "
                         "ORDER BY o.id DESC LIMIT 1", (property_id,)).fetchone()
    except Exception:  # noqa: BLE001 — sqlite without json1: no link, nothing else changes
        return None
    return f"https://app.asana.com/0/0/{r[0]}/f" if r and r[0] else None


def lead_card(conn, i: int, p: dict[str, Any]) -> str:
    lab, col, bg = STATUS.get(p.get("lead_status") or "", ("Open", MUTED, SUNK))
    phone, email = p.get("agent_phone"), p.get("agent_email")
    who = " · ".join(x for x in (
        e(p.get("agent_name") or "Agent unknown"),
        f'<a href="tel:{e(phone)}" style="color:{GOLD_INK};text-decoration:none">{e(phone)}</a>' if phone else "",
        f'<a href="mailto:{e(email)}" style="color:{GOLD_INK};text-decoration:none">{e(email)}</a>' if email else "") if x)
    quote = p.get("last_message") or "(no reply yet)"
    replied = quote != "(no reply yet)"
    links = [f'<a href="{e(u)}" style="color:{GOLD_INK}">{t}</a>' for t, u in
             (("Asana card", asana_card(conn, p["id"])), ("Listing", p.get("listing_url"))) if u]
    inner = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
             f'<td style="font-family:{MONO};font-size:10px;letter-spacing:.14em;color:{FAINT}">{i:02d}</td>'
             f'<td align="right">{pill(lab, col, bg)}</td></tr></table>'
             f'<div style="font-family:{SERIF};font-size:19px;line-height:1.25;color:{INK};margin:6px 0 2px">{e(p["street"])}, {e(p["city"])}</div>'
             f'<div style="font-size:13px;color:{MUTED};margin-bottom:10px">Asking {e(money(p.get("list_price")))} · {who}</div>'
             + (f'<div style="border-left:2px solid {GOLD};padding:2px 0 2px 12px;margin:0 0 10px;font-size:13.5px;color:{INK}">'
                f'<span style="font-family:{MONO};font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:{FAINT}">Their last words</span><br>'
                f'{e(quote)}</div>' if replied else
                f'<div style="font-size:13px;color:{FAINT};margin:0 0 10px">No reply yet.</div>')
             + f'<div style="background:{GOLD_SOFT};border-radius:8px;padding:10px 12px;font-size:13.5px;color:{INK}">'
               f'<span style="font-family:{MONO};font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:{GOLD_INK}">Next step</span><br>'
               f'{e(p.get("say_this"))}</div>'
             + f'<div style="font-family:{MONO};font-size:11px;color:{FAINT};margin-top:10px">Internal only · ARV {e(money(p.get("arv")))} '
               f'({e(p.get("comp_count") or 0)} comps) · screening MAO {e(money(p.get("mao")))}'
               + (f' · {" · ".join(links)}' if links else "") + "</div>")
    return panel(inner, accent=col)


def build_html(conn, cfg: Any, run_date: str | None = None) -> str:
    from .learning import ladder
    from .report import collect_warnings, hot_leads, top_targets
    from .stats import compute_stats
    run_date = run_date or today_et()
    day = datetime.strptime(run_date, "%Y-%m-%d").strftime("%A, %B %-d")
    leads = hot_leads(conn, cfg.get("report.max_hot_leads", 10))
    stats = compute_stats(conn, cfg, with_asana=bool(cfg.secret("ASANA_TOKEN")))
    st = stats.get("totals") or {}
    pct = lambda v: "n/a" if v is None else f"{v:.0f}%"  # noqa: E731
    rows: list[str] = []

    # header
    rows.append(f'<tr><td style="background:{NAVY};border-bottom:3px solid {GOLD};padding:20px 24px">'
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
                f'<td width="40" valign="middle"><div style="width:32px;height:32px;border-radius:16px;background:{GOLD};color:#1a1205;'
                f'font-family:{SERIF};font-size:18px;line-height:32px;text-align:center">T</div></td>'
                f'<td valign="middle" style="padding-left:10px"><div style="font-family:{MONO};font-size:12px;letter-spacing:.3em;'
                f'text-transform:uppercase;color:#fff">Titan <span style="color:{GOLD};font-weight:700">Morning Report</span></div>'
                f'<div style="font-family:{MONO};font-size:10.5px;letter-spacing:.06em;color:rgba(255,255,255,.6);margin-top:3px">'
                f'{e(day)} · email first · every email waits for you on Asana</div></td></tr></table></td></tr>')

    # the day at a glance
    needs = len([p for p in leads if p.get("lead_status") in ("NEEDS_REVIEW", "OFFER_DRAFTED")])
    headline = (f"{len(leads)} conversation{'s' if len(leads) != 1 else ''} for you"
                + (f", {needs} waiting on your decision" if needs else "") + ".")
    rows.append(f'<tr><td style="padding:26px 24px 0">{label("Today")}'
                f'<div style="font-family:{SERIF};font-size:28px;line-height:1.15;color:{INK}">{e(headline)}</div></td></tr>')
    rows.append(f'<tr><td style="padding:18px 24px 0">' + tiles([
        ("Emails sent", str(st.get("emails_sent", 0)), f"to {st.get('agents_emailed', 0)} agents · {st.get('bounces', 0)} bounced"),
        ("Reply rate", pct(st.get("reply_rate_pct")), f"{st.get('agents_replied', 0)} agents answered"),
        ("Awaiting our answer", str(st.get("awaiting_our_answer", 0)), f"needs you: {st.get('needs_rodrigo', 0)}"),
        ("Cards waiting", str(st.get("cards_waiting", 0)), f"HOT listings now: {st.get('hot', 0)}"),
    ]) + "</td></tr>")

    # conversations
    if leads:
        rows.append(section("Conversations for you", "".join(lead_card(conn, i, p) for i, p in enumerate(leads, 1)), "Needs you"))
    else:
        rows.append(section("Conversations for you", panel(f'<span style="color:{MUTED}">No conversations need you yet. '
                                                           'Replies land here as agents answer.</span>'), "Needs you"))

    # scoreboard
    o = stats.get("offers") or {}
    score = [["Conversations", e(st.get("conversations", 0))], ["Answered by us", f"{e(st.get('answered', 0))} ({e(pct(st.get('answer_rate_pct')))})"],
             ["Waiting on the agent", e(st.get("waiting_on_agent", 0))],
             ["Offers sent", e(o.get("offers", 0)) + (f" · median {e(o.get('median_pct_of_list'))}% of list" if o.get("offers") else "")],
             ["Today", f"{e(st.get('new_today', 0))} new listings · {e(st.get('sent_today', 0))} sent · {e(st.get('replies_today', 0))} replies"]]
    rows.append(section("Scoreboard", table(["", ""], score, right={1}), "How it's going"))

    # charts: the last 14 days, and how far emails get
    series = stats.get("daily") or []
    if series:
        rows.append(section("Day by day", day_chart(series), "Last 7 days, emails out and replies in"))
    rows.append(section("From email to offer", funnel([
        ("Emails sent", st.get("emails_sent", 0) or 0), ("Agents who replied", st.get("agents_replied", 0) or 0),
        # answered counts conversations (one agent can have two houses): never more agents answered than replied (was "4 of 3 · 133%")
        ("Answered by us", min(st.get("answered", 0) or 0, st.get("agents_replied", 0) or 0)), ("Offers sent", o.get("offers", 0) or 0)],
        ["", "wrote back", "got an answer from Charles", "got a number"]), "Where they drop off"))

    # trust ladder
    try:
        stages = ladder(conn, cfg)
    except Exception:  # noqa: BLE001
        stages = []
    if stages:
        body = "".join(panel(f'<div style="font-size:14px;color:{INK};margin-bottom:8px"><b>{e(s["title"])}</b></div>'
                             f'<div style="font-size:12.5px;color:{MUTED};margin-bottom:8px">{e(s["what"])} · last {e(s["decided"])} of {e(s["need"])} needed</div>'
                             + bar(s.get("rate"), s.get("unlock") or 0.8)) for s in stages)
        rows.append(section("Trust ladder", body, "How close Charles is to working on his own"))

    # top targets
    targets = top_targets(conn, 10)
    if targets:
        trs = [[f'<a href="{e(t.get("listing_url") or "#")}" style="color:{INK};text-decoration:none"><b>{e(t["street"])}</b></a><br>'
                f'<span style="color:{MUTED};font-size:12px">{e(t["city"])} · {e(t.get("agent_name") or "?")}</span>',
                f'{pill(t.get("lane") or "WATCH", GOOD if t.get("lane") == "FRESH_DEAL" else GOLD_INK, GOOD_SOFT if t.get("lane") == "FRESH_DEAL" else GOLD_SOFT)}',
                f'{e(money(t.get("list_price")))}<br><span style="color:{MUTED};font-size:12px">{e(t.get("days_on_market") or "?")} days'
                + (f' · {e(t["price_cuts"])} cut{"s" if (t.get("price_cuts") or 0) > 1 else ""}' if t.get("price_cuts") else "") + "</span>"]
               for t in targets]
        rows.append(section("Top targets", table(["House", "Lane", "Asking"], trs, right={2}), "HOT listings, best first"))

    # engine health
    f = db.funnel(conn, run_date)
    usage = db.usage_summary(conn, iso(now_utc() - timedelta(hours=24)))
    warnings = collect_warnings(conn, run_date)
    health = (f'<div style="font-size:13px;color:{MUTED};margin-bottom:10px">{f.get("properties", 0):,} listings stored · '
              f'{e(f.get("HOT", 0))} HOT · {e(f.get("WARM", 0))} warm · {f.get("agents_with_email", 0):,} agents with email · '
              f'Claude calls last 24h {e(usage["calls"])} (cost ${usage["cost_usd"]:.2f})</div>')
    if warnings:
        health += (f'<div style="background:{WARN_SOFT};border:1px solid {WARN};border-radius:8px;padding:10px 12px;font-size:13px;color:{INK}">'
                   f'<span style="font-family:{MONO};font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:{WARN}">Warnings</span>'
                   + "".join(f"<br>{e(w)}" for w in warnings) + "</div>")
    else:
        health += (f'<div style="background:{GOOD_SOFT};border:1px solid {GOOD};border-radius:8px;padding:10px 12px;font-size:13px;color:{INK}">'
                   "Everything ran clean today.</div>")
    rows.append(section("Engine health", health, "Behind the scenes"))

    rows.append(f'<tr><td style="padding:28px 24px 30px;font-family:{MONO};font-size:10.5px;letter-spacing:.06em;color:{FAINT};line-height:1.6">'
                f'Titan Realty Investments · lead engine · internal numbers are screening only and never reach an agent.<br>'
                f'The full text version (with the data block for the cloud review) is in this same email.</td></tr>')

    return ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="color-scheme" content="light only"><title>Titan morning report</title></head>'
            f'<body style="margin:0;padding:0;background:{PAPER}">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAPER}"><tr><td align="center" style="padding:0">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;background:{PAPER}">'
            + "".join(rows) + "</table></td></tr></table></body></html>")
