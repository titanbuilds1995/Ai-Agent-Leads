"""Cards for the 1031 screen on its own Asana board (config commercial.asana.project; created when missing), tagged "1031-buyer".

Columns: Top tier (CLEAN, ranked) · Needs OM (broker request drafted) · Suspect · Contacted · Skip. Every card carries the offer
anchor, the broker's phone and email (tap to call), the evidence grade, the tag reasons and the LoopNet link. Nothing sends:
the approvals engine only reads the Email Approvals board, so these cards are a worklist, not a send queue."""
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


def card_name(L: dict[str, Any]) -> str:
    cap = f" · {L['stated_cap']:.2f}% cap" if L.get("stated_cap") else ""
    return f"{L.get('score', 0):>2} · {(L.get('asset_class') or L.get('property_type') or 'commercial').upper()} · {L.get('street')}, {L.get('city')} · {_k(L.get('price'))}{cap}"


def broker_line(L: dict[str, Any]) -> str:
    phone = pretty_phone(L.get("broker_phone")) if L.get("broker_phone") else (L.get("broker_phone_raw") or "")
    return f"Broker: {L.get('broker_name') or '?'} · {phone + ' · ' if phone else ''}{L.get('broker_email') or 'no email'} · {L.get('brokerage') or ''}"


def facts_block(L: dict[str, Any]) -> str:
    anchor = L.get("offer_anchor")
    lines = [
        f"OFFER ANCHOR: {_k(anchor)}" + (f" ({L.get('offer_anchor_basis')})" if anchor else " (no NOI yet: ask for the books first)"),
        f"Asking {_k(L.get('price'))} · stated cap {L['stated_cap']:.2f}%" if L.get("stated_cap") else f"Asking {_k(L.get('price'))} · cap not stated",
        f"Stated NOI {_k(L.get('stated_noi'))} · back-solved cap {L['back_solved_cap']:.2f}%" if L.get("back_solved_cap") else f"Stated NOI {_k(L.get('stated_noi'))}",
        f"Tag: {L.get('tag')} · evidence grade {L.get('evidence_grade')} ({L.get('evidence_note')})",
        "Why: " + "; ".join(L.get("tag_reasons") or []) if L.get("tag_reasons") else "",
        f"{L.get('property_type') or ''}{' / ' + L['property_subtype'] if L.get('property_subtype') else ''} · {L.get('building_sf') or '?'} SF"
        f"{' · ' + str(L['units']) + ' units' if (L.get('units') or 0) > 1 else ''} · built {L.get('year_built') or '?'} · {L.get('county') or '?'} County ({L.get('region') or '?'})",
        (f"Occupancy {L.get('occupancy') or '?'}%" + (f" · RevPAR/ADR {'stated' if L.get('hotel_metrics') else 'not stated'}" if L.get("asset_class") == "hotel"
         else f" · {L.get('tenancy') or '?'} tenant · lease {L.get('lease_type') or '?'}"
              + (f" · {L['remaining_term_years']} yrs left" if L.get("remaining_term_years") else ""))),
        (f"Seen by us for {L.get('days_on_market')} days (LoopNet listing date comes with the property sheet)" if L.get("dom_basis")
         else f"On market {L.get('days_on_market') if L.get('days_on_market') is not None else '?'} days") + f" · {L.get('price_cuts') or 0} price cut(s)"
        + (f" · signals: {', '.join(L['motivation'])}" if L.get("motivation") else ""),
        f"Docs missing: {', '.join(L.get('docs_missing') or [])}" if L.get("docs_missing") else "",
        f"Gated: {', '.join(L['docs_gated'])}" if L.get("docs_gated") else "",
        f"Score parts: {json.dumps(L.get('parts') or L.get('score_parts') or {})}",
        broker_line(L),
        L.get("listing_url") or "",
    ]
    return "\n".join(x for x in lines if x)


def card_notes(cfg: Any, L: dict[str, Any], with_email: bool) -> str:
    parts = [facts_block(L)]
    if with_email and L.get("broker_email"):
        om = om_request(cfg, L)
        parts.append(f"To: {L['broker_email']}\nSubject: {om['subject']}\n{START}\n{om['body']}\n{END}")
    elif with_email:
        parts.append("No broker email on LoopNet: call, or find it on the brokerage site. OM request text:\n" + om_request(cfg, L)["body"])
    parts.append("Tagged 1031-buyer · this board never sends anything on its own.")
    return "\n\n".join(parts)


def section_for(L: dict[str, Any]) -> str:
    """Every new card lands in New (Rodrigo sorts them into his categories by dragging); the card text says CLEAN / NEEDS-OM."""
    return "New"


def card_specs(cfg: Any, ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What goes on the board this run: the CLEAN top tier and the NEEDS-OM request list, as {section, name, notes, listing_id}."""
    top_n = int(cfg.get("commercial.asana.push_top_n", 25))
    om_n = int(cfg.get("commercial.asana.needs_om_n", 40))
    top = [L for L in ranked if L.get("in_box") and L.get("tag") == "CLEAN"][:top_n]
    om = [L for L in ranked if L.get("in_box") and L.get("tag") == "NEEDS-OM"][:om_n]
    specs = [{"section": "New", "name": card_name(L), "notes": card_notes(cfg, L, with_email=False), "listing_id": L["listing_id"]} for L in top]
    specs += [{"section": "New", "name": "OM request · " + card_name(L), "notes": card_notes(cfg, L, with_email=True), "listing_id": L["listing_id"]} for L in om]
    return specs


LISTING_URL_RE = re.compile(r"loopnet\.com/Listing/(?:[^/\s]+/)?(\d+)/?", re.I)


def already_on_board(asana, project: str) -> dict[tuple[str, str], str]:
    """{(kind, listing_id): task gid} for every open card on the board, read from the LoopNet link in its notes, so a card posted
    by hand (a cloud session) or from another machine's database is never doubled."""
    out: dict[tuple[str, str], str] = {}
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
            out[("om" if (t.get("name") or "").startswith("OM request") else "top", m.group(1))] = t["gid"]
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
        kind = "om" if spec["name"].startswith("OM request") else "top"
        col = "asana_om_task_id" if kind == "om" else "asana_task_id"
        row = conn.execute(f"SELECT {col} FROM commercial_listings WHERE listing_id=?", (spec["listing_id"],)).fetchone()
        if (row and row[0]) or (kind, spec["listing_id"]) in on_board:
            if not (row and row[0]) and (kind, spec["listing_id"]) in on_board:
                conn.execute(f"UPDATE commercial_listings SET {col}=? WHERE listing_id=?", (on_board[(kind, spec["listing_id"])], spec["listing_id"]))
            skipped += 1
            continue
        task = asana.create_task(project, secs[spec["section"]], spec["name"], spec["notes"])
        if tag:
            asana.tag_task(task["gid"], tag)
        conn.execute(f"UPDATE commercial_listings SET {col}=? WHERE listing_id=?", (task["gid"], spec["listing_id"]))
        made += 1
    return {"project": project, "cards": made, "already_on_board": skipped}
