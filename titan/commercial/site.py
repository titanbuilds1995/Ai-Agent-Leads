"""The investor site's data: titan/deals.json in the same Vercel Blob store as the dashboard snapshot (dashboard/public/1031.html
reads it through dashboard/api/deals.js). Two layers in one file: `deals` (safe for the investor link: facts, photo, why it
fits, column) and `private` (score parts, offer anchor, cooked-books reasons, broker contact) that the API only returns with the
dashboard password. Columns come from the Asana board (titan/commercial/board.py)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import db
from ..dashboard_sync import upload
from ..util import iso, log, pretty_phone
from .board import COLUMNS
from .why import photo_url, why_fits

BLOB_PATH = "titan/deals.json"
PUBLIC_KEYS = ["listing_id", "street", "city", "county", "region", "zip", "asset_class", "property_type", "property_subtype", "price", "price_text",
               "stated_cap", "stated_noi", "building_sf", "lot_sf", "year_built", "units", "occupancy", "tenancy", "lease_type", "remaining_term_years",
               "credit_tenants", "days_on_market", "listed_at", "highlights", "description", "listing_url", "tag", "evidence_grade", "board_column",
               "status", "first_seen", "last_seen"]
PRIVATE_KEYS = ["score", "parts", "score_parts", "offer_anchor", "offer_anchor_basis", "back_solved_cap", "cap_mismatch", "tag_reasons", "evidence_note",
                "tier", "motivation", "brokerage_size", "broker_name", "brokerage", "broker_phone", "broker_email", "docs_missing", "docs_gated",
                "documents", "detail_level", "asana_task_id", "asana_om_task_id", "price_cuts", "previous_price"]


def _loads(v: Any, default: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return default
    return v if v is not None else default


def load_listings(conn) -> list[dict[str, Any]]:
    """Every in-box listing from the store, with its JSON columns unpacked and the raw actor row for the photo."""
    out = []
    for r in conn.execute("SELECT * FROM commercial_listings WHERE in_box=1 ORDER BY score DESC, evidence_grade").fetchall():
        L = db.row_to_dict(r)
        data = _loads(L.pop("data", None), {})
        raw = _loads(L.pop("raw", None), {})
        L = {**data, **{k: v for k, v in L.items() if v is not None}}
        for k in ("score_parts", "tag_reasons", "motivation", "credit_tenants", "documents", "docs_missing", "highlights"):
            L[k] = _loads(L.get(k), [] if k != "score_parts" else {})
        L["parts"] = L.get("parts") or L.get("score_parts") or {}
        L["raw"] = raw
        out.append(L)
    return out


def build_deals(conn, cfg: Any, run_date: str | None = None) -> dict[str, Any]:
    size = str(cfg.get("commercial.email.photo_size", "116"))
    investor_cols = cfg.get("commercial.site.investor_columns") or ["Good fit", "In touch with the agent", "Books received", "Offer out"]
    deals, private = [], {}
    for L in load_listings(conn):
        L["board_column"] = L.get("board_column") or "New"
        pub = {k: L.get(k) for k in PUBLIC_KEYS}
        pub["photo"] = photo_url(L, size)
        pub["photos"] = [str(u).replace("{s}", size) for u in ((L.get("raw") or {}).get("images") or (L.get("raw") or {}).get("photos") or [])][:12]
        pub["why"] = why_fits(L, cfg)
        pub["investor"] = L["board_column"] in investor_cols and L.get("status") != "gone"
        deals.append(pub)
        private[L["listing_id"]] = {**{k: L.get(k) for k in PRIVATE_KEYS}, "broker_phone_pretty": pretty_phone(L.get("broker_phone")) if L.get("broker_phone") else L.get("broker_phone_raw")}
    counts = {c: sum(1 for d in deals if d["board_column"] == c) for c in COLUMNS}
    return {"generated_at": iso(), "run_date": run_date, "columns": COLUMNS, "investor_columns": investor_cols, "buyer_box": cfg.get("commercial.buyer_box", {}),
            "counts": counts, "deals": deals, "private": private}


def publish(conn, cfg: Any, run_date: str | None = None, session=None) -> dict[str, Any]:
    """Write reports/commercial/deals.json and upload it to Blob when the token is set. Never raises into the run."""
    out: dict[str, Any] = {"uploaded": False}
    try:
        snap = build_deals(conn, cfg, run_date)
        path = cfg.path("reports") / "commercial" / "deals.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snap, default=str))
        out.update(local=str(path), deals=len(snap["deals"]), counts=snap["counts"])
        token = cfg.secret("BLOB_READ_WRITE_TOKEN")
        if token:
            meta = upload(snap, token, session=session, pathname=BLOB_PATH)
            out.update(uploaded=True, url=meta.get("url"))
        else:
            out["skipped"] = "BLOB_READ_WRITE_TOKEN blank (site not connected)"
    except Exception as e:  # noqa: BLE001
        log.warning("1031 site publish failed: %s", e)
        out["error"] = str(e)[:300]
    return out
