"""DASHBOARD SYNC: snapshot everything the engine knows into one JSON and push it to Vercel Blob.

The Vercel site (dashboard/) reads that snapshot behind a password. The engine stays the only writer; the site never
touches the database. Runs at the end of run-daily, check-replies and report, and via `make sync-dashboard`.
Secrets never enter the snapshot. Agent contact details do (that is the CRM), so the blob path is fixed and the site
is password-gated; see dashboard/README.md.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import requests

from . import db
from .util import iso, log, now_utc, parse_iso, today_et

BLOB_API = "https://blob.vercel-storage.com"
BLOB_PATH = "titan/snapshot.json"
MAX_PROPERTIES = 400
MAX_EVENTS = 400
MAX_OUTREACH = 300


def _j(s: str | None, default):
    try:
        return json.loads(s) if s else default
    except Exception:  # noqa: BLE001
        return default


def _agent(conn, aid) -> dict[str, Any] | None:
    if not aid:
        return None
    r = conn.execute("SELECT id, name, brokerage, phone, email, email_confidence, email_source, is_office_line FROM agents WHERE id=?", (aid,)).fetchone()
    return db.row_to_dict(r) if r else None


def build_snapshot(conn, cfg, include_connections: bool = False) -> dict[str, Any]:
    run_date = today_et()
    since24 = iso(now_utc() - timedelta(hours=24))
    snap: dict[str, Any] = {
        "generated_at": iso(), "run_date": run_date, "mode": cfg.mode, "channel": cfg.channel,
        "market": {"counties": cfg.get("market.counties"), "price_min": cfg.get("market.price_min"), "price_max": cfg.get("market.price_max")},
        "formula": cfg["formula"], "terms": cfg["outreach"]["terms"],
        "funnel": db.funnel(conn, run_date),
        "usage": {"last_24h": db.usage_summary(conn, since24), "all_time": db.usage_summary(conn)},
    }
    # --- runs (last 40 steps) --------------------------------------------------------------
    runs = conn.execute("SELECT run_date, step, started_at, finished_at, status, counts, notes FROM daily_runs ORDER BY id DESC LIMIT 40").fetchall()
    snap["runs"] = [dict(db.row_to_dict(r), counts=_j(r["counts"], {})) for r in runs]
    # --- properties (ranked) ---------------------------------------------------------------
    rows = conn.execute(
        "SELECT p.id, p.street, p.city, p.county, p.zip, p.list_price, p.original_list_price, p.price_cuts, p.days_on_market, p.beds, p.baths, p.sqft, "
        "p.property_type, p.listing_url, p.source, p.listing_status, p.vacant, p.status_flags, p.lead_status, p.lead_status_at, p.lead_note, p.first_seen, p.last_seen, "
        "p.remarks, p.agent_id, s.score, s.cited, s.summary, s.method, s.scored_at, "
        "v.arv, v.arv_method, v.comp_count, v.repair_tier, v.repair_cost, v.mao, v.offer_price, v.spread_pct, v.rating "
        "FROM properties p LEFT JOIN scores s ON s.property_id=p.id LEFT JOIN valuations v ON v.property_id=p.id "
        "ORDER BY CASE p.lead_status WHEN 'HOT_LEAD' THEN 0 WHEN 'NEEDS_CALL' THEN 1 ELSE 2 END, "
        "CASE v.rating WHEN 'HOT' THEN 0 WHEN 'WARM' THEN 1 WHEN 'COLD' THEN 2 ELSE 3 END, s.score DESC, p.last_seen DESC LIMIT ?", (MAX_PROPERTIES,)).fetchall()
    agents_cache: dict[int, dict[str, Any] | None] = {}
    props = []
    for r in rows:
        d = db.row_to_dict(r)
        d["cited"] = _j(d.pop("cited"), [])
        d["status_flags"] = _j(d.pop("status_flags"), [])
        d["remarks"] = (d.get("remarks") or "")[:700]
        aid = d.pop("agent_id")
        if aid not in agents_cache:
            agents_cache[aid] = _agent(conn, aid)
        d["agent"] = agents_cache[aid]
        props.append(d)
    snap["properties"] = props
    snap["properties_total"] = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    # --- agents with counts ----------------------------------------------------------------
    ag = conn.execute(
        "SELECT a.id, a.name, a.brokerage, a.phone, a.email, a.email_confidence, a.email_source, a.is_office_line, a.updated_at, "
        "(SELECT COUNT(*) FROM properties p WHERE p.agent_id=a.id) AS listings, "
        "(SELECT COUNT(*) FROM outreach o WHERE o.agent_id=a.id AND o.status IN ('sent','draft','queued')) AS touches, "
        "(SELECT MAX(m.created_at) FROM messages m JOIN threads t ON t.id=m.thread_id WHERE t.agent_id=a.id AND m.direction='in') AS last_reply_at, "
        "(SELECT 1 FROM do_not_contact d WHERE d.agent_id=a.id OR (a.phone IS NOT NULL AND d.phone=a.phone) OR (a.email IS NOT NULL AND d.email=a.email) LIMIT 1) AS dnc "
        "FROM agents a ORDER BY touches DESC, listings DESC LIMIT 500").fetchall()
    snap["agents"] = [dict(db.row_to_dict(r), dnc=bool(r["dnc"])) for r in ag]
    # --- threads + messages ----------------------------------------------------------------
    threads = []
    for t in conn.execute("SELECT * FROM threads ORDER BY updated_at DESC LIMIT 200").fetchall():
        td = db.row_to_dict(t)
        pids = _j(td["property_ids"], [])
        td["property_ids"] = pids
        td["properties"] = [db.row_to_dict(x) for x in conn.execute(
            f"SELECT id, street, city FROM properties WHERE id IN ({','.join('?' * len(pids)) or 'NULL'})", pids).fetchall()] if pids else []
        td["agent"] = _agent(conn, td["agent_id"])
        td["messages"] = [db.row_to_dict(m) for m in conn.execute(
            "SELECT id, direction, body, classification, confidence, action, created_at FROM messages WHERE thread_id=? ORDER BY created_at", (t["id"],)).fetchall()]
        threads.append(td)
    snap["threads"] = threads
    # --- outreach --------------------------------------------------------------------------
    out = []
    for o in conn.execute("SELECT * FROM outreach ORDER BY id DESC LIMIT ?", (MAX_OUTREACH,)).fetchall():
        od = db.row_to_dict(o)
        pids = _j(od["property_ids"], [])
        od["property_ids"] = pids
        od["properties"] = [db.row_to_dict(x) for x in conn.execute(
            f"SELECT id, street, city FROM properties WHERE id IN ({','.join('?' * len(pids)) or 'NULL'})", pids).fetchall()] if pids else []
        od["agent"] = _agent(conn, od["agent_id"])
        out.append(od)
    snap["outreach"] = out
    snap["do_not_contact"] = [db.row_to_dict(r) for r in conn.execute("SELECT * FROM do_not_contact ORDER BY id DESC LIMIT 200").fetchall()]
    # --- live event feed -------------------------------------------------------------------
    snap["events"] = build_events(conn, snap)
    if include_connections:
        from . import doctor
        snap["connections"] = [{k: v for k, v in r.items()} for r in doctor.run(cfg)]
    return snap


def build_events(conn, snap: dict[str, Any]) -> list[dict[str, Any]]:
    ev: list[dict[str, Any]] = []
    for r in snap["runs"]:
        c = r.get("counts") or {}
        detail = ""
        if r["step"] == "ingest":
            detail = f"{c.get('seen', 0)} listings seen, {c.get('kept', 0)} fit, {c.get('new', 0)} new"
        elif r["step"] == "score":
            detail = f"{c.get('scored', 0)} scored ({c.get('claude', 0)} by Claude, {c.get('rules', 0)} by rules)"
        elif r["step"] == "value":
            detail = f"{c.get('valued', 0)} priced"
        elif r["step"] == "outreach":
            detail = f"{c.get('email_drafted', 0)} emails drafted, {c.get('email_sent', 0)} sent · {c.get('sms_drafted', 0)} texts drafted, {c.get('sms_sent', 0)} sent"
        elif r["step"] == "enrich":
            detail = f"{c.get('found', c.get('emails_found', 0))} agent emails found" if c else ""
        ev.append({"ts": r["finished_at"] or r["started_at"], "type": "run", "status": r["status"],
                   "title": f"{r['step']} {r['status']}", "detail": detail or (r.get("notes") or "")})
    for o in snap["outreach"]:
        who = (o.get("agent") or {}).get("name") or o.get("to_address") or "?"
        streets = ", ".join(p["street"] for p in o.get("properties", [])) or "?"
        ev.append({"ts": o.get("sent_at") or o["created_at"], "type": "outreach", "status": o["status"], "channel": o["channel"],
                   "title": f"{o['channel']} {o['status']} → {who}", "detail": f"{streets}" + (f" · {o['error']}" if o.get("error") else ""),
                   "outreach_id": o["id"], "agent_id": o.get("agent_id")})
    for t in snap["threads"]:
        who = (t.get("agent") or {}).get("name") or "?"
        for m in t["messages"]:
            if m["direction"] == "in":
                ev.append({"ts": m["created_at"], "type": "reply", "status": m.get("classification") or "reply", "channel": t["channel"],
                           "title": f"reply from {who} · {m.get('classification') or '?'}", "detail": (m.get("body") or "")[:200],
                           "thread_id": t["id"], "agent_id": t.get("agent_id")})
    for p in snap["properties"]:
        if p.get("lead_status") in ("HOT_LEAD", "NEEDS_CALL") and p.get("lead_status_at"):
            ev.append({"ts": p["lead_status_at"], "type": "lead", "status": p["lead_status"],
                       "title": f"{p['lead_status'].replace('_', ' ')} · {p['street']}, {p['city']}", "detail": p.get("lead_note") or "", "property_id": p["id"]})
    for d in snap["do_not_contact"]:
        ev.append({"ts": d["created_at"], "type": "dnc", "status": "STOP", "title": "do-not-contact added", "detail": d.get("reason") or ""})
    ev.sort(key=lambda e: e.get("ts") or "", reverse=True)
    return ev[:MAX_EVENTS]


def write_local(snap: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, default=str))
    return path


def upload(snap: dict[str, Any], token: str, session: requests.Session | None = None, pathname: str = BLOB_PATH) -> dict[str, Any]:
    """PUT the snapshot to Vercel Blob at a fixed pathname (overwrite, 60 s CDN cache). Returns the blob metadata."""
    s = session or requests.Session()
    body = json.dumps(snap, default=str).encode()
    r = s.put(f"{BLOB_API}/{pathname}", data=body, timeout=60, headers={
        "authorization": f"Bearer {token}", "x-api-version": "7", "x-content-type": "application/json",
        "x-add-random-suffix": "0", "x-allow-overwrite": "1", "x-cache-control-max-age": "60"})
    if r.status_code >= 400:
        raise RuntimeError(f"Vercel Blob upload failed: HTTP {r.status_code}: {r.text[:300]}")
    meta = r.json()
    log.info("dashboard: snapshot uploaded (%d KB) → %s", len(body) // 1024, meta.get("pathname", pathname))
    return meta


def sync(conn, cfg, include_connections: bool = False, session: requests.Session | None = None) -> dict[str, Any]:
    """Build, write reports/dashboard.json, upload when BLOB_READ_WRITE_TOKEN is set. Never raises into the caller."""
    result: dict[str, Any] = {"uploaded": False}
    try:
        snap = build_snapshot(conn, cfg, include_connections=include_connections)
        result["local"] = str(write_local(snap, cfg.path("reports") / "dashboard.json"))
        result["events"] = len(snap["events"]); result["properties"] = len(snap["properties"])
        token = cfg.secret("BLOB_READ_WRITE_TOKEN")
        if token:
            meta = upload(snap, token, session=session)
            result.update(uploaded=True, url=meta.get("url"))
        else:
            result["skipped"] = "BLOB_READ_WRITE_TOKEN blank (dashboard not connected)"
    except Exception as e:  # noqa: BLE001 — the dashboard must never break a run
        log.warning("dashboard sync failed: %s", e)
        result["error"] = str(e)[:300]
    return result
