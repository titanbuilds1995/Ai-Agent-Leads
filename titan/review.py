"""Review summary for Rodrigo: built whenever a conversation reaches meaningful engagement (agent invites an offer, shares
a number, confirms flexibility, or asks something we should not answer automatically). Rodrigo decides the price; nothing
numeric goes to the agent until he approves it with `titan.cli approve-offer`.

Written to reports/review/<date>_<property id>.md and listed in the morning report; cloud sessions post it to the Asana
board (Needs Rodrigo)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import db
from .util import iso, today_et
from .valuation import preliminary_range


def _money(v: Any) -> str:
    return f"${int(v):,}" if v else "?"


def build(conn, cfg: Any, thread_id: int, property_id: int | None = None, reason: str = "") -> dict[str, Any]:
    t = db.row_to_dict(conn.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone())
    pids = json.loads(t["property_ids"] or "[]")
    pid = property_id or (pids[0] if pids else None)
    p = db.row_to_dict(conn.execute(
        "SELECT p.*, v.arv, v.arv_method, v.comp_count, v.repair_tier, v.repair_cost, v.rating, s.score, s.summary AS score_summary "
        "FROM properties p LEFT JOIN valuations v ON v.property_id=p.id LEFT JOIN scores s ON s.property_id=p.id WHERE p.id=?", (pid,)).fetchone()) or {}
    agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (t["agent_id"],)).fetchone()) or {}
    msgs = [db.row_to_dict(r) for r in conn.execute("SELECT direction, body, classification, created_at FROM messages WHERE thread_id=? ORDER BY id", (thread_id,))]
    said = [m for m in msgs if m["direction"] == "in"]
    agent_text = " ".join(m["body"] or "" for m in said).lower()

    history = []
    for h in json.loads(p.get("price_history") or "[]") if isinstance(p.get("price_history"), str) else (p.get("price_history") or []):
        if h.get("price"):
            history.append(f"{h.get('date') or '?'} {h.get('event') or ''} {_money(h.get('price'))}".strip())
    facts = [f"{p.get('beds') or '?'} bd / {p.get('baths') or '?'} ba", f"{p['sqft']:,} sq ft" if p.get("sqft") else "sq ft unknown",
             f"built {p['year_built']}" if p.get("year_built") else None, f"lot {p['lot_sqft']:,} sq ft" if p.get("lot_sqft") else None,
             f"{p['days_on_market']} days on market" if p.get("days_on_market") is not None else None,
             f"{p['price_cuts']} price cut(s)" if p.get("price_cuts") else None]
    missing = []
    for key, pat, label in (("timing", r"timing|timeline|clos(e|ing)|\d+\s*days|weeks?|months?|asap|soon|date", "seller's timing"),
                            ("condition", r"roof|\bac\b|a/c|condition|repair|updated|renovat", "interior condition (only the listing remarks so far)"),
                            ("expectations", r"\$|\bnet\b|number|expect|hoping|bottom|\d{3}k", "seller's price expectations"),
                            ("occupancy", r"vacant|occupied|tenant|lease", "occupancy")):
        if not re.search(pat, agent_text):
            missing.append(label)
    if not p.get("sqft"):
        missing.append("square footage")
    rng = preliminary_range(p, cfg) if p else None
    flexible = (p.get("lead_status") == "FLEXIBLE") or bool(re.search(r"motivated|flexible|negotiable|some room|open to offers", agent_text))
    if reason.startswith("agent shared a number"):
        nxt = "Decide whether the agent's number can work; if so, approve a price and I will draft the written offer."
    elif "invited an offer" in reason:
        nxt = "Review the numbers and approve a specific price (or a range we may share); I will draft it for your OK."
    elif flexible:
        nxt = "Seller flexibility is stated by the agent (not yet confirmed with a number). Consider a call, or approve a price."
    else:
        nxt = "Read the conversation and decide: call the agent, ask one more question, or approve a price."

    lines = [f"# Review: {p.get('street')}, {p.get('city')} {p.get('zip') or ''}".rstrip(),
             f"_Prepared {today_et()} · reason: {reason or 'meaningful engagement'}_", "",
             f"**Listing:** {p.get('listing_url') or '(no link)'}",
             f"**Asking:** {_money(p.get('list_price'))}" + (f" (originally {_money(p.get('original_list_price'))})" if (p.get('original_list_price') or 0) > (p.get('list_price') or 0) else ""),
             "**Price history:** " + ("; ".join(history[-5:]) if history else "none on file"),
             "**Verified details (from the listing):** " + ", ".join(f for f in facts if f),
             "**Listing remarks:** " + ((p.get("remarks") or "")[:600] or "none"),
             f"**Agent:** {agent.get('name') or '?'} · {agent.get('email') or 'no email'} · {agent.get('phone') or 'no phone'} · {agent.get('brokerage') or ''}",
             "", "## What the agent actually said"]
    lines += [f"- {m['created_at'][:10]} ({m['classification'] or 'reply'}): \"{(m['body'] or '').strip()[:400]}\"" for m in said] or ["- (nothing yet)"]
    lines += ["", f"**Signal:** {'agent says the seller has flexibility (not yet confirmed with a number)' if flexible else 'agent is responsive / willing to discuss'}",
              "", "## Missing information", *(f"- {m}" for m in missing or ["nothing obvious"]), "",
              "## Preliminary internal range (screening only — NOT an approved offer, never sent)"]
    if rng:
        lines += [f"- {_money(rng['low'])} – {_money(rng['high'])} · uncertainty: {rng['uncertainty']}", *(f"- assumes {a}" for a in rng["assumptions"])]
    else:
        lines += ["- not enough information to estimate"]
    lines += ["", f"## Recommended next step\n{nxt}", "",
              f"_To approve a price: `.venv/bin/python -m titan.cli approve-offer {pid} <price>` (creates a Gmail draft for you to read; nothing sends)._"]
    md = "\n".join(lines)
    out = cfg.root / "reports" / "review" / f"{today_et()}_{pid}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    conn.execute("UPDATE threads SET updated_at=? WHERE id=?", (iso(), thread_id))
    return {"path": str(out), "markdown": md, "property_id": pid, "range": rng, "missing": missing, "flexible": flexible}


def pending(cfg: Any) -> list[Path]:
    d = cfg.root / "reports" / "review"
    return sorted(d.glob("*.md")) if d.exists() else []
