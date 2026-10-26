"""SMS CONSENT: pull text-message opt-ins from the public form into the database and onto the agents.

The form (dashboard/public/sms-consent.html, hosted on the Vercel project) writes one JSON record per submission to the
project's Blob store under titan/consents/. This module lists that prefix with the same BLOB_READ_WRITE_TOKEN the
dashboard sync uses, stores every new record in sms_consents, matches it to an agent by phone (then email) and stamps
agents.sms_consent_at. The outreach planner texts only agents with that stamp (outreach.sms_require_consent). An
opt-out record (action=opt_out) clears the stamp and adds the phone to do_not_contact.
Runs inside every check-replies and via `titan.cli consents`.
"""
from __future__ import annotations

import json
from typing import Any

import requests

from . import db
from .util import iso, log, normalize_phone

BLOB_API = "https://blob.vercel-storage.com"
PREFIX = "titan/consents/"


def list_blobs(token: str, session: requests.Session | None = None, prefix: str = PREFIX) -> list[dict[str, Any]]:
    """Every blob under the prefix (pathname, url, uploadedAt). Follows the cursor."""
    s = session or requests.Session()
    out: list[dict[str, Any]] = []
    cursor = None
    for _ in range(50):
        params = {"prefix": prefix, "limit": "1000"}
        if cursor:
            params["cursor"] = cursor
        r = s.get(f"{BLOB_API}/", params=params, timeout=30, headers={"authorization": f"Bearer {token}", "x-api-version": "7"})
        if r.status_code >= 400:
            raise RuntimeError(f"Vercel Blob list failed: HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        out.extend(data.get("blobs") or [])
        if not data.get("hasMore") or not data.get("cursor"):
            break
        cursor = data["cursor"]
    return out


def match_agent(conn, phone: str | None, email: str | None) -> int | None:
    if phone:
        r = conn.execute("SELECT id FROM agents WHERE phone=? ORDER BY updated_at DESC LIMIT 1", (phone,)).fetchone()
        if r:
            return int(r["id"])
    if email:
        r = conn.execute("SELECT id FROM agents WHERE lower(email)=? ORDER BY updated_at DESC LIMIT 1", (email.lower(),)).fetchone()
        if r:
            return int(r["id"])
    return None


def record(conn, blob_path: str, rec: dict[str, Any]) -> dict[str, Any]:
    """Store one consent record and apply it to the matching agent. Idempotent on blob_path."""
    if conn.execute("SELECT 1 FROM sms_consents WHERE blob_path=?", (blob_path,)).fetchone():
        return {"stored": False, "reason": "already"}
    action = (rec.get("action") or "opt_in").strip().lower()
    phone = normalize_phone(rec.get("phone"))
    email = (rec.get("email") or "").strip().lower() or None
    agent_id = None
    if str(rec.get("agent_id") or "").isdigit():
        r = conn.execute("SELECT id FROM agents WHERE id=?", (int(rec["agent_id"]),)).fetchone()
        agent_id = int(r["id"]) if r else None
    if agent_id is None:
        agent_id = match_agent(conn, phone, email)
    now = iso()
    conn.execute("INSERT INTO sms_consents(blob_path,agent_id,action,name,phone,email,property,consent_text,ip,user_agent,created_at,pulled_at) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                 (blob_path, agent_id, action, (rec.get("name") or "")[:120] or None, phone, email, (rec.get("property") or "")[:200] or None,
                  (rec.get("consent_text") or "")[:1200] or None, (rec.get("ip") or "")[:60] or None, (rec.get("user_agent") or "")[:300] or None,
                  rec.get("created_at") or now, now))
    if action == "opt_out":
        db.add_dnc(conn, agent_id, phone, email, "sms opt-out form")
        if agent_id is not None:
            conn.execute("UPDATE agents SET sms_consent_at=NULL, sms_consent_source='opt_out', updated_at=? WHERE id=?", (now, agent_id))
        return {"stored": True, "action": action, "agent_id": agent_id}
    if agent_id is not None:
        conn.execute("UPDATE agents SET sms_consent_at=?, sms_consent_source='form', updated_at=? WHERE id=?",
                     (rec.get("created_at") or now, now, agent_id))
        if phone and not conn.execute("SELECT phone FROM agents WHERE id=?", (agent_id,)).fetchone()["phone"]:
            conn.execute("UPDATE agents SET phone=?, phone_raw=? WHERE id=?", (phone, rec.get("phone"), agent_id))   # the number they want texted
    return {"stored": True, "action": action, "agent_id": agent_id}


def pull_consents(conn, cfg: Any, session: requests.Session | None = None) -> dict[str, Any]:
    """List the consents prefix, fetch every record not yet stored, apply it. Never raises into the caller."""
    res: dict[str, Any] = {"new": 0, "matched": 0, "unmatched": 0, "opt_outs": 0, "errors": 0}
    token = cfg.secret("BLOB_READ_WRITE_TOKEN")
    if not token:
        res["skipped"] = "BLOB_READ_WRITE_TOKEN blank (dashboard not connected)"
        return res
    s = session or requests.Session()
    try:
        blobs = list_blobs(token, s)
    except Exception as e:  # noqa: BLE001
        log.warning("consents: %s", e)
        res["errors"] += 1
        res["error"] = str(e)[:200]
        return res
    known = {r["blob_path"] for r in conn.execute("SELECT blob_path FROM sms_consents").fetchall()}
    for b in blobs:
        path = b.get("pathname") or ""
        if not path or path in known or not path.endswith(".json"):
            continue
        try:
            r = s.get(b["url"], timeout=30)
            r.raise_for_status()
            rec = r.json()
            with db.tx(conn):
                out = record(conn, path, rec)
        except Exception as e:  # noqa: BLE001
            log.warning("consents: %s: %s", path, e)
            res["errors"] += 1
            continue
        if not out.get("stored"):
            continue
        res["new"] += 1
        if out["action"] == "opt_out":
            res["opt_outs"] += 1
        elif out.get("agent_id") is not None:
            res["matched"] += 1
        else:
            res["unmatched"] += 1
    res["total_opted_in"] = conn.execute("SELECT COUNT(*) FROM agents WHERE sms_consent_at IS NOT NULL").fetchone()[0]
    if res["new"]:
        log.info("consents: %d new (%d matched to agents, %d unmatched, %d opt-outs)", res["new"], res["matched"], res["unmatched"], res["opt_outs"])
    return res


def summary(conn) -> list[dict[str, Any]]:
    """Every opt-in, newest first, with the agent it landed on (for `titan.cli consents`)."""
    rows = conn.execute("SELECT c.created_at, c.action, c.name, c.phone, c.email, c.property, c.agent_id, a.name AS agent_name, a.brokerage "
                        "FROM sms_consents c LEFT JOIN agents a ON a.id=c.agent_id ORDER BY c.created_at DESC LIMIT 200").fetchall()
    return [db.row_to_dict(r) for r in rows]
