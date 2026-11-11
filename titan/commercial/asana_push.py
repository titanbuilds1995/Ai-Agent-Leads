"""Cards for the 1031 screen on its own Asana board (config commercial.asana.project; created when missing), tagged "1031-buyer".

Card name = agent · asset · city · price · cap (Rodrigo, 2026-11-11: "put the name of the agent and key things about the property as the
card names"); description = bold headlines (THE NUMBERS / DO WE BELIEVE IT? / THE PROPERTY / WHAT WE STILL NEED / BROKER) with the
OM-request email block below as plain lines, so the sender and the LoopNet-link readers still parse it. `refresh_cards` brings every
older card on the 1031 and Working Deals boards to this shape once (approvals-round stage `card_format`)."""
from __future__ import annotations

import json
import re
from typing import Any

from ..util import log, money, pretty_phone
from .om_request import om_request

from .board import COLUMNS as SECTIONS, ensure_board

BOARD_NAME = "Titan 1031 · Commercial (LoopNet)"
START, END = "=== EMAIL (what will be sent) ===", "=== END EMAIL ==="


def _k(x: Any) -> str:
    return money(x) if x else "n/a"


def _e(x: Any) -> str:
    import html
    return html.escape(str(x if x is not None else ""), quote=False)


def short_money(x: Any) -> str:
    """$4,999,999 → $5.0M; $850,000 → $850k (card names have no room for digits)."""
    if not x:
        return "price n/a"
    x = float(x)
    if x >= 1_000_000:
        v = f"{x / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return f"${v}M" if "." in v else f"${v}.0M"
    return f"${x / 1_000:.0f}k"


ASSET_LABEL = {"nnn": "NNN", "retail": "Retail", "hotel": "Hotel", "industrial": "Industrial", "office": "Office",
               "multifamily": "Multifamily", "medical": "Medical", "mixed": "Mixed use"}
NAME_SUFFIX_RE = re.compile(r"\s*,?\s*\b(MBA|CCIM|SIOR|CPM|PA|P\.A\.|LLC|Jr\.?|Sr\.?|III|II|Esq\.?|Broker|Realtor|GRI|ABR|CRS)\b\.?", re.I)


def broker_short(L: dict[str, Any]) -> str:
    """"Adriana Montes MBA" → "Adriana Montes": the name Rodrigo remembers the deal by (2026-11-11)."""
    name = NAME_SUFFIX_RE.sub("", L.get("broker_name") or "").strip(" ,·")
    return re.sub(r"\s+", " ", name) or (L.get("brokerage") or "broker unknown")


def asset_label(L: dict[str, Any]) -> str:
    ac = (L.get("asset_class") or "").lower()
    if ac in ASSET_LABEL:
        return ASSET_LABEL[ac]
    return (L.get("property_type") or ac or "Commercial").title()


def card_name(L: dict[str, Any]) -> str:
    """Rodrigo, 2026-11-11: "put the name of the agent and key things about the property as the card names … it's using up a lot of
    headspace". Agent · asset · city · price · cap (no score, no "OM request" prefix)."""
    cap = f" · {L['stated_cap']:.1f}% cap" if L.get("stated_cap") else ""
    return f"{broker_short(L)} · {asset_label(L)} · {L.get('city') or L.get('street') or '?'} · {short_money(L.get('price'))}{cap}"


def broker_line(L: dict[str, Any]) -> str:
    phone = pretty_phone(L.get("broker_phone")) if L.get("broker_phone") else (L.get("broker_phone_raw") or "")
    return f"Broker: {L.get('broker_name') or '?'} · {phone + ' · ' if phone else ''}{L.get('broker_email') or 'no email'} · {L.get('brokerage') or ''}"


def _anchor_why(L: dict[str, Any]) -> str:
    basis = L.get("offer_anchor_basis") or ""
    if "clears" in basis:
        return "the asking price already clears an 8 cap on the stated NOI"
    return basis or "stated NOI at an 8 cap"


def _occupancy_line(L: dict[str, Any]) -> str:
    occ = f"Occupancy {L['occupancy']:g}%" if L.get("occupancy") is not None else "Occupancy not stated"
    if L.get("asset_class") == "hotel":
        return occ + (" · RevPAR / ADR stated" if L.get("hotel_metrics") else " · no RevPAR / ADR")
    bits = [occ]
    if L.get("tenancy"):
        bits.append(f"{L['tenancy']} tenant")
    if L.get("lease_type"):
        bits.append(f"{str(L['lease_type']).upper()} lease")
    if L.get("remaining_term_years"):
        bits.append(f"{L['remaining_term_years']:g} yrs left on the lease")
    return " · ".join(bits)


