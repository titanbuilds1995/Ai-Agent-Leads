"""The only path by which a price reaches an agent: Rodrigo approves a specific number for one house.
Builds the written offer and leaves it as a Gmail draft (Titan/Review) or a local draft file. Nothing is sent."""
from __future__ import annotations

import json
from typing import Any

from . import db
from .outreach.compose import compose_offer_email
from .util import iso, log, today_et


def approve_offer(conn, cfg: Any, property_id: int, price: int, gmail=None, note: str | None = None) -> dict[str, Any]:
    p = db.row_to_dict(conn.execute("SELECT * FROM properties WHERE id=?", (property_id,)).fetchone())
    if not p:
        raise ValueError(f"no property with id {property_id}")
    agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (p["agent_id"],)).fetchone()) or {}
    if not agent.get("email"):
        raise ValueError(f"no agent email on file for {p['street']}; call {agent.get('name') or 'the agent'} instead")
    if db.is_dnc(conn, agent_id=agent.get("id"), email=agent.get("email")):
        raise ValueError(f"{agent.get('email')} is on do-not-contact")
    d = compose_offer_email(cfg, agent, p, int(price))
    t = conn.execute("SELECT external_id FROM threads WHERE agent_id=? AND channel='email' AND property_ids LIKE ? ORDER BY id DESC LIMIT 1",
                     (agent["id"], f"%{property_id}%")).fetchone()
    status, ids = "draft", {}
    if gmail is not None:
        ids = gmail.create_draft(agent["email"], d["subject"], d["body"], label=cfg["outreach"]["gmail_review_label"],
                                 thread_id=t["external_id"] if t else None)
    else:
        out = cfg.path("reports") / "drafts" / today_et() / f"offer_{property_id}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"To: {agent['email']}\nSubject: {d['subject']}\n\n{d['body']}")
        ids = {"local": str(out)}
    conn.execute("INSERT INTO outreach(agent_id,property_ids,channel,kind,status,subject,body,to_address,gmail_draft_id,gmail_thread_id,run_date,created_at) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                 (agent["id"], json.dumps([property_id]), "email", "offer:approved", status, d["subject"], d["body"], agent["email"],
                  ids.get("draft_id"), ids.get("thread_id"), today_et(), iso()))
    from .learning import record_price
    record_price(conn, cfg, property_id, int(price))      # stage 4 data: his number next to the engine's internal range
    db.set_lead_status(conn, property_id, "OFFER_DRAFTED", f"Rodrigo approved ${int(price):,}" + (f" ({note})" if note else ""))
    log.info("approved offer %s for property %s → %s", price, property_id, ids)
    return {"property": p["street"], "price": int(price), "to": agent["email"], "status": status, **ids}
