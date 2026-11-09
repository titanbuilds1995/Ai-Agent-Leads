"""The commercial screen, end to end and idempotent: pull → details → documents → score → store → diff → files → Asana → email.
Re-running on the same day is harmless (upserts by listing id, one snapshot per listing per day)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from .. import db
from ..util import iso, log, now_utc, today_et
from . import asana_push, documents, loopnet, outputs, scoring
from .board import apply_board, read_board
from .email_html import build_email
from .fl_geo import region_for
from .site import publish as publish_site

SNAPSHOT_COLS = ("price", "days_on_market", "stated_cap", "score", "tag", "status")
STORE_COLS = [
    "listing_id", "listing_url", "name", "street", "city", "state", "zip", "county", "region", "asset_class", "property_type", "property_subtype",
    "price", "price_text", "building_sf", "lot_sf", "year_built", "units", "stated_noi", "stated_cap", "back_solved_cap", "offer_anchor", "occupancy",
    "tenants", "tenancy", "lease_type", "remaining_term_years", "days_on_market", "listed_at", "price_cuts", "previous_price", "broker_name", "brokerage",
    "broker_phone", "broker_email", "brokerage_size", "score", "score_parts", "tag", "tag_reasons", "evidence_grade", "evidence_note", "in_box", "box_note",
    "tier", "motivation", "credit_tenants", "documents", "docs_missing", "detail_level", "status", "description", "highlights",
    "airport_code", "airport_minutes", "quality",
]
JSON_COLS = {"score_parts", "tag_reasons", "motivation", "credit_tenants", "documents", "docs_missing", "highlights"}


def _stored(conn, listing_id: str) -> dict[str, Any] | None:
    r = db.row_to_dict(conn.execute("SELECT * FROM commercial_listings WHERE listing_id=?", (listing_id,)).fetchone())
    if not r:
        return None
    try:
        r["data"] = json.loads(r.get("data") or "{}")
    except json.JSONDecodeError:
        r["data"] = {}
    return r


def merge_with_stored(L: dict[str, Any], stored: dict[str, Any] | None) -> dict[str, Any]:
    """A summary card today + a full sheet from an earlier run → keep the sheet's facts, take today's price/cap/status."""
    if not stored or L.get("detail_level") == "full" or stored.get("detail_level") != "full":
        return L
    base = dict(stored.get("data") or {})
    if not base:
        return L
    for k in ("price", "price_text", "status", "listing_url", "broker_phone", "broker_email", "broker_name", "brokerage"):
        if L.get(k) not in (None, ""):
            base[k] = L[k]
    if L.get("stated_cap") is not None:
        base["stated_cap"] = L["stated_cap"]
    base["detail_level"] = "full"
    base["raw"] = L.get("raw")
    return base


def price_cuts_from_snapshots(conn, listing_id: str, today_price: int | None) -> tuple[int, int | None]:
    rows = [r[0] for r in conn.execute("SELECT price FROM commercial_snapshots WHERE listing_id=? AND price IS NOT NULL ORDER BY run_date", (listing_id,)).fetchall()]
    if today_price:
        rows.append(today_price)
    cuts, prev = 0, None
    for a, b in zip(rows, rows[1:]):
        if b < a:
            cuts += 1
            prev = a
    return cuts, prev


def store(conn, L: dict[str, Any], run_date: str) -> None:
    now = iso()
    vals = {c: L.get(c) for c in STORE_COLS}
    vals["score_parts"] = L.get("parts")
    for c in JSON_COLS:
        vals[c] = json.dumps(vals.get(c) or ([] if c != "score_parts" else {}), default=str)
    vals["in_box"] = None if L.get("in_box") is None else int(bool(L["in_box"]))
    vals["data"] = loopnet.to_json(L)
    vals["raw"] = json.dumps(L.get("raw") or {}, default=str)[:200_000]
    cols = list(vals) + ["first_seen", "last_seen", "gone_at"]
    sets = ", ".join(f"{c}=excluded.{c}" for c in vals if c != "listing_id") + ", last_seen=excluded.last_seen, gone_at=NULL"
    conn.execute(f"INSERT INTO commercial_listings ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) ON CONFLICT(listing_id) DO UPDATE SET {sets}",
                 [*vals.values(), now, now, None])
    conn.execute("INSERT INTO commercial_snapshots (run_date, listing_id, price, days_on_market, stated_cap, score, tag, status, created_at) VALUES (?,?,?,?,?,?,?,?,?) "
                 "ON CONFLICT(run_date, listing_id) DO UPDATE SET price=excluded.price, days_on_market=excluded.days_on_market, stated_cap=excluded.stated_cap, "
                 "score=excluded.score, tag=excluded.tag, status=excluded.status",
                 (run_date, L["listing_id"], L.get("price"), L.get("days_on_market"), L.get("stated_cap"), L.get("score"), L.get("tag"), L.get("status"), now))


def mark_gone(conn, seen_ids: set[str], run_date: str) -> list[dict[str, Any]]:
    """In-box listings that were active and did not come back in a FULL pull → gone (pending / sold / pulled). Only on full pulls."""
    gone = []
    for r in conn.execute("SELECT * FROM commercial_listings WHERE gone_at IS NULL AND in_box=1").fetchall():
        r = db.row_to_dict(r)
        if r["listing_id"] not in seen_ids:
            conn.execute("UPDATE commercial_listings SET gone_at=?, status='gone' WHERE listing_id=?", (iso(), r["listing_id"]))
            conn.execute("INSERT OR IGNORE INTO commercial_snapshots (run_date, listing_id, price, days_on_market, stated_cap, score, tag, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (run_date, r["listing_id"], r["price"], r["days_on_market"], r["stated_cap"], r["score"], r["tag"], "gone", iso()))
            gone.append(r)
    return gone


def diff(conn, run_date: str, listings: list[dict[str, Any]], gone: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Today vs each listing's previous snapshot: new, price drops / ups, DOM jumps, tag changes, gone, back."""
    out: dict[str, list[dict[str, Any]]] = {"new": [], "price_drops": [], "price_ups": [], "gone": [], "back": [], "dom_changes": [], "tag_changes": []}

    def row(L: dict[str, Any], was: Any = None, now: Any = None) -> dict[str, Any]:
        return {"change": "", "street": L.get("street"), "city": L.get("city"), "asset_class": L.get("asset_class"), "price": L.get("price"), "was": was, "now": now,
                "score": L.get("score"), "tag": L.get("tag"), "listing_url": L.get("listing_url"), "listing_id": L.get("listing_id")}

    for L in listings:
        if not L.get("in_box"):
            continue
        prev = conn.execute("SELECT * FROM commercial_snapshots WHERE listing_id=? AND run_date<? ORDER BY run_date DESC LIMIT 1", (L["listing_id"], run_date)).fetchone()
        if not prev:
            out["new"].append(row(L))
            continue
        prev = dict(prev)
        if prev.get("status") == "gone":
            out["back"].append(row(L, "gone", "active"))
        if prev.get("price") and L.get("price") and L["price"] < prev["price"]:
            out["price_drops"].append(row(L, prev["price"], L["price"]))
        elif prev.get("price") and L.get("price") and L["price"] > prev["price"]:
            out["price_ups"].append(row(L, prev["price"], L["price"]))
        if prev.get("days_on_market") is not None and L.get("days_on_market") is not None and L["days_on_market"] - prev["days_on_market"] >= 30:
            out["dom_changes"].append(row(L, prev["days_on_market"], L["days_on_market"]))
        if prev.get("tag") and L.get("tag") and prev["tag"] != L["tag"]:
            out["tag_changes"].append(row(L, prev["tag"], L["tag"]))
    for g in gone:
        out["gone"].append(row(g, "active", "gone"))
    for k, rows in out.items():
        for r in rows:
            r["change"] = k
    return out