def _market_line(L: dict[str, Any]) -> str:
    dom = L.get("days_on_market")
    if L.get("dom_basis"):
        first = f"Seen by us for {dom} days"
    else:
        first = f"On market {dom} days" if dom is not None else "Days on market unknown"
    cuts = L.get("price_cuts") or 0
    bits = [first, f"{cuts} price cut{'s' if cuts != 1 else ''}" if cuts else "no price cuts"]
    if L.get("motivation"):
        bits.append("signals: " + ", ".join(L["motivation"]))
    return " · ".join(bits)


PART_LABEL = {"cap": "cap rate", "dom_and_cuts": "time on market", "motivation": "motivation", "lease_quality": "lease",
              "brokerage": "brokerage", "asset_fit": "asset fit", "price_band": "price band", "region": "region"}


def _score_parts(L: dict[str, Any]) -> str:
    parts = L.get("parts") or L.get("score_parts") or {}
    if not isinstance(parts, dict) or not parts:
        return ""
    return " · ".join(f"{PART_LABEL.get(k, k.replace('_', ' '))} {v:g}" if isinstance(v, (int, float)) else f"{PART_LABEL.get(k, k)} {v}" for k, v in parts.items())


def facts_block(L: dict[str, Any]) -> str:
    """The plain-text facts (what older cards carried; the html card below is the one Rodrigo reads)."""
    anchor = L.get("offer_anchor")
    lines = [
        f"OFFER ANCHOR: {_k(anchor)}" + (f" ({L.get('offer_anchor_basis')})" if anchor else " (no NOI yet: ask for the books first)"),
        f"Asking {_k(L.get('price'))} · stated cap {L['stated_cap']:.2f}%" if L.get("stated_cap") else f"Asking {_k(L.get('price'))} · cap not stated",
        f"Stated NOI {_k(L.get('stated_noi'))} · back-solved cap {L['back_solved_cap']:.2f}%" if L.get("back_solved_cap") else f"Stated NOI {_k(L.get('stated_noi'))}",
        f"Tag: {L.get('tag')} · evidence grade {L.get('evidence_grade')} ({L.get('evidence_note')})",
        "Why: " + "; ".join(L.get("tag_reasons") or []) if L.get("tag_reasons") else "",
        f"{L.get('property_type') or ''}{' / ' + L['property_subtype'] if L.get('property_subtype') else ''} · {L.get('building_sf') or '?'} SF"
        f"{' · ' + str(L['units']) + ' units' if (L.get('units') or 0) > 1 else ''} · built {L.get('year_built') or '?'} · {L.get('county') or '?'} County ({L.get('region') or '?'})",
        _occupancy_line(L),
        _market_line(L),
        (f"Airport: {L.get('airport_name')} ({L.get('airport_code')}) about {L.get('airport_minutes')} min" if L.get("airport_code") else "Airport: no coordinates on the listing"),
        f"Quality: {L.get('quality')}" + (" · " + "; ".join(L["quality_reasons"]) if L.get("quality_reasons") else ""),
        f"Docs missing: {', '.join(L.get('docs_missing') or [])}" if L.get("docs_missing") else "",
        f"Gated: {', '.join(L['docs_gated'])}" if L.get("docs_gated") else "",
        f"Score {L.get('score', 0)}" + (f" · {_score_parts(L)}" if _score_parts(L) else ""),
        broker_line(L),
        L.get("listing_url") or "",
    ]
    return "\n".join(x for x in lines if x)


FOOTER = "Tagged 1031-buyer · drag this card to \"Approved · send OM request\" and Charles emails the broker at the next round."


def email_block(cfg: Any, L: dict[str, Any]) -> str:
    """The To / Subject / EMAIL-marker block the sender reads (plain lines, never inside a tag)."""
    if L.get("broker_email"):
        om = om_request(cfg, L)
        return f"To: {L['broker_email']}\nSubject: {om['subject']}\n{START}\n{om['body']}\n{END}"
    return "No broker email on LoopNet: call, or find it on the brokerage site. OM request text:\n" + om_request(cfg, L)["body"]


