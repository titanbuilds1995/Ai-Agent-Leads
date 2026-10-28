"""The daily comp-quality loop (Rodrigo, 2026-10-28: "run a loop consistently on a daily basis to improve how we comp
each property"). Runs as the `comps` step of run-daily (after value) and by hand with `make comps-loop`.

Every day:
1. BACK-TEST on real sales. Every sold home the free scraper pulled (comps_cache, one pool per ZIP) is valued from the
   OTHER sales in its pool with the engine's rules, and the prediction is scored against what it actually sold for.
   That is ground truth, available every day, no waiting for our own deals to close.
2. TUNE. Radius, size tolerance, year and bed matching are nudged one at a time; the setting that predicts real sales
   best is kept (data/comps_tuning.json) when it beats the current one by a clear margin on enough sales. The engine
   reads the file on the next valuation, so tomorrow's numbers use today's lesson.
3. RE-VALUE the houses Rodrigo is working (every card on the approvals board + HOT) with the tuned rules; an ARV that
   moved 10%+ is logged and, for cards he is engaged on, posted as one short comment on the lead card.
4. CLAUDE REVIEW of the comp set behind those houses (capped per day, paid API like other card work): it reads the subject
   and each sale and rules out the ones that don't belong (wrong product, waterfront, new build, a different street
   class), flags what the listing hides (split layout, unpermitted work) and gives its own resale read. Its exclusions
   feed the engine's valuation and the numbers snapshot; Rodrigo's own comp comments (lessons) are in its prompt.
5. REPORT. The morning report's "Comps" section shows the accuracy, the grades, what changed and what Claude found."""
from __future__ import annotations

import json
import random
import re
import statistics
import time
from datetime import datetime
from typing import Any, Callable

from . import comps_engine as ce
from . import db
from .util import iso, log, today_et

COMP_LESSON_RE = re.compile(r"\b(comps?|comparables?|arv|resale|after[- ]repair|per (square|sq) ?f(oo|ee)?t|\$/s?f|town ?house|water ?front|"
                            r"wrong (product|comps|sales|houses)|(too|way) (high|low)|over ?priced|price per|duplex|new build|new construction|"
                            r"detached|split|unpermitted|permit)\b", re.I)
GRID = {"radius_miles": [0.5, 0.75, 1.0], "sqft_tol_pct": [15, 20, 25, 30], "year_tol": [15, 30, 50], "beds_tol": [0, 1, 2]}
WORKING_STATUSES = ("NEEDS_REVIEW", "OFFER_DRAFTED", "NEEDS_CALL", "FLEXIBLE", "HOT_LEAD", "DISCUSSING", "RESPONDED", "CONTACTED", "QUEUED", "NONE")
DEAD_STATUSES = ("EXCLUDED", "DO_NOT_CONTACT", "DEAD", "CLOSED", "LOST")

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "drop": {"type": "array", "items": {"type": "object", "properties": {"key": {"type": "string"}, "reason": {"type": "string"}},
                                            "required": ["key", "reason"]}},
        "subject_flags": {"type": "array", "items": {"type": "string"}},
        "arv_low": {"type": "integer"}, "arv_high": {"type": "integer"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "note": {"type": "string"},
    },
    "required": ["drop", "subject_flags", "arv_low", "arv_high", "confidence", "note"],
}

REVIEW_SYSTEM = """You are the comps reviewer for a South Florida wholesaler (Titan Realty Investments). You get ONE subject listing
and the sold homes the engine picked (and the ones it dropped, with its reason). Your job: make the comp set honest.

Rules you apply (Rodrigo's, from real mistakes):
- Same product only. A townhouse / villa / attached home in an HOA is never comped against detached houses, whatever the
  feed calls it. A duplex is not a single-family house. A condo is not a townhouse.
- Waterfront, ocean-access or canal sales never comp a dry house. New construction never comps an old house.
- A sale whose $/sf is far from the group is a different animal (a builder's new build, a teardown, a non-arm's-length
  transfer): rule it out.
- Read the subject's remarks for what the numbers hide: a main house plus a detached cottage / in-law / efficiency is not
  one house of the combined size; unpermitted work or "hold harmless" wording; a bed count impossible for the size
  (a conversion or addition). Name these in subject_flags.
- Give your own renovated-resale range (arv_low..arv_high) for THIS layout from the sales you keep. Be plain; no selling.
- Only drop a sale for a reason you can see in the data given. Keys must be copied exactly from the list.
- note: one or two sentences Rodrigo can read on his phone (what you dropped and why, what the house really is)."""


