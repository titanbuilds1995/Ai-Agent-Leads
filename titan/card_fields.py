"""Phone and Address as Asana fields on every lead card (Rodrigo, 2026-11-19: "a field that I can click on for the number and
also a field for the address").

Two workspace text fields, "Phone" and "Address", are put on the Email Approvals, Warm Leads, 1031 and Working Deals boards
(shown on the card front). Every approvals round fills them from the database: a house card gets the listing agent's phone and
the house's address, a 1031 card the broker's phone and the property's address. A card is written only when a value changed
(`card_fields` table), so after the first round this is a handful of calls. Rodrigo can type into the fields himself; the
engine only overwrites them when its own value changes."""
from __future__ import annotations

import json
import logging
from typing import Any

from . import db
from .util import now_utc, pretty_phone

log = logging.getLogger(__name__)
PHONE, ADDRESS = "Phone", "Address"


def _addr(p: dict[str, Any]) -> str:
    tail = " ".join(x for x in [p.get("state") or "FL", p.get("zip") or ""] if x)
    return ", ".join(x for x in [p.get("street"), p.get("city"), tail] if x)


def house_values(conn) -> dict[str, tuple[str, str]]:
    """{card gid: (phone, address)} for every house card the engine made."""
    out: dict[str, tuple[str, str]] = {}
    rows = conn.execute("SELECT o.asana_task_id, o.property_ids, a.phone FROM outreach o LEFT JOIN agents a ON a.id=o.agent_id "
                        "WHERE o.asana_task_id IS NOT NULL AND o.asana_task_id<>'' ORDER BY o.id").fetchall()
    for r in rows:
        gid, pids, phone = r[0], r[1], r[2]
        try:
            ids = json.loads(pids or "[]")
        except ValueError:
            ids = []
        props = [db.row_to_dict(x) for x in conn.execute(
            f"SELECT id, street, city, state, zip FROM properties WHERE id IN ({','.join('?' * len(ids)) or 'NULL'})", ids)]
        props.sort(key=lambda p: ids.index(p["id"]) if p["id"] in ids else 99)   # the card's own house order
        address = " + ".join(_addr(p) for p in props if p.get("street"))
        out[gid] = (pretty_phone(phone) if phone else "", address)
    return out


def commercial_values(conn) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    try:
        rows = conn.execute("SELECT asana_task_id, asana_om_task_id, broker_phone, street, city, state, zip FROM commercial_listings").fetchall()
    except Exception:  # noqa: BLE001 — no commercial table on this database
        return out
    for r in rows:
        d = db.row_to_dict(r)
        vals = (pretty_phone(d.get("broker_phone")) if d.get("broker_phone") else "", _addr(d) if d.get("street") else "")
        for gid in {d.get("asana_task_id"), d.get("asana_om_task_id")} - {None, ""}:
            out[gid] = vals
    return out


def boards(cfg: Any) -> list[str]:
    out = [cfg.get("outreach.asana.project"), cfg.get("outreach.asana.warm_leads.project")]
    if cfg.get("commercial.enabled"):
        out += [cfg.get("commercial.asana.project"), cfg.get("commercial.asana.working_project")]
    return [str(p) for p in out if p]


def sync(conn, cfg: Any, asana, limit: int = 300) -> dict[str, int]:
    """Make sure both fields are on the boards, then write the values that changed (up to `limit` cards a round)."""
    n = {"written": 0, "unchanged": 0, "failed": 0}
    if not cfg.get("outreach.asana.card_fields", True) or not hasattr(asana, "ensure_text_field"):
        return n
    phone_f, addr_f = asana.ensure_text_field(PHONE), asana.ensure_text_field(ADDRESS)
    for p in boards(cfg):
        for f in (phone_f, addr_f):
            try:
                asana.ensure_project_field(p, f)
            except Exception as e:  # noqa: BLE001 — retried next round
                log.warning("card fields: board %s: %s", p, e)
    seen = {r[0]: (r[1] or "", r[2] or "") for r in conn.execute("SELECT gid, phone, address FROM card_fields")}
    values = {**house_values(conn), **commercial_values(conn)}
    for gid, (phone, address) in values.items():
        if n["written"] >= limit:
            break
        if seen.get(gid) == (phone, address) or not (phone or address):
            n["unchanged"] += 1
            continue
        try:
            asana.set_fields(gid, {phone_f: phone, addr_f: address})
        except Exception as e:  # noqa: BLE001 — a deleted card or one on a board without the field: noted so it is not retried every round
            log.info("card fields %s: %s", gid, e)
            n["failed"] += 1
        else:
            n["written"] += 1
        conn.execute("INSERT OR REPLACE INTO card_fields(gid, phone, address, at) VALUES (?,?,?,?)",
                     (gid, phone, address, now_utc().isoformat()))
    conn.commit()
    return n
