"""The house cards on the Email Approvals board, in the same shape as the 1031 cards (Rodrigo, 2026-11-12: "add the same design that
we have for the loopnet commercial properties into our MLS deals for single family homes so it's that organized").

Card name = `Agent · 3bd House · City · $300k · 92 days` (agent first, the key facts, no street clutter). Description = bold headline
(HOUSE · street, city · Listing link), then short bold-headed lists: THE NUMBERS (internal, never sent) / DO WE BELIEVE IT? (comps
grade) / THE HOUSE / WHY THIS HOUSE (the engine's read) / AGENT, then the FIRST EMAIL block (plain To / Subject / EMAIL-marker lines
and "(outreach #N)", kept exactly, so parse_card() and the sender read it on Asana's plain rendering just as before).

Asana html_notes allows body, strong, em, ul, li, a, hr, blockquote only here (no p / br / th). Older cards are refreshed once by
`refresh_cards` (approvals-round stage `house_card_format`)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import db
from .util import now_utc, pretty_phone

START, END = "-----EMAIL-----", "-----END EMAIL-----"   # same markers approvals.parse_card reads

log = logging.getLogger(__name__)

VERSION = "2026-11-12"
FOOTER = "Comment to change it · drag to Approved to send (business hours) · or edit the text between the EMAIL lines yourself."

TYPE_LABEL = [
    (re.compile(r"town", re.I), "Townhouse"), (re.compile(r"condo|apartment", re.I), "Condo"),
    (re.compile(r"duplex", re.I), "Duplex"), (re.compile(r"triplex", re.I), "Triplex"), (re.compile(r"quad|fourplex", re.I), "Fourplex"),
    (re.compile(r"multi", re.I), "Multi-family"), (re.compile(r"mobile|manufactured", re.I), "Mobile home"),
    (re.compile(r"land|lot", re.I), "Land"), (re.compile(r"single|house|residential|sfr", re.I), "House"),
]
GRADE_MEANING = {"A": "solid comps, build on it", "B": "good comps, small doubts", "C": "rough comps, check before a number",
                 "D": "a guess, don't price off it"}
LANE_LABEL = {"SITTING": "Sitting 60+ days", "FRESH_DEAL": "⚡ Fresh deal (lock it in fast)", "WATCH": "Watching"}


def _e(x: Any) -> str:
    import html
    return html.escape(str(x if x is not None else ""), quote=False)


def _money(x: Any) -> str:
    return f"${int(x):,}" if x else "n/a"


def short_money(x: Any) -> str:
    if not x:
        return "price n/a"
    x = float(x)
    if x >= 1_000_000:
        return f"${x / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    return f"${x / 1_000:.0f}k"


def type_label(p: dict[str, Any]) -> str:
    units = p.get("units") or 0
    t = str(p.get("property_type") or "")
    for rx, label in TYPE_LABEL:
        if rx.search(t):
            if label == "Multi-family" and units in (2, 3, 4):
                return {2: "Duplex", 3: "Triplex", 4: "Fourplex"}[units]
            return label
    return "House"


def _beds(p: dict[str, Any]) -> str:
    b = p.get("beds")
    return f"{b:g}bd " if isinstance(b, (int, float)) and b else ""


def card_name(agent: dict[str, Any], props: list[dict[str, Any]]) -> str:
    """Agent · 3bd House · City · $300k · 92 days (· 2 cuts) (· ⚡ fresh deal) (· +1 more)."""
    p = props[0] if props else {}
    bits = [agent.get("name") or "Agent", f"{_beds(p)}{type_label(p)}", p.get("city") or p.get("street") or "?", short_money(p.get("list_price"))]
    if p.get("days_on_market") is not None:
        bits.append(f"{p['days_on_market']} days")
    if p.get("price_cuts"):
        bits.append(f"{p['price_cuts']} cut{'s' if p['price_cuts'] != 1 else ''}")
    if p.get("lane") == "FRESH_DEAL":
        bits.append("⚡ fresh deal")
    if len(props) > 1:
        bits.append(f"+{len(props) - 1} more")
    return " · ".join(str(b) for b in bits)


def agent_line(agent: dict[str, Any]) -> str:
    phone = pretty_phone(agent.get("phone")) if agent.get("phone") else ""
    return f"Agent: {agent.get('name')} · {phone + ' · ' if phone else ''}{agent.get('email')} · {agent.get('brokerage') or ''}"


def listing_line(p: dict[str, Any]) -> str:
    """The plain one-line listing summary (also what card_parts() keeps when a lead card is rebuilt)."""
    photos = f" · {p.get('photos_count')} photos" if p.get("photos_count") is not None else ""
    grade = f" · comps {p['comp_grade']}" if p.get("comp_grade") else ""
    return (f"Listing: {p.get('street')}, {p.get('city')} · ${int(p.get('list_price') or 0):,} · {p.get('days_on_market')} days on market · "
            f"{p.get('price_cuts') or 0} price cut(s){photos}{grade} · {p.get('lane') or ''}")


def _numbers(p: dict[str, Any]) -> list[str]:
    lp, arv, mao = p.get("list_price"), p.get("arv"), p.get("mao")
    out = []
    ask = f"<li><strong>Asking {_e(_money(lp))}</strong>"
    if lp and p.get("sqft"):
        ask += f" · ${lp / p['sqft']:,.0f}/sf"
    if p.get("original_list_price") and lp and p["original_list_price"] > lp:
        ask += f" · down from {_e(_money(p['original_list_price']))}"
    out.append(ask + "</li>")
    method = str(p.get("arv_method") or "")
    if arv and method.endswith("_suspect"):
        out.append(f"<li>ARV {_e(_money(arv))} · <strong>looks too high, don't trust it</strong> (bad comps or bad square footage)</li>")
    elif arv:
        basis = f"{p['comp_count']} sales" if p.get("comp_count") else method.replace("_", " ") or "estimate"
        out.append(f"<li><strong>ARV {_e(_money(arv))}</strong> · {_e(basis)}</li>")
    else:
        out.append("<li>ARV: not valued yet</li>")
    if p.get("repair_cost"):
        out.append(f"<li>Repairs about {_e(_money(p['repair_cost']))}" + (f" · {_e(p['repair_tier'])}" if p.get("repair_tier") else "") + "</li>")
    if mao:
        pct = f" · {mao / lp:.0%} of asking" if lp else ""
        out.append(f"<li><strong>Internal MAO {_e(_money(mao))}</strong>{pct}</li>")
    return out


def _believe(p: dict[str, Any]) -> list[str]:
    g = (p.get("comp_grade") or "").upper()
    out = [f"<li>Comps grade <strong>{_e(g)}</strong> · {_e(GRADE_MEANING.get(g, ''))}</li>" if g else "<li>Comps grade: not graded yet</li>"]
    notes = p.get("comp_notes")
    if isinstance(notes, str) and notes.strip():
        try:
            parsed = json.loads(notes)
            notes = parsed if isinstance(parsed, list) else [str(parsed)]
        except ValueError:
            notes = [x for x in re.split(r"\s*[;\n]\s*", notes) if x]
    for n in (notes or [])[:4]:
        out.append(f"<li>{_e(n)}</li>")
    return out


def _house(p: dict[str, Any]) -> list[str]:
    bits = []
    if p.get("beds"):
        bits.append(f"{p['beds']:g} bd")
    if p.get("baths"):
        bits.append(f"{p['baths']:g} ba")
    bits.append(f"{p['sqft']:,} sf" if p.get("sqft") else "size unknown")
    bits.append(f"built {p['year_built']}" if p.get("year_built") else "year unknown")
    if p.get("lot_sqft"):
        bits.append(f"lot {p['lot_sqft']:,} sf")
    out = [f"<li>{_e(type_label(p))} · {_e(' · '.join(bits))}</li>"]
    market = [f"On market {p['days_on_market']} days" if p.get("days_on_market") is not None else "Days on market unknown",
              f"{p['price_cuts']} price cut{'s' if p['price_cuts'] != 1 else ''}" if p.get("price_cuts") else "no price cuts"]
    if p.get("back_on_market"):
        market.append("back on market")
    market.append(f"{p['photos_count']} photos" if p.get("photos_count") is not None else "photo count unknown")
    if p.get("hoa"):
        market.append(f"HOA ${int(p['hoa']):,}/mo")
    out.append(f"<li>{_e(' · '.join(market))}</li>")
    return out


def _why(analysis: dict[str, Any] | None) -> list[str]:
    a = analysis or {}
    out = []
    if a.get("hypothesis"):
        out.append(f"<li><strong>Hypothesis:</strong> {_e(a['hypothesis'])}</li>")
    if a.get("rehab_level"):
        out.append(f"<li><strong>Likely rehab:</strong> level {_e(a['rehab_level'])} of 5</li>")
    if a.get("key_unknown"):
        out.append(f"<li><strong>Biggest unknown:</strong> {_e(a['key_unknown'])}</li>")
    for k, label in (("positive_signals", "Positives"), ("negative_signals", "Negatives"), ("unknown_big_ticket", "Unknown big-ticket")):
        if a.get(k):
            out.append(f"<li><strong>{label}:</strong> " + _e("; ".join(str(x) for x in a[k][:5])) + "</li>")
    return out


def facts_html(props: list[dict[str, Any]], agent: dict[str, Any], analysis: dict[str, Any] | None = None) -> list[str]:
    """The sections above the email, as html lines (the lead card reuses them under WHERE WE STAND)."""
    out: list[str] = []
    for i, p in enumerate(props or [{}]):
        if i:
            out.append("<hr/>")
        where = ", ".join(x for x in (p.get("street"), p.get("city")) if x)
        url = p.get("listing_url") or ""
        out.append(f"<strong>{_e(type_label(p).upper())}{' · ' + _e(where) if where else ''}</strong>"
                   + (f" · <a href=\"{_e(url)}\">Listing</a>" if url else ""))
        sub = " · ".join(x for x in (f"{p['county']} County" if p.get("county") else "", f"ZIP {p['zip']}" if p.get("zip") else "",
                                      LANE_LABEL.get(p.get("lane") or "", "")) if x)
        if sub:
            out.append(_e(sub))
        out.append("")
        out += ["<strong>THE NUMBERS</strong> (internal, never sent to the agent)", "<ul>", *_numbers(p), "</ul>"]
        out += ["<strong>DO WE BELIEVE IT?</strong>", "<ul>", *_believe(p), "</ul>"]
        out += ["<strong>THE HOUSE</strong>", "<ul>", *_house(p), "</ul>"]
    why = _why(analysis)
    if why:
        out += ["<strong>WHY THIS HOUSE</strong> (the engine's read, not sent)", "<ul>", *why, "</ul>"]
    phone = pretty_phone(agent.get("phone")) if agent.get("phone") else ""
    out += ["<strong>AGENT</strong>", "<ul>",
            f"<li><strong>{_e(agent.get('name') or 'unknown')}</strong>" + (f" · {_e(agent['brokerage'])}" if agent.get("brokerage") else "") + "</li>",
            f"<li>{_e(' · '.join(x for x in (phone, agent.get('email') or 'no email') if x))}</li>", "</ul>"]
    # the plain lines older code reads (card_parts keeps them; _add_phone finds the Agent line)
    for p in props or []:
        out.append(f"<em>{_e(listing_line(p))}</em>")
        if p.get("listing_url"):
            out.append(_e(p["listing_url"]))
    out.append(f"<em>{_e(agent_line(agent))}</em>")
    return out


def email_block(to: str, subject: str, body: str, outreach_id: int, cc: list[str] | None = None) -> str:
    cc_line = f"Cc: {', '.join(cc)}\n" if cc else ""
    return f"{cc_line}To: {to}\nSubject: {subject}\n{START}\n{body}\n{END}\n(outreach #{outreach_id})"


def card_html(props: list[dict[str, Any]], agent: dict[str, Any], analysis: dict[str, Any] | None, email: str, sent: str = "") -> str:
    """The whole first-email card. `email` = the To/Subject/EMAIL block with its "(outreach #N)" line, kept verbatim."""
    out = ["<body>", *facts_html(props, agent, analysis), "<hr/>"]
    out.append("<strong>FIRST EMAIL</strong> · " + (f"sent {_e(sent)}" if sent else "what Charles sends when you drag the card to Approved"))
    out.append(_e(email))
    if not sent:
        out += ["<hr/>", _e(FOOTER)]
    out.append("</body>")
    return "\n".join(out)


def plain(html: str) -> str:
    """Asana's plain rendering of the html (tags off, links' attributes too, lines kept), for clients without html_notes."""
    import html as h
    return h.unescape(re.sub(r"<hr/>", "-" * 40, re.sub(r"</?(?!hr)[a-z]+(?: [^>]*)?/?>", "", html or ""))).strip()


def load(conn, outreach_id: int) -> tuple[dict[str, Any] | None, dict[str, Any], list[dict[str, Any]]]:
    """(outreach row, agent, properties joined with their valuation) for one card."""
    row = db.row_to_dict(conn.execute("SELECT * FROM outreach WHERE id=?", (outreach_id,)).fetchone())
    if not row:
        return None, {}, []
    agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (row["agent_id"],)).fetchone()) or {}
    pids = json.loads(row.get("property_ids") or "[]")
    props = [db.row_to_dict(r) for r in conn.execute(
        "SELECT p.*, v.lane, v.mao, v.arv, v.arv_method, v.comp_count, v.repair_cost, v.repair_tier, v.comp_grade, v.comp_notes "
        f"FROM properties p LEFT JOIN valuations v ON v.property_id=p.id WHERE p.id IN ({','.join('?' * len(pids)) or 'NULL'})", pids)]
    order = {pid: i for i, pid in enumerate(pids)}
    props.sort(key=lambda p: order.get(p["id"], 99))
    return row, agent, props


def _analysis(row: dict[str, Any]) -> dict[str, Any]:
    try:
        a = json.loads(row.get("analysis") or "{}")
        return a if isinstance(a, dict) else {}
    except ValueError:
        return {}


def write(asana, gid: str, name: str | None, html: str) -> None:
    """Name + formatted description; plain notes when the client cannot do html."""
    if name and hasattr(asana, "update_card"):
        asana.update_card(gid, name, html)
    elif hasattr(asana, "update_html_notes"):
        asana.update_html_notes(gid, html)
    else:
        asana.update_notes(gid, plain(html))


def create(asana, project: str, section: str, agent: dict[str, Any], props: list[dict[str, Any]], analysis: dict[str, Any] | None,
           to: str, subject: str, body: str, outreach_id: int) -> dict[str, Any]:
    name = card_name(agent, props)
    html = card_html(props, agent, analysis, email_block(to, subject, body, outreach_id))
    fn = getattr(asana, "create_task_html", None)
    if fn:
        return fn(project, section, name, html)
    return asana.create_task(project, section, name, plain(html))


def rerender(conn, asana, gid: str, outreach_id: int, to: str, subject: str, body: str, cc: list[str] | None = None,
             rename: bool = False) -> bool:
    """A first email card after its text changed (rewrite): the whole card is drawn again, so it keeps the layout."""
    row, agent, props = load(conn, outreach_id)
    if not row or not props:
        return False
    write(asana, gid, card_name(agent, props) if rename else None,
          card_html(props, agent, _analysis(row), email_block(to, subject, body, outreach_id, cc)))
    return True


def refresh_cards(conn, cfg: Any, asana, limit: int = 40) -> dict[str, int]:
    """Older first-email cards get the new name + layout, once each (`card_format`). Cards still waiting keep the text on the card
    (it may carry Rodrigo's hand edits); a sent card shows the email that went out. A card the lead board keeps up (the agent
    answered, or a NEXT EMAIL waits) is only renamed: lead_board draws its description in the same shape."""
    from .approvals import parse_card
    n = {"formatted": 0, "renamed": 0, "skipped": 0, "failed": 0}
    rows = [db.row_to_dict(r) for r in conn.execute(
        "SELECT o.* FROM outreach o LEFT JOIN card_format f ON f.gid=o.asana_task_id "
        "WHERE o.asana_task_id IS NOT NULL AND o.kind='first_touch' AND o.status IN ('pending_approval','approved','sent') "
        "AND (f.version IS NULL OR f.version<>?) ORDER BY o.id DESC", (VERSION,)).fetchall()]
    for row in rows:
        if n["formatted"] + n["renamed"] >= limit:
            break
        gid = row["asana_task_id"]
        try:
            try:
                t = asana.task(gid, "name,notes,completed")
            except TypeError:          # a client without the fields argument
                t = asana.task(gid)
            if not t or t.get("completed"):
                n["skipped"] += 1
                conn.execute("INSERT OR REPLACE INTO card_format(gid, version, at) VALUES (?,?,?)", (gid, VERSION, now_utc().isoformat()))
                continue
            _, agent, props = load(conn, row["id"])
            if not props:
                n["skipped"] += 1
                continue
            name = card_name(agent, props)
            in_talk = bool(conn.execute("SELECT 1 FROM messages m JOIN threads th ON th.id=m.thread_id WHERE th.agent_id=? "
                                        "AND m.direction='in' LIMIT 1", (row["agent_id"],)).fetchone()) or bool(row.get("answer_draft"))
            if in_talk:
                if hasattr(asana, "rename"):
                    asana.rename(gid, name)
                n["renamed"] += 1
            else:
                card = parse_card(t.get("notes") or "")
                if row["status"] == "sent":
                    email = email_block(row.get("to_address") or "", row.get("subject") or "", (row.get("body") or "").strip(), row["id"])
                    from .conversations import _when
                    sent = _when(row["sent_at"]) if row.get("sent_at") else "yes"
                elif card:
                    email, sent = email_block(card["to"], card["subject"], card["body"], row["id"], card.get("cc")), ""
                else:
                    n["skipped"] += 1
                    continue
                write(asana, gid, name, card_html(props, agent, _analysis(row), email, sent))
                n["formatted"] += 1
            conn.execute("INSERT OR REPLACE INTO card_format(gid, version, at) VALUES (?,?,?)", (gid, VERSION, now_utc().isoformat()))
        except Exception as e:  # noqa: BLE001 — retried next round
            log.warning("house card_format %s: %s", gid, e)
            n["failed"] += 1
    return n