# ---------- pools ----------

def sold_pools(conn, min_size: int = 12) -> dict[str, list[dict[str, Any]]]:
    """The newest sold pull per ZIP from comps_cache (hh-sold:<zip>:<date>[:12m]), prepared for fast selection."""
    rows = conn.execute("SELECT key, fetched_at, comps FROM comps_cache WHERE key LIKE 'hh-sold:%' OR key LIKE 'sold:%' ORDER BY fetched_at").fetchall()
    newest: dict[str, tuple[str, str]] = {}
    for r in rows:
        parts = r["key"].split(":")
        loc = parts[1] if len(parts) > 1 else ""
        if not (len(loc) == 5 and loc.isdigit()):
            continue
        newest[loc] = (r["fetched_at"], r["comps"])
    pools = {}
    for loc, (_, blob) in newest.items():
        try:
            comps = ce.dedupe(json.loads(blob))
        except ValueError:
            continue
        if len(comps) >= min_size:
            pools[loc] = prepare(comps)
    return pools


def prepare(comps: list[dict[str, Any]], year: int | None = None) -> list[dict[str, Any]]:
    """Cache the per-sale reads (product, flags, condition, $/sf) so the back-test doesn't re-run the regexes."""
    year = year or datetime.now().year
    for c in comps:
        c["_product"] = ce.product(c)
        c["flags"] = ce.flags(c, year)
        c["condition"] = ce.condition_of(c.get("remarks"))
        c["ppsf"] = ce.ppsf(c)
    return comps


# ---------- back-test ----------

def test_cases(pools: dict[str, list[dict[str, Any]]], per_zip: int = 25, max_zips: int = 40, seed: str | None = None) -> list[tuple[str, dict[str, Any]]]:
    """(zip, sale) pairs to predict: sized, priced, dated, not land, not a new build; the same sample all day (seeded)."""
    rng = random.Random(seed or today_et())
    out = []
    zips = sorted(pools)
    rng.shuffle(zips)
    for z in zips[:max_zips]:
        ok = [c for c in pools[z] if c.get("sqft") and c.get("sold_price") and ce.date_key(c) and c.get("_product") not in ("land", None)
              and not (c.get("flags") or {}).get("new_build")]
        rng.shuffle(ok)
        out += [(z, c) for c in ok[:per_zip]]
    return out


def predict(sale: dict[str, Any], pool: list[dict[str, Any]], prm: dict[str, Any]) -> int | None:
    key = ce.comp_key(sale)
    subject = {k: sale.get(k) for k in ("sqft", "beds", "baths", "year_built", "lot_sqft", "property_type", "lat", "lng", "zip", "remarks", "hoa", "units")}
    others = [c for c in pool if ce.comp_key(c) != key and ce.street_key(c.get("address")) != ce.street_key(sale.get("address"))]
    sel = ce.select(subject, others, prm, want=int(prm.get("want", 10)))
    if len(sel["comps"]) < int(prm.get("min_comps", 3)):
        return None
    return ce.arv(subject, sel["comps"], prm, mode="median", apply_layout=False)["arv"]