def counts_of(listings: list[dict[str, Any]]) -> dict[str, int]:
    box = [L for L in listings if L.get("in_box")]
    return {"total": len(listings), "in_box": len(box), "clean": sum(L.get("tag") == "CLEAN" for L in box), "suspect": sum(L.get("tag") == "SUSPECT" for L in box),
            "needs_om": sum(L.get("tag") == "NEEDS-OM" for L in box), "undisclosed": sum(L.get("in_box") is None for L in listings),
            "out_of_box": sum(L.get("in_box") is False for L in listings), "sheets_pending": sum(L.get("detail_level") != "full" for L in box),
            "brokers_missing_email": sum(not L.get("broker_email") for L in box)}


def run(conn, cfg: Any, apify=None, asana=None, gmail=None, *, run_date: str | None = None, families: list[str] | None = None,
        max_items: int | None = None, details: bool | None = None, raw_rows: list[dict[str, Any]] | None = None, push_asana: bool = True,
        email: bool = True, full_pull: bool | None = None, today: date | None = None, site: bool = True) -> dict[str, Any]:
    run_date = run_date or today_et()
    rid = db.start_run(conn, "commercial", run_date)
    started = now_utc()
    cost: dict[str, float] = {"search_usd": 0.0, "details_usd": 0.0}
    # 1. search pass (or rows handed in: fixture / raw file)
    rows_handed_in = raw_rows is not None
    if raw_rows is None:
        if apify is None:
            db.finish_run(conn, rid, "skipped", {"reason": "no Apify client"})
            return {"error": "no Apify client (APIFY_TOKEN missing)"}
        by_family = loopnet.pull_search(cfg, apify, families, max_items)
        raw_rows = [r for rows in by_family.values() for r in rows]
        cost["search_usd"] = loopnet.estimate_cost(len(raw_rows), 0, cfg)["search_usd"]
    listings = loopnet.dedupe(raw_rows)
    if today:
        for L in listings:
            L["days_on_market"] = loopnet._days_since(L.get("listed_at"), today)
    # a FULL pull (every family, no row cap, live) is the only run that may mark missing listings gone; saved rows and samples never do
    full_pull = (apify is not None and families is None and max_items is None and not rows_handed_in) if full_pull is None else full_pull
    # 2. merge with what earlier runs already fetched, then a first scoring pass to know who is in the box
    from .airports import airport_screen
    from .quality import quality_screen
    for i, L in enumerate(listings):
        listings[i] = L = merge_with_stored(L, _stored(conn, L["listing_id"]))
        L["region"] = region_for(L.get("county"))
        L.update(airport_screen(L, cfg))
        L.update(quality_screen(L, cfg))
        L.update(scoring.score(L, cfg))
    # 3. detail sheets for in-box listings still on a summary card (budgeted)
    want_details = bool(cfg.get("commercial.loopnet.details", True)) if details is None else details
    pending = [L for L in listings if L.get("in_box") and L.get("detail_level") != "full"]
    fetched = 0
    if want_details and apify is not None and pending:
        budget = float(cfg.get("commercial.loopnet.detail_budget_usd", 4.0))
        n = min(len(pending), int(budget / 0.0115))
        pending.sort(key=lambda L: -(L.get("score") or 0))
        rows = loopnet.pull_details(cfg, apify, [L["listing_url"] for L in pending[:n]])
        cost["details_usd"] = loopnet.estimate_cost(0, n, cfg)["details_usd"]
        by_id = {L["listing_id"]: L for L in loopnet.dedupe(rows) if L["detail_level"] == "full"}
        for i, L in enumerate(listings):
            if L["listing_id"] in by_id:
                full = by_id[L["listing_id"]]
                for k in ("broker_email", "broker_phone", "broker_phone_raw", "brokerage", "broker_name", "brokerage_size", "stated_cap"):
                    if full.get(k) in (None, "") and L.get(k) not in (None, ""):
                        full[k] = L[k]
                if full.get("price") is None and L.get("price"):
                    full["price"] = L["price"]
                listings[i] = full
                fetched += 1
    # 4. documents, price cuts, final score
    doc_dir = cfg.root / cfg.get("commercial.documents.dir", "data/listings")
    docs_on = bool(cfg.get("commercial.documents.download", True))
    for i, L in enumerate(listings):
        L["region"] = region_for(L.get("county"))
        if L.get("in_box") is not False:
            L.update(documents.doc_facts(L, doc_dir, enabled=docs_on, max_mb=float(cfg.get("commercial.documents.max_mb", 25))))
            if L.get("doc_noi") and not L.get("stated_noi"):
                L["stated_noi"] = L["doc_noi"]
            if L.get("doc_occupancy") and not L.get("occupancy"):
                L["occupancy"] = L["doc_occupancy"]
            if L.get("doc_lease_type") and not L.get("lease_type"):
                L["lease_type"] = L["doc_lease_type"]
            if L.get("doc_tenants"):
                L["tenant_names"] = L["doc_tenants"]
            if any(d.get("kind") == "sheet" and d.get("downloaded") for d in L.get("documents") or []):
                L["detail_level"] = "full"           # the property sheet was read from Rodrigo's browser / a saved copy
                if L.get("stated_cap") is None:
                    from . import extract as _x
                    txt = " ".join((d.get("facts") or {}).get("_text", "") for d in L["documents"])
                    L["stated_cap"] = _x.cap_from_text(txt) if txt else None
        cuts, prev = price_cuts_from_snapshots(conn, L["listing_id"], L.get("price"))
        L["price_cuts"] = max(cuts, L.get("price_cuts") or 0)
        L["previous_price"] = L.get("previous_price") or prev
        if L.get("days_on_market") is None:
            st = _stored(conn, L["listing_id"])
            if st and st.get("first_seen"):
                L["days_on_market"] = loopnet._days_since(st["first_seen"][:10], today)
                L["dom_basis"] = "since we first saw it (sheet pending)"
    meds = scoring.ppsf_medians([L for L in listings if L.get("in_box")])
    for L in listings:
        L.update(airport_screen(L, cfg))
        L.update(quality_screen(L, cfg))
        L.update(scoring.score(L, cfg, meds.get((L.get("county"), L.get("asset_class")))))
    # 5. store + gone + diff
    with db.tx(conn):
        for L in listings:
            store(conn, L, run_date)
        gone = mark_gone(conn, {L["listing_id"] for L in listings}, run_date) if full_pull else []
    changes = diff(conn, run_date, listings, gone)
    board_cols: dict[str, str] = {}
    if asana is not None and cfg.get("commercial.asana.project"):
        try:
            board = read_board(asana, cfg.get("commercial.asana.project"))
            apply_board(conn, board)
            board_cols = {lid: info["column"] for lid, info in board.items()}
        except Exception as e:  # noqa: BLE001
            log.warning("1031 board read failed: %s", e)
    for L in listings:
        st = conn.execute("SELECT board_column FROM commercial_listings WHERE listing_id=?", (L["listing_id"],)).fetchone()
        L["board_column"] = board_cols.get(L["listing_id"]) or (st[0] if st and st[0] else None) or "New"
    ranked = outputs.rank(listings)
    counts = counts_of(listings)
    # 6. files
    out_dir = cfg.root / cfg.get("commercial.outputs.dir", "reports/commercial") / run_date
    top = outputs.top_clean(ranked, int(cfg.get("commercial.outputs.top_n", 25)))
    brokers = outputs.broker_contacts(ranked, include_undisclosed=bool(cfg.get("commercial.buyer_box.include_undisclosed_price", False)))
    needs_om = [L for L in ranked if L.get("in_box") and L.get("tag") == "NEEDS-OM"]
    diff_rows = [r for rows in changes.values() for r in rows]
    cost["total_usd"] = round(cost["search_usd"] + cost["details_usd"], 2)
    files = {
        "master_csv": str(outputs.write_csv(out_dir / "master.csv", ranked, outputs.MASTER_COLS)),
        "broker_contacts_csv": str(outputs.write_csv(out_dir / "broker_contacts.csv", brokers,
                                                     ["broker_name", "brokerage", "brokerage_size", "broker_phone", "broker_email", "email_missing", "listing_count", "best_score", "grades", "tags", "listings", "missing_docs"])),
        "needs_om_csv": str(outputs.write_csv(out_dir / "needs_om.csv", needs_om, ["score", "asset_class", "street", "city", "county", "price", "stated_cap", "broker_name", "broker_phone", "broker_email", "listing_url"])),
    }
    xlsx = outputs.write_workbook(out_dir / "master.xlsx", ranked, top, brokers, needs_om, diff_rows, cfg)
    pdf = outputs.write_pdf(out_dir / "top25_1031.pdf", top, cfg, run_date, counts)
    files["workbook"] = str(xlsx) if xlsx else "openpyxl missing"
    files["pdf"] = str(pdf) if pdf else "reportlab missing"
    md = outputs.diff_markdown(run_date, changes, counts, cost)
    (out_dir / "diff.md").write_text(md)
    files["diff_md"] = str(out_dir / "diff.md")
    # 7. Asana + email
    summary: dict[str, Any] = {"run_date": run_date, "counts": counts, "cost": cost, "files": files, "details_fetched": fetched,
                               "diff": {k: len(v) for k, v in changes.items()}, "full_pull": full_pull}
    if asana is not None and push_asana:
        try:
            summary["asana"] = asana_push.push(conn, cfg, asana, ranked)
        except Exception as e:  # noqa: BLE001 — the files are the deliverable; a board hiccup must not lose them
            log.exception("commercial asana push failed: %s", e)
            summary["asana"] = {"error": str(e)}
    if site:
        summary["site"] = publish_site(conn, cfg, run_date)
    from .outreach import response_clock
    clock = response_clock(conn)
    for L in ranked:
        L["om_request"] = clock.get(L["listing_id"])
    html, text = build_email(ranked, cfg, run_date, counts, intro=f"{len(changes['new'])} new since last time, {len(changes['price_drops'])} price drops. ")
    (out_dir / "deals_email.html").write_text(html)
    files["deals_email"] = str(out_dir / "deals_email.html")
    to = cfg.secret("REPORT_TO")
    if gmail is not None and email and to and cfg.get("commercial.report_to_email", True):
        try:
            if cfg.get("commercial.email.enabled", True):
                gmail.send(to, f"Titan 1031 deals {run_date}: {counts['in_box']} in the box, {len(changes['new'])} new, {len(changes['price_drops'])} price drops",
                           text + "\n\n" + md, html=html)
            else:
                gmail.send(to, f"Titan 1031 screen {run_date}: {counts['in_box']} in the box, {len(changes['new'])} new, {len(changes['price_drops'])} price drops", md)
            summary["emailed"] = to
        except Exception as e:  # noqa: BLE001
            summary["email_error"] = str(e)
    db.finish_run(conn, rid, "ok", summary["counts"])
    conn.execute("INSERT INTO commercial_runs (run_date, kind, started_at, finished_at, counts, cost_usd, notes) VALUES (?,?,?,?,?,?,?)",
                 (run_date, "full" if full_pull else "partial", iso(started), iso(), json.dumps(counts), cost["total_usd"], json.dumps(summary["diff"])))
    summary["ranked"] = ranked
    return summary