def card_html(L: dict[str, Any], email: str = "", extra: str = "", sent: str = "") -> str:
    """The card Rodrigo reads (2026-11-11: "give it good headlines and make things bold that need to be bold"). Asana html_notes:
    strong / ul / li / hr / a only (no p, br, th), lines kept as text, so parse_om_card() and the LoopNet-link readers still work on
    the plain rendering. `email` is the To/Subject/EMAIL block (kept verbatim: it may carry Rodrigo's hand edits); `extra` any
    plain text a card already carried (e.g. "The email also asked about …"); `sent` = when the OM request went out, if it did."""
    anchor = L.get("offer_anchor")
    price = L.get("price")
    phone = pretty_phone(L.get("broker_phone")) if L.get("broker_phone") else (L.get("broker_phone_raw") or "")
    url = L.get("listing_url") or ""
    out = ["<body>"]
    where = ", ".join(x for x in (L.get("street"), L.get("city")) if x)
    out.append(f"<strong>{_e(asset_label(L).upper())} · {_e(where)}</strong>" + (f" · <a href=\"{_e(url)}\">LoopNet</a>" if url else ""))
    out.append(f"{_e(L.get('county') or '?')} County · {_e(L.get('region') or '?')}" + (f" · {_e(L['airport_name'])} airport about {L['airport_minutes']} min" if L.get("airport_code") else ""))
    out.append("")
    out.append("<strong>THE NUMBERS</strong>")
    out.append("<ul>")
    ask = f"<li><strong>Asking {_e(_k(price))}</strong>"
    ask += f" · stated cap <strong>{L['stated_cap']:.2f}%</strong>" if L.get("stated_cap") else " · <strong>no cap rate stated</strong>"
    out.append(ask + "</li>")
    if L.get("stated_noi"):
        noi = f"<li>Stated NOI {_e(_k(L['stated_noi']))}"
        if L.get("back_solved_cap"):
            noi += f" · that is a <strong>{L['back_solved_cap']:.2f}%</strong> cap at asking"
        out.append(noi + "</li>")
    else:
        out.append("<li>No NOI on the listing: the books decide</li>")
    if anchor:
        out.append(f"<li><strong>Offer anchor {_e(_k(anchor))}</strong> · {_e(_anchor_why(L))}</li>")
    else:
        out.append("<li><strong>Offer anchor:</strong> none yet, ask for the books first</li>")
    out.append("</ul>")
    out.append("<strong>DO WE BELIEVE IT?</strong>")
    out.append("<ul>")
    out.append(f"<li><strong>{_e(L.get('tag') or '?')}</strong> · evidence grade <strong>{_e(L.get('evidence_grade') or '?')}</strong>"
               + (f" · {_e(L.get('evidence_note'))}" if L.get("evidence_note") else "") + "</li>")
    for r in L.get("tag_reasons") or []:
        out.append(f"<li>{_e(r)}</li>")
    if L.get("quality") and str(L["quality"]).lower() != "pass":
        out.append(f"<li>Quality screen: <strong>{_e(L['quality'])}</strong>" + (" · " + _e("; ".join(L["quality_reasons"])) if L.get("quality_reasons") else "") + "</li>")
    elif L.get("quality_reasons"):
        out.append(f"<li>Quality screen: pass · {_e('; '.join(L['quality_reasons']))}</li>")
    out.append("</ul>")
    out.append("<strong>THE PROPERTY</strong>")
    out.append("<ul>")
    ptype = f"{L.get('property_type') or asset_label(L)}{' / ' + L['property_subtype'] if L.get('property_subtype') else ''}"
    size = f"{L['building_sf']:,} SF" if isinstance(L.get("building_sf"), (int, float)) and L.get("building_sf") else "size unknown"
    units = f" · {L['units']} units" if (L.get("units") or 0) > 1 else ""
    out.append(f"<li>{_e(ptype)} · {_e(size)}{units} · built {_e(L.get('year_built') or 'unknown')}</li>")
    out.append(f"<li>{_e(_occupancy_line(L))}</li>")
    out.append(f"<li>{_e(_market_line(L))}</li>")
    out.append("</ul>")
    if L.get("docs_missing") or L.get("docs_gated"):
        out.append("<strong>WHAT WE STILL NEED</strong>")
        out.append("<ul>")
        if L.get("docs_missing"):
            out.append(f"<li>Missing: {_e(', '.join(L['docs_missing']))}</li>")
        if L.get("docs_gated"):
            out.append(f"<li>Behind a LoopNet gate: {_e(', '.join(L['docs_gated']))}</li>")
        out.append("</ul>")
    out.append("<strong>BROKER</strong>")
    out.append("<ul>")
    out.append(f"<li><strong>{_e(L.get('broker_name') or 'unknown')}</strong>" + (f" · {_e(L['brokerage'])}" if L.get("brokerage") else "") + "</li>")
    contact = " · ".join(x for x in (phone, L.get("broker_email") or "no email on LoopNet") if x)
    out.append(f"<li>{_e(contact)}</li>")
    out.append("</ul>")
    score = f"Engine score {L.get('score', 0)} of 100"
    if _score_parts(L):
        score += f" · {_e(_score_parts(L))}"
    out.append(score)
    if url:
        out.append(_e(url))
    if email:
        out.append("<hr/>")
        out.append(f"<strong>OM REQUEST</strong> · " + (f"sent {_e(sent)}" if sent else "what Charles sends when you drag the card to \"Approved · send OM request\""))
        out.append(_e(email))
    if extra and extra.strip():
        out.append("<hr/>")
        out.append(_e(extra.strip()))
    out.append("<hr/>")
    out.append(_e(FOOTER))
    out.append("</body>")
    return "\n".join(out)