def backtest(pools: dict[str, list[dict[str, Any]]], prm: dict[str, Any], cases: list[tuple[str, dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Median / mean absolute % error and the share within 10%, overall and per product."""
    cases = cases if cases is not None else test_cases(pools)
    errs: list[float] = []
    by: dict[str, list[float]] = {}
    for z, sale in cases:
        pred = predict(sale, pools[z], prm)
        if not pred:
            continue
        e = abs(pred - sale["sold_price"]) / sale["sold_price"]
        errs.append(e)
        by.setdefault(sale.get("_product") or "?", []).append(e)
    if not errs:
        return {"n": 0, "mdape": None, "mape": None, "within_10_pct": None, "coverage": 0.0, "by_product": {}}
    return {"n": len(errs), "mdape": round(statistics.median(errs) * 100, 2), "mape": round(statistics.mean(errs) * 100, 2),
            "within_10_pct": round(sum(1 for e in errs if e <= 0.10) / len(errs) * 100, 1),
            "coverage": round(len(errs) / max(len(cases), 1), 3),
            "by_product": {k: {"n": len(v), "mdape": round(statistics.median(v) * 100, 2)} for k, v in by.items()}}


def tune(pools: dict[str, list[dict[str, Any]]], base: dict[str, Any], cases: list[tuple[str, dict[str, Any]]],
         max_seconds: float = 240.0, passes: int = 2) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Coordinate descent over GRID starting from `base`: → (best params, its score, trials)."""
    best = json.loads(json.dumps(base))
    best_score = backtest(pools, best, cases)
    trials = [{"params": {k: best[k] for k in GRID}, **best_score}]
    t0 = time.time()
    for _ in range(passes):
        improved = False
        for k, values in GRID.items():
            for v in values:
                if v == best.get(k):
                    continue
                if time.time() - t0 > max_seconds:
                    return best, best_score, trials
                cand = {**best, k: v}
                sc = backtest(pools, cand, cases)
                trials.append({"params": {kk: cand[kk] for kk in GRID}, **sc})
                if sc["n"] and best_score["n"] and sc["mdape"] is not None and _better(sc, best_score):
                    best, best_score, improved = cand, sc, True
        if not improved:
            break
    return best, best_score, trials


def _better(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Lower median error wins, but not by starving coverage (a rule that only values the easy houses is no better)."""
    if a["coverage"] < b["coverage"] * 0.9:
        return False
    return a["mdape"] < b["mdape"] - 0.05


def adopt(cfg: Any, conn, best: dict[str, Any], score: dict[str, Any], baseline: dict[str, Any], run_date: str) -> bool:
    """Keep the tuned settings only on enough sales and a clear gain (min_tests / min_gain_pts in config `comps.loop`)."""
    lp = cfg.get("comps.loop", {}) or {}
    enough = (score.get("n") or 0) >= int(lp.get("min_tests", 150))
    gain = (baseline.get("mdape") or 0) - (score.get("mdape") or 0)
    changed = any(best.get(k) != baseline.get("params", {}).get(k) for k in GRID) if baseline.get("params") else True
    ok = enough and changed and gain >= float(lp.get("min_gain_pts", 0.3))
    note = (f"kept: {gain:+.2f} pts on {score.get('n')} sales" if ok else
            f"not kept: {'too few sales' if not enough else 'no clear gain'} ({gain:+.2f} pts on {score.get('n')} sales)")
    conn.execute("INSERT INTO comps_calibration(run_date,scope,params,n_tests,mdape,mape,within_10_pct,baseline_mdape,adopted,note,created_at) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 (run_date, "global", json.dumps({k: best[k] for k in GRID}), score.get("n"), score.get("mdape"), score.get("mape"),
                  score.get("within_10_pct"), baseline.get("mdape"), int(ok), note + "; by product " + json.dumps(score.get("by_product", {})), iso()))
    if ok:
        path = ce.tuning_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            cur = json.loads(path.read_text()) if path.exists() else {}
        except ValueError:
            cur = {}
        cur["global"] = {k: best[k] for k in GRID}
        cur["updated"] = run_date
        cur["mdape"] = score.get("mdape")
        cur["n"] = score.get("n")
        cur.setdefault("history", []).append({"date": run_date, "mdape": score.get("mdape"), "n": score.get("n"), "params": cur["global"]})
        cur["history"] = cur["history"][-60:]
        path.write_text(json.dumps(cur, indent=1))
    return ok


# ---------- the houses Rodrigo is working ----------

def working_set(conn, limit: int = 400) -> list[dict[str, Any]]:
    """Every house with a card on the approvals board, plus HOT ones; the ones he is deepest in first."""
    cards: dict[int, tuple[int, str]] = {}
    for r in conn.execute("SELECT id, property_ids, asana_task_id FROM outreach WHERE asana_task_id IS NOT NULL ORDER BY id"):
        for pid in json.loads(r["property_ids"] or "[]"):
            cards[int(pid)] = (r["id"], r["asana_task_id"])          # the newest card for the house wins
    rows = conn.execute("SELECT p.*, v.arv, v.arv_method, v.comp_grade, v.rating, v.comp_count FROM properties p LEFT JOIN valuations v ON v.property_id=p.id "
                        "WHERE p.lead_status NOT IN (" + ",".join("?" * len(DEAD_STATUSES)) + ")", DEAD_STATUSES).fetchall()
    order = {"NEEDS_REVIEW": 0, "OFFER_DRAFTED": 0, "NEEDS_CALL": 1, "FLEXIBLE": 1, "HOT_LEAD": 2, "DISCUSSING": 2, "RESPONDED": 3, "CONTACTED": 4}
    out = []
    for r in rows:
        d = db.row_to_dict(r)
        card = cards.get(d["id"])
        if not card and d.get("rating") != "HOT":
            continue
        d["outreach_id"], d["asana_task_id"] = card if card else (None, None)
        out.append(d)
    out.sort(key=lambda d: (order.get(d.get("lead_status") or "", 5), 0 if d.get("asana_task_id") else 1, d.get("lead_status_at") or ""), )
    out.sort(key=lambda d: (order.get(d.get("lead_status") or "", 5), 0 if d.get("asana_task_id") else 1))
    return out[:limit]


def stored_comps_fn(conn) -> Callable[[dict[str, Any]], list[dict[str, Any]]]:
    """Off the Mac there is no fresh sold pull: re-select from the sales the engine stored with the valuation."""
    def fn(p: dict[str, Any]) -> list[dict[str, Any]]:
        v = conn.execute("SELECT comps FROM valuations WHERE property_id=?", (p.get("id"),)).fetchone()
        return [{**c, "property_type": c.get("property_type") or p.get("property_type")} for c in json.loads((v[0] if v else None) or "[]")]
    return fn


def revalue(conn, cfg: Any, comps_fn: Callable | None, props: list[dict[str, Any]], run_date: str, why: str = "daily comp loop") -> list[dict[str, Any]]:
    """Re-run the valuation for these houses; log every ARV move of 10%+ or a grade that moved two letters."""
    from .ranking import rate
    from .valuation import review_exclusions, value_property
    changes = []
    thresh = float(cfg.get("comps.loop.change_pct", 10)) / 100
    fresh = comps_fn is not None
    comps_fn = comps_fn or stored_comps_fn(conn)
    for p in props:
        full = db.row_to_dict(conn.execute("SELECT * FROM properties WHERE id=?", (p["id"],)).fetchone())
        old = db.row_to_dict(conn.execute("SELECT arv, comp_grade, comp_notes FROM valuations WHERE property_id=?", (p["id"],)).fetchone()) or {}
        score = conn.execute("SELECT score FROM scores WHERE property_id=?", (p["id"],)).fetchone()
        if not score:
            continue
        try:
            res = value_property(full, cfg, comps_fn, exclude=review_exclusions(conn, p["id"]))
        except Exception as e:  # noqa: BLE001
            log.warning("comps loop: valuation failed for %s: %s", full.get("street"), e)
            continue
        if not fresh and old.get("arv") and res["arv_method"] not in ("comps", "comps_wide"):
            continue           # no sales on this machine and no comps-based number: nothing learned, keep the stored value
        from .valuation import store_valuation
        rating = rate(score["score"], res["list_price"], res["mao"], cfg, res["arv_method"])
        store_valuation(conn, p["id"], res, rating)
        moved = old.get("arv") and res["arv"] and abs(res["arv"] - old["arv"]) / old["arv"] >= thresh
        g_old, g_new = old.get("comp_grade") or "?", res.get("comp_grade") or "?"
        regraded = g_old != "?" and g_new != "?" and abs("ABCD".find(g_old) - "ABCD".find(g_new)) >= 2
        if moved or regraded:
            reason = f"{why}: {res['comp_notes'].get('basis', '')}" + (f"; ruled out {'; '.join(res['comp_notes'].get('dropped') or [])}" if res["comp_notes"].get("dropped") else "")
            conn.execute("INSERT INTO comps_changes(run_date,property_id,old_arv,new_arv,old_grade,new_grade,why,created_at) VALUES(?,?,?,?,?,?,?,?)",
                         (run_date, p["id"], old.get("arv"), res["arv"], g_old, g_new, reason[:600], iso()))
            changes.append({"property_id": p["id"], "street": full.get("street"), "city": full.get("city"), "old_arv": old.get("arv"),
                            "new_arv": res["arv"], "old_grade": g_old, "new_grade": g_new, "why": reason, "asana_task_id": p.get("asana_task_id")})
    return changes


# ---------- Claude's review ----------

def comp_lessons(conn, limit: int = 15) -> list[str]:
    """Rodrigo's own comp-related comments (lessons table), newest first, deduped."""
    try:
        rows = conn.execute("SELECT text FROM lessons ORDER BY created_at DESC, id DESC LIMIT 400").fetchall()
    except Exception:  # noqa: BLE001
        return []
    out, seen = [], set()
    for r in rows:
        t = " ".join((r[0] or "").split())
        if COMP_LESSON_RE.search(t) and t.lower()[:80] not in seen:
            seen.add(t.lower()[:80])
            out.append(t[:300])
        if len(out) >= limit:
            break
    return out


def review_prompt(p: dict[str, Any], sel: dict[str, Any], lessons: list[str]) -> str:
    f = {k for k, on in (sel.get("flags") or {}).items() if on}
    lines = [f"SUBJECT: {p.get('street')}, {p.get('city')} {p.get('zip')} · asking ${int(p.get('list_price') or 0):,}",
             f"type {p.get('property_type')} (engine read: {sel.get('product') or 'unclear'}) · {p.get('beds') or '?'} bd / {p.get('baths') or '?'} ba · "
             f"{p.get('sqft') or '?'} sf · lot {p.get('lot_sqft') or '?'} sf · built {p.get('year_built') or '?'} · HOA {p.get('hoa') or 0} · "
             f"units {p.get('units') or 1} · engine flags: {', '.join(sorted(f)) or 'none'}",
             f"remarks: {' '.join((p.get('remarks') or '').split())[:1200]}", ""]
    if lessons:
        lines += ["WHAT RODRIGO HAS SAID ABOUT COMPS ON OTHER CARDS (apply what fits):"] + [f"- {l}" for l in lessons] + [""]
    lines.append("SALES THE ENGINE KEPT (key | address | sold | price | sf | $/sf | bd | built | distance | condition | remarks):")
    for c in sel.get("comps") or []:
        lines.append(_comp_line(c))
    dropped = [(c, why) for c, why in (sel.get("dropped") or []) if why != "further down the ranking"][:12]
    if dropped:
        lines += ["", "SALES THE ENGINE DROPPED (and why) — say if one should come back by naming it in note:"]
        for c, why in dropped:
            lines.append(_comp_line(c) + f" | dropped: {why}")
    lines += ["", "Return drop (keys from the KEPT list only), subject_flags, arv_low, arv_high (renovated resale for this layout), confidence, note."]
    return "\n".join(lines)


def _comp_line(c: dict[str, Any]) -> str:
    d = f"{c['distance_mi']} mi" if c.get("distance_mi") is not None else "?"
    psf = f"${c['ppsf']:,.0f}" if c.get("ppsf") else "?"
    fl = c.get("flags") or {}
    extras = (" | pool" if fl.get("pool") or c.get("pool") else "") + (" | WATER" if fl.get("waterfront") or c.get("water") else "")
    return (f"{ce.comp_key(c)} | {c.get('address')} | {str(c.get('sold_date') or '')[:10]} | ${int(c.get('sold_price') or 0):,} | "
            f"{c.get('sqft') or '?'} | {psf} | {c.get('beds') or '?'} | {c.get('year_built') or '?'} | {d} | "
            f"{c.get('condition') or ce.condition_of(c.get('remarks'))}{extras} | {' '.join((c.get('remarks') or '').split())[:160]}")


def claude_review(conn, cfg: Any, llm, p: dict[str, Any], sel: dict[str, Any], run_date: str, lessons: list[str] | None = None) -> dict[str, Any] | None:
    """One structured call; the answer is validated (keys must be real) and stored. None when Claude is unreachable."""
    if llm is None or not getattr(llm, "available", False):
        return None
    lessons = lessons if lessons is not None else comp_lessons(conn)
    out = llm.structured("comps_review", REVIEW_SYSTEM, review_prompt(p, sel, lessons), REVIEW_SCHEMA, max_tokens=900)
    if not isinstance(out, dict):
        return None
    valid = {ce.comp_key(c) for c in sel.get("comps") or []}
    drops = [d for d in (out.get("drop") or []) if isinstance(d, dict) and d.get("key") in valid]
    keys = sorted({d["key"] for d in drops})
    if len(keys) > max(len(valid) - 2, 0):          # it may trim, never empty the table
        keys, drops = [], []
    lo, hi = out.get("arv_low") or 0, out.get("arv_high") or 0
    if not (0 < lo <= hi) or (p.get("list_price") and hi > 3 * p["list_price"]):
        lo = hi = 0
    note = " ".join(str(out.get("note") or "").split())[:500]
    if drops:
        note = (note + " Dropped: " + "; ".join(f"{d['key'].split('|')[0].title()} ({d['reason'][:80]})" for d in drops[:5])).strip()
    row = {"property_id": p["id"], "run_date": run_date, "exclude_keys": json.dumps(keys),
           "subject_flags": json.dumps([str(x)[:60] for x in (out.get("subject_flags") or [])][:8]),
           "arv_low": lo or None, "arv_high": hi or None, "confidence": out.get("confidence") if out.get("confidence") in ("low", "medium", "high") else None,
           "note": note, "model": getattr(llm, "model", None), "created_at": iso()}
    conn.execute("INSERT INTO comps_review(property_id,run_date,exclude_keys,subject_flags,arv_low,arv_high,confidence,note,model,created_at) "
                 "VALUES(:property_id,:run_date,:exclude_keys,:subject_flags,:arv_low,:arv_high,:confidence,:note,:model,:created_at)", row)
    return row


def review_due(conn, props: list[dict[str, Any]], cfg: Any) -> list[dict[str, Any]]:
    lp = cfg.get("comps.loop", {}) or {}
    every = int(lp.get("review_every_days", 7))
    cap = int(lp.get("claude_reviews_per_day", 15))
    out = []
    for p in props:
        last = conn.execute("SELECT run_date FROM comps_review WHERE property_id=? ORDER BY id DESC LIMIT 1", (p["id"],)).fetchone()
        if last and (datetime.strptime(today_et(), "%Y-%m-%d") - datetime.strptime(last[0][:10], "%Y-%m-%d")).days < every:
            continue
        out.append(p)
        if len(out) >= cap:
            break
    return out


# ---------- the loop ----------

def run(conn, cfg: Any, comps_fn: Callable | None = None, llm=None, asana=None, tune_params: bool = True,
        review: bool = True, run_date: str | None = None) -> dict[str, Any]:
    run_date = run_date or today_et()
    lp = cfg.get("comps.loop", {}) or {}
    if not lp.get("enabled", True):
        return {"skipped": "comps.loop.enabled is off"}
    run_id = db.start_run(conn, "comps", run_date)
    out: dict[str, Any] = {"date": run_date}
    # 1–2. back-test + tune on today's sold pools
    pools = sold_pools(conn)
    out["pools"] = len(pools)
    base = ce.params(cfg)
    cases = test_cases(pools, per_zip=int(lp.get("tests_per_zip", 25)), max_zips=int(lp.get("max_zips", 40)))
    baseline = backtest(pools, base, cases) if cases else {"n": 0}
    baseline["params"] = {k: base.get(k) for k in GRID}
    out["baseline"] = baseline
    if tune_params and cases and (baseline.get("n") or 0) >= 30:
        best, score, trials = tune(pools, base, cases, max_seconds=float(lp.get("tune_seconds", 240)))
        out["tuned"] = {"params": {k: best[k] for k in GRID}, **score, "trials": len(trials)}
        out["adopted"] = adopt(cfg, conn, best, score, baseline, run_date)
    else:
        out["tuned"] = None
        out["adopted"] = False
        if cases:
            conn.execute("INSERT INTO comps_calibration(run_date,scope,params,n_tests,mdape,mape,within_10_pct,baseline_mdape,adopted,note,created_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (run_date, "global", json.dumps(baseline["params"]), baseline.get("n"), baseline.get("mdape"),
                                                           baseline.get("mape"), baseline.get("within_10_pct"), baseline.get("mdape"), 0,
                                                           "measured only; by product " + json.dumps(baseline.get("by_product", {})), iso()))
    # 3. re-value the houses he is working with the (possibly new) rules
    props = working_set(conn)
    out["working_set"] = len(props)
    changes = revalue(conn, cfg, comps_fn, props, run_date) if props else []
    # 4. Claude's review of the comp set behind them, then re-value those
    reviewed, review_notes = 0, []
    if review and llm is not None and getattr(llm, "available", False) and props:
        lessons = comp_lessons(conn)
        for p in review_due(conn, props, cfg):
            full = db.row_to_dict(conn.execute("SELECT * FROM properties WHERE id=?", (p["id"],)).fetchone())
            cands = []
            if comps_fn:
                try:
                    cands = comps_fn(full) or []
                except Exception as e:  # noqa: BLE001
                    log.warning("comps loop: pull for %s failed: %s", full.get("street"), e)
            if not cands:
                cands = stored_comps_fn(conn)(full)
            if len(cands) < 3:
                continue
            sel = ce.select(full, cands, ce.params(cfg, ce.product(full)), want=12)
            if len(sel["comps"]) < 3:
                continue
            r = claude_review(conn, cfg, llm, full, sel, run_date, lessons)
            if r:
                reviewed += 1
                review_notes.append({"property_id": p["id"], "street": full.get("street"), "note": r["note"], "excluded": len(json.loads(r["exclude_keys"]))})
                changes += revalue(conn, cfg, comps_fn, [p], run_date, why="Claude's comp review")
    out["claude_reviews"] = reviewed
    out["review_notes"] = review_notes
    out["changes"] = changes
    # 5. tell him where it matters: one short comment on the lead card for a house he is engaged on
    posted = 0
    if asana is not None and lp.get("post_changes", True):
        for ch in changes:
            if not ch.get("asana_task_id") or not ch.get("old_arv"):
                continue
            try:
                asana.comment(ch["asana_task_id"], change_comment(ch))
                conn.execute("UPDATE comps_changes SET posted=1 WHERE property_id=? AND run_date=? AND new_arv=?", (ch["property_id"], run_date, ch["new_arv"]))
                posted += 1
            except Exception as e:  # noqa: BLE001
                log.warning("comps loop: comment on %s failed: %s", ch["asana_task_id"], e)
    out["posted"] = posted
    try:
        from .ranking import rerank
        out["rerank"] = rerank(conn, cfg)
    except Exception as e:  # noqa: BLE001
        log.warning("comps loop: rerank failed: %s", e)
    out["grades"] = grade_counts(conn)
    db.finish_run(conn, run_id, "ok", {k: v for k, v in out.items() if k in ("pools", "working_set", "claude_reviews", "posted", "adopted")})
    write_log(cfg, out)
    log.info("comps loop: %s", {k: out[k] for k in ("pools", "working_set", "claude_reviews", "posted", "adopted")})
    return out


def change_comment(ch: dict[str, Any]) -> str:
    arrow = "up" if ch["new_arv"] > ch["old_arv"] else "down"
    return (f"🤖 Comps re-checked ({today_et()}): resale value (ARV) {arrow} from ${ch['old_arv']:,} to ${ch['new_arv']:,}, "
            f"comps grade {ch['old_grade']} → {ch['new_grade']}. {ch['why'][:300]}. Comment 'numbers' for the full snapshot.")


def grade_counts(conn) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in conn.execute("SELECT COALESCE(comp_grade,'?') g, COUNT(*) n FROM valuations v JOIN properties p ON p.id=v.property_id "
                          "WHERE v.rating IN ('HOT','WARM') AND p.lead_status NOT IN ('EXCLUDED') GROUP BY g"):
        out[r[0]] = r[1]
    return out


def write_log(cfg: Any, out: dict[str, Any]) -> None:
    try:
        d = cfg.path("reports") / "comps"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"loop_{out['date']}.json").write_text(json.dumps(out, indent=1, default=str))
    except Exception as e:  # noqa: BLE001
        log.warning("comps loop: could not write log: %s", e)


# ---------- for the morning report ----------

def summary(conn, cfg: Any, run_date: str | None = None) -> dict[str, Any]:
    run_date = run_date or today_et()
    cal = conn.execute("SELECT * FROM comps_calibration ORDER BY id DESC LIMIT 1").fetchone()
    prev = conn.execute("SELECT mdape FROM comps_calibration WHERE run_date < ? ORDER BY id DESC LIMIT 1", (cal["run_date"] if cal else run_date,)).fetchone()
    changes = [db.row_to_dict(r) for r in conn.execute(
        "SELECT c.*, p.street, p.city FROM comps_changes c JOIN properties p ON p.id=c.property_id WHERE c.run_date>=date(?, '-1 day') "
        "ORDER BY c.id DESC LIMIT 8", (run_date,))]
    reviews = [db.row_to_dict(r) for r in conn.execute(
        "SELECT r.*, p.street, p.city FROM comps_review r JOIN properties p ON p.id=r.property_id WHERE r.run_date>=date(?, '-1 day') "
        "ORDER BY r.id DESC LIMIT 8", (run_date,))]
    tuned = {}
    try:
        tuned = json.loads(ce.tuning_path(cfg).read_text())
    except (OSError, ValueError):
        pass
    return {"calibration": db.row_to_dict(cal) if cal else None, "previous_mdape": prev[0] if prev else None, "changes": changes,
            "reviews": reviews, "grades": grade_counts(conn), "tuned": tuned.get("global"), "tuned_on": tuned.get("updated")}


def summary_lines(conn, cfg: Any, run_date: str | None = None) -> list[str]:
    """Plain lines for the report ('' when the loop has never run)."""
    s = summary(conn, cfg, run_date)
    cal = s["calibration"]
    if not cal and not s["changes"] and not s["grades"]:
        return []
    out = []
    if cal:
        n, md, w10 = cal.get("n_tests") or 0, cal.get("mdape"), cal.get("within_10_pct")
        if n and md is not None:
            trend = ""
            if s["previous_mdape"] is not None:
                d = md - s["previous_mdape"]
                trend = f" ({'better' if d < -0.05 else 'worse' if d > 0.05 else 'same'} than last time: {s['previous_mdape']:.1f}%)"
            out.append(f"Accuracy on real sales ({cal['run_date']}): the engine's number was within {md:.1f}% of the real sold price on the "
                       f"typical house{trend}; {w10:.0f}% of {n} test sales landed within 10%.")
            out.append(("Settings kept from today's tuning: " if cal.get("adopted") else "Settings unchanged today: ")
                       + _params_words(json.loads(cal.get("params") or "{}")) + (f" · tuned {s['tuned_on']}" if s.get("tuned_on") else ""))
        else:
            out.append("Accuracy: no sold pools in the database yet (the free scraper's sold pulls run on the Mac); nothing measured.")
    g = s["grades"]
    if g:
        tot = sum(g.values())
        out.append("Comps grades on HOT/WARM houses: " + ", ".join(f"{g.get(k, 0)} {k}" for k in "ABCD" if g.get(k)) +
                   (f", {g['?']} not graded yet" if g.get("?") else "") + f" (of {tot}). A = build on it, D = a guess.")
    for ch in s["changes"][:5]:
        if ch.get("old_arv") and ch.get("new_arv"):
            out.append(f"Changed: {ch['street']}, {ch['city']}: ARV ${ch['old_arv']:,} → ${ch['new_arv']:,} (grade {ch['old_grade']} → {ch['new_grade']}).")
    for r in s["reviews"][:5]:
        out.append(f"Claude's review · {r['street']}: {r.get('note') or 'no note'}")
    return out


def _params_words(p: dict[str, Any]) -> str:
    return (f"{p.get('radius_miles', '?')} mi radius, size ±{p.get('sqft_tol_pct', '?')}%, year built ±{p.get('year_tol', '?')}, "
            f"beds ±{p.get('beds_tol', '?')}")