def sample_table(ranked: list[dict[str, Any]], n: int = 10, min_counties: int = 3) -> list[dict[str, Any]]:
    """n scored in-box listings spread over at least min_counties counties (the sanity-check sample Rodrigo asked for)."""
    box = [L for L in ranked if L.get("in_box")]
    picked: list[dict[str, Any]] = []
    counties: list[str] = []
    for L in box:                                  # one per county first, best score first
        if L.get("county") and L["county"] not in counties:
            picked.append(L)
            counties.append(L["county"])
        if len(picked) >= n:
            break
    for L in box:
        if len(picked) >= n:
            break
        if L not in picked:
            picked.append(L)
    return picked[:n]


def sample_text(rows: list[dict[str, Any]]) -> str:
    from ..util import money
    out = []
    for L in rows:
        out.append(f"{L.get('score'):>3} | {L.get('tag'):<8} | {L.get('evidence_grade')} | {(L.get('asset_class') or '?'):<11} | {L.get('street')}, {L.get('city')} ({L.get('county')}) | "
                   f"ask {money(L.get('price'))} | cap {L.get('stated_cap') or '—'} | NOI {money(L.get('stated_noi')) if L.get('stated_noi') else '—'} | "
                   f"anchor {money(L.get('offer_anchor')) if L.get('offer_anchor') else '—'} | {L.get('broker_name')} ({L.get('brokerage_size')}) | "
                   f"flags: {'; '.join(L.get('tag_reasons') or [])}")
    return "\n".join(out)