def card_notes(cfg: Any, L: dict[str, Any], with_email: bool) -> str:
    """Plain-text fallback (an Asana client without html support, and the tests' reading of the card)."""
    parts = [facts_block(L)]
    if with_email:
        parts.append(email_block(cfg, L))
    parts.append(FOOTER)
    return "\n\n".join(parts)


def create_card(asana, project: str, section: str, cfg: Any, L: dict[str, Any], with_email: bool, extra: str = "") -> dict[str, Any]:
    """One 1031 card: agent-first name, formatted description (plain text when the client cannot do html)."""
    email = email_block(cfg, L) if with_email else ""
    fn = getattr(asana, "create_task_html", None)
    if fn:
        return fn(project, section, card_name(L), card_html(L, email=email, extra=extra))
    return asana.create_task(project, section, card_name(L), card_notes(cfg, L, with_email=with_email) + (("\n\n" + extra) if extra else ""))


def section_for(L: dict[str, Any]) -> str:
    """Every new card lands in New (Rodrigo sorts them into his categories by dragging); the card text says CLEAN / NEEDS-OM."""
    return "New"


def card_specs(cfg: Any, ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What goes on the board this run: the CLEAN top tier and the NEEDS-OM request list, as {section, name, notes, listing_id}."""
    top_n = int(cfg.get("commercial.asana.push_top_n", 25))
    om_n = int(cfg.get("commercial.asana.needs_om_n", 40))
    top = [L for L in ranked if L.get("in_box") and L.get("tag") == "CLEAN"][:top_n]
    om = [L for L in ranked if L.get("in_box") and L.get("tag") == "NEEDS-OM"][:om_n]
    specs = [{"section": "New", "kind": "top", "name": card_name(L), "notes": card_notes(cfg, L, with_email=False), "listing_id": L["listing_id"], "L": L} for L in top]
    specs += [{"section": "New", "kind": "om", "name": card_name(L), "notes": card_notes(cfg, L, with_email=True), "listing_id": L["listing_id"], "L": L} for L in om]
    return specs


LISTING_URL_RE = re.compile(r"loopnet\.com/Listing/(?:[^/\s]+/)?(\d+)/?", re.I)


def already_on_board(asana, project: str) -> dict[str, str]:
    """{listing_id: task gid} for every open card on the board, read from the LoopNet link in its notes, so a card posted by hand
    (a cloud session) or from another machine's database is never doubled. One card per listing (Rodrigo: never two cards)."""
    out: dict[str, str] = {}
    try:
        tasks = asana.project_tasks(project)
    except Exception as e:  # noqa: BLE001
        log.warning("commercial: could not read the board: %s", e)
        return out
    for t in tasks:
        notes = t.get("notes") or ""
        if not notes:
            try:
                notes = (asana.task(t["gid"]) or {}).get("notes") or ""
            except Exception:  # noqa: BLE001
                continue
        m = LISTING_URL_RE.search(notes)
        if m:
            out.setdefault(m.group(1), t["gid"])
    return out


def push(conn, cfg: Any, asana, ranked: list[dict[str, Any]]) -> dict[str, Any]:
    """Create the board (once), the sections, the tag, then one card per listing not yet carded. Idempotent by listing_id."""
    project = cfg.get("commercial.asana.project")
    if not project:
        project = asana.ensure_project(BOARD_NAME, "Commercial listings for the 1031 buyer (LoopNet). Cards are a worklist; nothing sends from here.")
        log.info("commercial: created board %s (%s); put it in config.yaml commercial.asana.project", BOARD_NAME, project)
    secs = ensure_board(asana, project)
    tag = asana.ensure_tag(cfg.get("commercial.tag", "1031-buyer"))
    made, skipped = 0, 0
    on_board = already_on_board(asana, project)       # cards posted by hand or by another machine's DB count too
    for spec in card_specs(cfg, ranked):
        kind = spec["kind"]
        col = "asana_om_task_id" if kind == "om" else "asana_task_id"
        row = conn.execute(f"SELECT asana_task_id, asana_om_task_id FROM commercial_listings WHERE listing_id=?", (spec["listing_id"],)).fetchone()
        if (row and (row[0] or row[1])) or spec["listing_id"] in on_board:
            if not (row and (row[0] or row[1])) and spec["listing_id"] in on_board:
                conn.execute(f"UPDATE commercial_listings SET {col}=? WHERE listing_id=?", (on_board[spec["listing_id"]], spec["listing_id"]))
            skipped += 1
            continue
        task = create_card(asana, project, secs[spec["section"]], cfg, spec["L"], with_email=(kind == "om"))
        if tag:
            asana.tag_task(task["gid"], tag)
        conn.execute(f"UPDATE commercial_listings SET {col}=? WHERE listing_id=?", (task["gid"], spec["listing_id"]))
        made += 1
    return {"project": project, "cards": made, "already_on_board": skipped}


EXTRA_RE = re.compile(r"^The email also asked about listing\(s\)[^\n]*$", re.M)
FORMAT_VERSION = "2"      # bump to re-render every card once more


def listing_for(conn, listing_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT data FROM commercial_listings WHERE listing_id=?", (listing_id,)).fetchone()
    try:
        L = json.loads(row[0]) if row and row[0] else {}
    except (json.JSONDecodeError, TypeError):
        L = {}
    if L:
        L.setdefault("listing_id", listing_id)
    return L


def refresh_cards(conn, cfg: Any, asana, limit: int = 40) -> dict[str, int]:
    """Bring every older 1031 card (both boards) to the new name + formatted description, once per card (commercial_kv
    `card_format:<gid>`). The email block, any "also asked about" line and the sent time are kept; nothing moves."""
    from .outreach import parse_om_card
    n = {"updated": 0, "skipped": 0, "errors": 0}
    projects = [p for p in (cfg.get("commercial.asana.project"), cfg.get("commercial.asana.working_project")) if p]
    seen: set[str] = set()
    budget = limit
    for project in projects:
        try:
            tasks = [t for t in asana.project_tasks(project) if not t.get("completed")]
        except Exception as e:  # noqa: BLE001
            log.warning("card_format: could not read %s: %s", project, e)
            n["errors"] += 1
            continue
        for t in tasks:
            gid = t["gid"]
            if gid in seen:
                continue
            seen.add(gid)
            key = f"card_format:{gid}"
            if (conn.execute("SELECT value FROM commercial_kv WHERE key=?", (key,)).fetchone() or [None])[0] == FORMAT_VERSION:
                continue
            if budget <= 0:
                return n
            budget -= 1
            try:
                full = asana.task(gid) or {}
                notes = full.get("notes") or ""
                m = LISTING_URL_RE.search(notes)
                L = listing_for(conn, m.group(1)) if m else {}
                if not L:
                    n["skipped"] += 1
                    _mark(conn, key)
                    continue
                om = parse_om_card(notes)
                email = f"To: {om['to']}\nSubject: {om['subject']}\n{START}\n{om['body']}\n{END}" if om else ""
                extra = "\n".join(EXTRA_RE.findall(notes))
                sent = conn.execute("SELECT sent_at FROM commercial_threads WHERE listing_id=? AND sent_at IS NOT NULL ORDER BY sent_at LIMIT 1",
                                    (L["listing_id"],)).fetchone()
                sent_when = (sent[0] or "")[:10] if sent else ""
                html = card_html(L, email=email, extra=extra, sent=sent_when)
                fn = getattr(asana, "update_card", None)
                if fn:
                    fn(gid, card_name(L), html)
                else:
                    asana.rename(gid, card_name(L))
                    asana.update_html_notes(gid, html)
                _mark(conn, key)
                n["updated"] += 1
            except Exception as e:  # noqa: BLE001
                log.warning("card_format %s: %s", gid, e)
                n["errors"] += 1
    return n


def _mark(conn, key: str) -> None:
    conn.execute("INSERT INTO commercial_kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, FORMAT_VERSION))
