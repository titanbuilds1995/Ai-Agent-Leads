"""INGEST step: pull from every configured source, filter, dedupe, store. A failing source never blocks the rest."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable

from .. import db
from ..util import et_day_bounds, log, today_et
from . import realtor, redfin_csv, zillow
from .apify import ApifyClient
from .normalize import Listing, passes_filters


def store_listings(conn, listings: Iterable[Listing], cfg: Any) -> dict[str, int]:
    counts = {"seen": 0, "kept": 0, "new": 0, "updated": 0, "filtered": 0}
    reasons: dict[str, int] = {}
    with db.tx(conn):
        for l in listings:
            counts["seen"] += 1
            ok, why = passes_filters(l, cfg)
            if not ok:
                counts["filtered"] += 1
                reasons[why.split(" ")[0]] = reasons.get(why.split(" ")[0], 0) + 1
                continue
            agent_id = None
            if l.agent_name or l.agent_phone:
                agent_id = db.upsert_agent(conn, l.agent_name, l.brokerage, l.agent_phone_raw, l.agent_phone, l.agent_email,
                                           {"profile": l.agent_profile_url} if l.agent_profile_url else None)
            fields = l.to_db()
            fields["agent_id"] = agent_id
            fields["raw"] = json.dumps(l.raw, default=str)[:200_000] if l.raw else None
            _, created = db.upsert_property(conn, l.address_key, fields)
            counts["kept"] += 1
            counts["new" if created else "updated"] += 1
        db.flag_office_lines(conn, cfg.get("enrichment.office_line_min_agents", 3))
    counts["filter_reasons"] = reasons  # type: ignore[assignment]
    return counts


def run_sources(cfg: Any, client: ApifyClient | None, sources: list[str] | None = None) -> tuple[list[Listing], dict[str, Any]]:
    """Returns (listings, per-source status). Each source is isolated."""
    sources = sources or ["realtor", "zillow", "redfin"]
    out: list[Listing] = []
    status: dict[str, Any] = {}

    def run(name: str, fn: Callable[[], list[Listing] | tuple[list[Listing], dict[str, Any]]]):
        try:
            got = fn()
            extra: dict[str, Any] = {}
            if isinstance(got, tuple):
                got, extra = got
            out.extend(got)
            status[name] = {"ok": True, "count": len(got), **extra}
        except Exception as e:  # noqa: BLE001 — isolate every source
            log.exception("source %s failed: %s", name, e)
            status[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}

    max_items = cfg.get("ingest.apify.max_items_per_actor", 3000)
    if "realtor" in sources:
        if client:
            run("realtor", lambda: map_items("realtor", client.run_actor(cfg.get("ingest.apify.realtor_actor"), realtor.actor_input(cfg), max_items, "realtor"), realtor.map_item))
        else:
            status["realtor"] = {"ok": False, "error": "APIFY_TOKEN missing"}
    if "zillow" in sources:
        if client:
            run("zillow", lambda: map_items("zillow", client.run_actor(cfg.get("ingest.apify.zillow_actor"), zillow.actor_input(cfg), max_items, "zillow"), zillow.map_item))
        else:
            status["zillow"] = {"ok": False, "error": "APIFY_TOKEN missing"}
    if "redfin" in sources:
        run("redfin", lambda: redfin_csv.parse_dir(cfg.root / cfg.get("ingest.redfin_csv_dir", "raw/redfin")))
    return out, status


def map_items(name: str, items: list[dict[str, Any]], mapper: Callable[[dict[str, Any]], Listing | None],
              min_ratio: float = 0.5) -> tuple[list[Listing], dict[str, Any]]:
    """Map raw actor items; when the mapper rejects most of them, log the real field names so the fix is obvious (BLOCKERS #2)."""
    mapped: list[Listing] = []
    first_bad: dict[str, Any] | None = None
    for it in items:
        if not isinstance(it, dict):
            continue
        l = mapper(it)
        if l:
            mapped.append(l)
        elif first_bad is None:
            first_bad = it
    extra: dict[str, Any] = {"raw_count": len(items), "unmapped": len(items) - len(mapped)}
    if items and len(mapped) < min_ratio * len(items) and first_bad is not None:
        extra["unmapped_sample_keys"] = sample_keys(first_bad)
        log.warning("%s: mapped only %d of %d raw items; the mapper does not recognise this shape. "
                    "Top-level keys of the first unmapped item: %s — compare with titan/ingest/%s.py and the newest file in raw/.",
                    name, len(mapped), len(items), extra["unmapped_sample_keys"], name)
    return mapped, extra


def sample_keys(item: dict[str, Any], depth: int = 1, limit: int = 60) -> list[str]:
    """Top-level keys plus one level of nested dict keys ('address.city'), capped. Values are never logged."""
    keys: list[str] = []
    for k, v in item.items():
        keys.append(str(k))
        if depth > 0 and isinstance(v, dict):
            keys.extend(f"{k}.{sub}" for sub in list(v.keys())[:12])
    return keys[:limit]


def ingest(conn, cfg: Any, client: ApifyClient | None = None, fixture: Path | None = None,
           sources: list[str] | None = None) -> dict[str, Any]:
    run_date = today_et()
    run_id = db.start_run(conn, "ingest", run_date)
    try:
        if fixture:
            listings, status = load_fixture(fixture), {"fixture": {"ok": True, "path": str(fixture)}}
        else:
            listings, status = run_sources(cfg, client, sources)
        counts = store_listings(conn, listings, cfg)
        counts["sources"] = status
        health = scrape_health(conn, run_date, cfg.get("ingest.scrape_health_min_ratio", 0.5))
        counts["health"] = health
        ok = any(s.get("ok") for s in status.values())
        db.finish_run(conn, run_id, "ok" if ok else "failed", counts, None if ok else "every source failed")
        log.info("ingest: %s", json.dumps(counts, default=str))
        return counts
    except Exception as e:
        db.finish_run(conn, run_id, "failed", None, str(e)[:500])
        raise


def load_fixture(path: Path) -> list[Listing]:
    data = json.loads(Path(path).read_text())
    out = []
    for it in data:
        src = it.get("_source", "realtor")
        l = realtor.map_item(it) if src == "realtor" else zillow.map_item(it)
        if l:
            out.append(l)
    return out


def scrape_health(conn, run_date: str, min_ratio: float) -> dict[str, Any]:
    rows = conn.execute("SELECT run_date, counts FROM daily_runs WHERE step='ingest' AND status='ok' AND run_date<? "
                        "ORDER BY run_date DESC LIMIT 7", (run_date,)).fetchall()
    hist = [json.loads(r["counts"]).get("seen", 0) for r in rows if r["counts"]]
    lo, hi = et_day_bounds(run_date)
    today = conn.execute("SELECT COUNT(*) FROM properties WHERE last_seen>=? AND last_seen<?", (lo, hi)).fetchone()[0]
    if not hist:
        return {"today": today, "median_7d": None, "warning": None}
    med = sorted(hist)[len(hist) // 2]
    warn = f"scrape returned {today} vs 7-day median {med}; check actors" if med and today < min_ratio * med else None
    return {"today": today, "median_7d": med, "warning": warn}
