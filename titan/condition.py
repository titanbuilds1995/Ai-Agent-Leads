"""CONDITION CHECK: Claude looks at the listing's photos before a house reaches Fresh Leads (Rodrigo, 2026-11-18: "a lot of
the fresh leads are fresh renovations and properties that look really good … they're not going to sell at a discount, even if
they've been on the market for a long time … go in the listing and look at the pictures and not give me those listings").

Why they got through: the remarks only cost a renovated house some motivation points, and a house sitting 60+ days with a
price cut is HOT whatever its score. Condition was never looked at.

What this does, per house, once:
1. FREE: remarks that plainly say fully renovated / remodeled / new construction, with nothing about work → RENOVATED.
2. Otherwise the listing's own photos (kitchens and baths first; links already stored with the listing, a realtor.com
   address lookup on the Mac when they are not) go to Claude in ONE paid API call → RENOVATED / GOOD_SHAPE / DATED /
   NEEDS_WORK / CANT_TELL + confidence + one sentence on what it saw (table `condition_checks`).
3. RENOVATED (high / medium confidence) or GOOD_SHAPE (high) = not for us: the house is EXCLUDED (no email is written);
   a card already waiting in Fresh Leads goes to Skip with one comment, its email never sends. Not Rodrigo's vote on the
   email (learning.OFF_MARKET). Dragging the card back to Fresh Leads keeps the house for good.
4. Outreach only drafts first emails for houses that were looked at (`condition.gate_outreach`); the rest wait their turn.

Run-daily step `condition` (after the board clean-up), `make condition-check`, `make condition-list` (moves nothing).
The email writer is NOT told what the photos show: emails still never mention photos."""
from __future__ import annotations

import json
import re
import urllib.request
from typing import Any, Callable

from . import db
from .util import log, today_et

RENOVATED, GOOD_SHAPE, DATED, NEEDS_WORK, CANT_TELL = "RENOVATED", "GOOD_SHAPE", "DATED", "NEEDS_WORK", "CANT_TELL"
WORDS = {RENOVATED: "looks freshly renovated", GOOD_SHAPE: "looks in really good shape", DATED: "dated", NEEDS_WORK: "needs work",
         CANT_TELL: "can't tell from the photos"}
DEFAULT_DROP = {RENOVATED: ["high", "medium"], GOOD_SHAPE: ["high"]}

# Remarks that settle it for free. Only the plain statements; "updated kitchen" or "move-in ready" alone goes to the photos.
RENO_RE = re.compile(
    r"\b(fully|completely|totally|newly|beautifully|recently|tastefully|just|freshly|professionally|gorgeously|stunningly) "
    r"(renovated|remodeled|remodelled|rehabbed|redone|rebuilt)\b|\b(renovated|remodeled|updated) (from top to bottom|top to bottom)\b|"
    r"\b(full|complete|total|gut) (renovation|remodel)\b|\bbrand new (construction|home|house|everything)\b|\bnew construction\b|"
    r"\bno expense (was )?spared\b|\bnothing to do but move in\b", re.I)
WORK_RE = re.compile(
    r"\bas[\s-]is\b|\bneeds? (some |a little |lots of |a lot of )?(tlc|work|updating|repairs?|renovation|love|attention)\b|\bhandyman\b|"
    r"\bfixer\b|\bcontractor'?s? special\b|\binvestor'?s? special\b|\bbring your\b|\brehab\b|\bdeferred maintenance\b|\bcash only\b|"
    r"\b(fire|water|storm|hurricane) damage\b|\bmold\b|\bunfinished\b|\bneeds to be completed\b|\bpartially (renovated|remodeled)\b|"
    r"\b(renovation|remodel) (started|in progress|not (finished|completed))\b", re.I)

SCHEMA = {"type": "object", "properties": {
    "verdict": {"type": "string", "enum": [RENOVATED, GOOD_SHAPE, DATED, NEEDS_WORK, CANT_TELL]},
    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    "reason": {"type": "string"}},
    "required": ["verdict", "confidence", "reason"], "additionalProperties": False}

PROMPT = """You screen house listings for a South Florida investor who buys houses that need work, at a discount, and resells them.
He does NOT want houses that are already renovated or in really good shape: those sell near full price however long they sit.
You get the listing's photos and the agent's remarks. Judge the CONDITION THE PHOTOS SHOW.

- RENOVATED: recently redone, flip style, or new: new kitchen (new cabinets, stone counters, new appliances), redone baths, new
  floors, fresh paint, often staged; or new construction.
- GOOD_SHAPE: not a fresh flip, but clean, updated and well kept. A buyer moves in and does nothing. Nothing in it for an investor.
- DATED: livable but old: original or tired kitchen and baths, old floors, worn finishes. It needs updating.
- NEEDS_WORK: visible damage or disrepair, gutted or unfinished rooms, heavy deferred maintenance.
- CANT_TELL: the photos do not show the inside (exterior only, aerials, maps, floor plans), or they disagree with each other.

Rules:
- Kitchens and baths decide it. One updated room in an otherwise old house is DATED.
- Renderings and virtual staging of an old empty house are not a renovation.
- The remarks are the agent selling; believe the photos over the remarks when they disagree.
- When honestly between GOOD_SHAPE and DATED, say DATED: he would rather look at one extra house than miss a deal.
- confidence: high only when the kitchen or a bath is clearly visible and leaves no doubt.
- reason: ONE plain sentence naming what you saw ("New white shaker kitchen with quartz counters, both baths redone, new vinyl
  floors throughout"). No advice, no selling."""

Fetch = Callable[[dict[str, Any]], "list[tuple[bytes, str]] | None"]


# ---------- photos ----------

def _photos(p: dict[str, Any]) -> list[tuple[str, set[str]]]:
    """(link, labels) for every photo in the stored realtor.com record. A record cut off at the storage limit is read by pattern."""
    raw = p.get("raw")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return [(u, set()) for u in dict.fromkeys(re.findall(r'"href":\s*"(https://[^"]*rdcpix[^"]*)"', raw))]
    out: list[tuple[str, set[str]]] = []
    for ph in ((raw.get("photos") or []) if isinstance(raw, dict) else []):
        if isinstance(ph, dict) and str(ph.get("href") or "").startswith("http"):
            out.append((ph["href"], {str(t.get("label") or "").lower() for t in (ph.get("tags") or []) if isinstance(t, dict)}))
        elif isinstance(ph, str) and ph.startswith("http"):
            out.append((ph, set()))
    return out


def photo_urls(p: dict[str, Any], limit: int = 8) -> list[str]:
    """The photos worth looking at: the front, then kitchens and baths (realtor.com labels them), then an even spread of the rest."""
    items = _photos(p)
    if not items:
        return []
    picked = [items[0][0]]

    def take(word: str, n: int) -> None:
        for href, labels in items:
            if n and href not in picked and any(word in lab for lab in labels):
                picked.append(href)
                n -= 1

    take("kitchen", 3)
    take("bath", 2)
    rest = [h for h, _ in items if h not in picked]
    need = limit - len(picked)
    if need > 0 and rest:
        step = max(1, len(rest) // need)
        picked += rest[::step][:need]
    return picked[:limit]


def _variants(href: str) -> list[str]:
    """realtor.com stores a thumbnail link (…s.jpg); the same picture exists bigger. Bigger first, the thumbnail as a last resort."""
    if href.endswith("s.jpg"):
        return [href[:-5] + "od-w640_h480.jpg", href[:-5] + "od-w480_h360_x2.webp?w=1080&q=75", href]
    return [href]


def _media_type(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _download(url: str, timeout: float = 15.0) -> tuple[bytes, str] | None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                                                             "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — https links from the listing record
            data = r.read(4_000_000)
    except Exception:  # noqa: BLE001
        return None
    mt = _media_type(data)
    return (data, mt) if mt else None


def default_fetch(cfg: Any, scrape=None, download=_download) -> Fetch:
    """Photos of one house as (bytes, media type). [] = the listing has no photos; None = it has, but none could be loaded
    (network): the house is tried again on the next run."""
    limit = int(cfg.get("condition.photos_per_house", 8))
    best = {"i": 0}            # the size that loaded last is tried first

    def fn(p: dict[str, Any]) -> list[tuple[bytes, str]] | None:
        urls = photo_urls(p, limit)
        if not urls:
            try:                # nothing stored with the listing: one realtor.com address lookup (works on the Mac only)
                from .board_clean import homeharvest_record
                rec = homeharvest_record(cfg, scrape)(p)
                urls = photo_urls({"raw": rec}, limit) if rec else []
            except Exception as e:  # noqa: BLE001
                log.warning("condition: photo lookup for %s failed: %s", p.get("street"), e)
                return None
        if not urls:
            return []
        out = []
        for u in urls:
            vs = _variants(u)
            order = sorted(range(len(vs)), key=lambda i: i != min(best["i"], len(vs) - 1))
            for i in order:
                got = download(vs[i])
                if got:
                    best["i"] = i
                    out.append(got)
                    break
        return out or None
    return fn


# ---------- the verdict ----------

def remarks_verdict(p: dict[str, Any]) -> dict[str, Any] | None:
    text = p.get("remarks") or ""
    m = RENO_RE.search(text)
    if m and not WORK_RE.search(text):
        return {"verdict": RENOVATED, "confidence": "high", "reason": f'The listing says "{" ".join(m.group(0).split())}".', "source": "remarks",
                "photos_seen": 0}
    return None


def look(llm: Any, p: dict[str, Any], images: list[tuple[bytes, str]]) -> dict[str, Any] | None:
    facts = {k: p.get(k) for k in ("beds", "baths", "sqft", "year_built", "property_type", "list_price", "days_on_market")}
    text = (f"{len(images)} photos of this listing are above.\nFacts: {json.dumps(facts)}\n"
            f"Agent's remarks: {(p.get('remarks') or '(none)')[:2500]}")
    fn = getattr(llm, "read_images", None)
    out = fn("condition", images, text, SCHEMA, system=PROMPT) if callable(fn) else None
    if not isinstance(out, dict) or out.get("verdict") not in WORDS:
        return None
    return {"verdict": out["verdict"], "confidence": out.get("confidence") or "low", "reason": " ".join(str(out.get("reason") or "").split())[:400],
            "source": "photos", "photos_seen": len(images)}


def get(conn, property_id: int) -> dict[str, Any] | None:
    return db.row_to_dict(conn.execute("SELECT * FROM condition_checks WHERE property_id=?", (property_id,)).fetchone())


def drops(row: dict[str, Any] | None, cfg: Any) -> bool:
    """True when this check says the house is not for us. A house Rodrigo put back is never dropped again."""
    if not row or row.get("kept_at"):
        return False
    rule = cfg.get("condition.drop", None) or DEFAULT_DROP
    return (row.get("confidence") or "low") in (rule.get(row.get("verdict")) or [])


def check(conn, cfg: Any, llm: Any, p: dict[str, Any], fetch: Fetch) -> tuple[dict[str, Any] | None, str]:
    """Check one house and store the result. → (row, how): how is remarks / photos / no_photos, or photo_error / claude_error
    (nothing stored, tried again next run)."""
    res = remarks_verdict(p)
    how = "remarks"
    if res is None:
        images = fetch(p)
        if images is None:
            return None, "photo_error"
        if len(images) < int(cfg.get("condition.min_photos", 3)):
            res, how = {"verdict": CANT_TELL, "confidence": "low", "reason": "The listing has too few photos to judge.", "source": "none",
                        "photos_seen": len(images)}, "no_photos"
        else:
            res, how = look(llm, p, images), "photos"
            if res is None:
                return None, "claude_error"
    conn.execute("INSERT OR REPLACE INTO condition_checks(property_id,verdict,confidence,reason,source,photos_seen,checked_at) VALUES(?,?,?,?,?,?,?)",
                 (p["id"], res["verdict"], res["confidence"], res["reason"], res["source"], res["photos_seen"], db.iso()))
    return get(conn, p["id"]), how


def gate(conn, cfg: Any, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Outreach candidates that were looked at and are still for us. Unchecked houses wait for their check."""
    conf = cfg.get("condition", {}) or {}
    if not conf.get("enabled", False) or not conf.get("gate_outreach", False):
        return candidates
    rows = {r["property_id"]: db.row_to_dict(r) for r in conn.execute("SELECT * FROM condition_checks").fetchall()}
    return [p for p in candidates if p["id"] in rows and not drops(rows[p["id"]], cfg)]


def _line(p: dict[str, Any], row: dict[str, Any]) -> str:
    src = {"remarks": "from the remarks", "photos": f"{row.get('photos_seen')} photos looked at"}.get(row.get("source"), "")
    return f"{p.get('street')}: {WORDS.get(row['verdict'], row['verdict'])}{f' ({src})' if src else ''}. {row.get('reason') or ''}".strip()


def _exclude(conn, p: dict[str, Any], row: dict[str, Any]) -> None:
    conn.execute("UPDATE condition_checks SET acted_at=?, prev_status=? WHERE property_id=?", (db.iso(), p.get("lead_status") or "NONE", p["id"]))
    conn.execute("UPDATE properties SET lead_status='EXCLUDED', lead_status_at=?, lead_note=? WHERE id=?",
                 (db.iso(), f"condition: {WORDS.get(row['verdict'], row['verdict'])} ({row.get('source')}, checked {today_et()}). {row.get('reason') or ''}"[:500],
                  p["id"]))


def run(conn, cfg: Any, asana, llm, fetch: Fetch | None = None, dry_run: bool = False, limit: int | None = None) -> dict[str, Any]:
    """Fresh Leads cards first, then the houses outreach would card next. Never raises."""
    from .learning import OFF_MARKET as DECISION, record_decision
    conf = cfg.get("condition", {}) or {}
    if not conf.get("enabled", False):
        return {"skipped": "condition.enabled is off"}
    n: dict[str, Any] = {"checked": 0, "by_remarks": 0, "by_photos": 0, "no_photos": 0, "errors": 0, "to_skip": 0, "partly": 0,
                         "excluded_before_card": 0, "put_back_by_you": 0, "waiting_for_a_check": 0, "verdicts": {}, "dropped": []}
    cap = int(limit or conf.get("max_per_run", 250))
    max_errors = int(conf.get("stop_after_errors", 5))
    fetch = fetch or default_fetch(cfg)
    streak = {"n": 0}

    def checked(p: dict[str, Any]) -> dict[str, Any] | None:
        row = get(conn, p["id"])
        if row or n["checked"] >= cap or streak["n"] >= max_errors:
            return row
        try:
            row, how = check(conn, cfg, llm, p, fetch)
        except Exception as e:  # noqa: BLE001 — one house failing changes nothing
            log.warning("condition: %s failed: %s", p.get("street"), e)
            row, how = None, "claude_error"
        if row is None:
            n["errors"] += 1
            streak["n"] += 1
            n["last_error"] = {"photo_error": "the photos would not load", "claude_error": "Claude did not answer (API key / credit?)"}.get(how, how)
            return None
        streak["n"] = 0
        n["checked"] += 1
        n[{"remarks": "by_remarks", "photos": "by_photos"}.get(how, "no_photos")] += 1
        n["verdicts"][row["verdict"]] = n["verdicts"].get(row["verdict"], 0) + 1
        return row

    a = cfg.get("outreach.asana", {}) or {}
    cards = []
    if asana and a.get("to_review") and a.get("skip"):
        from .approvals import parse_card
        try:
            cards = asana.section_tasks(a["to_review"])
        except Exception as e:  # noqa: BLE001
            log.warning("condition: Fresh Leads unreadable: %s", e)
            n["error"] = str(e)[:200]
    for t in cards:
        card = parse_card(t.get("notes") or "")
        if not card or card.get("reply_to"):
            continue
        row = db.row_to_dict(conn.execute("SELECT * FROM outreach WHERE id=?", (card["outreach_id"],)).fetchone())
        if not row or (row.get("kind") or "").startswith("backup"):
            continue
        pids = json.loads(row.get("property_ids") or "[]")
        props = [db.row_to_dict(r) for r in conn.execute(
            f"SELECT * FROM properties WHERE id IN ({','.join('?' * len(pids)) or 'NULL'})", pids).fetchall()]
        if not props:
            continue
        if row.get("status") == "skipped":
            # a card we moved to Skip that is back in Fresh Leads: Rodrigo wants this house. Keep it for good.
            acted = [p for p in props if (get(conn, p["id"]) or {}).get("acted_at")]
            if acted and not dry_run:
                for p in acted:
                    prev = get(conn, p["id"]).get("prev_status") or "NONE"
                    conn.execute("UPDATE condition_checks SET kept_at=? WHERE property_id=?", (db.iso(), p["id"]))
                    conn.execute("UPDATE properties SET lead_status=?, lead_status_at=?, lead_note=NULL WHERE id=? AND lead_status='EXCLUDED'",
                                 (prev, db.iso(), p["id"]))
                conn.execute("UPDATE outreach SET status='pending_approval' WHERE id=?", (row["id"],))
                conn.execute("DELETE FROM card_decisions WHERE card_gid=? AND decision=?", (t["gid"], DECISION))
                n["put_back_by_you"] += 1
            continue
        if row.get("status") != "pending_approval":
            continue
        rows = [(p, checked(p)) for p in props]
        bad = [(p, r) for p, r in rows if drops(r, cfg) and not r.get("acted_at")]
        if not bad:
            continue
        lines = " ".join(_line(p, r) for p, r in bad)
        whole = all(drops(r, cfg) for _, r in rows)
        n["dropped"].append({"agent": row.get("to_address"), "houses": lines, "card": whole})
        if dry_run:
            n["to_skip" if whole else "partly"] += 1
            continue
        for p, r in bad:
            _exclude(conn, p, r)
        try:
            if not whole:
                asana.comment(t["gid"], f"Condition check: {lines} The other house on this email was not ruled out, so the card stays; "
                                        "edit the email if you still want to send it.")
                n["partly"] += 1
                continue
            try:
                comments = asana.comments(t["gid"])
            except Exception:  # noqa: BLE001
                comments = []
            record_decision(conn, t["gid"], row["id"], "first_touch", DECISION, comments)     # not his vote on the email
            conn.execute("UPDATE outreach SET status='skipped' WHERE id=? AND status='pending_approval'", (row["id"],))
            asana.move(t["gid"], a["skip"])
            asana.comment(t["gid"], f"Condition check: {lines} Not a discount house, so it moved to Skip; the email was never sent and "
                                    "won't be. Drag it back to Fresh Leads if that's wrong and I'll keep it.")
            n["to_skip"] += 1
        except Exception as e:  # noqa: BLE001
            log.warning("condition: card %s: %s", t["gid"], e)
            n["errors"] += 1
    # the houses outreach would card next, in its own order
    try:
        from .outreach.send import _already_contacted_property, candidate_rows
        for p in candidate_rows(conn, cfg, gated=False):
            row = get(conn, p["id"])
            if row is None:
                if n["checked"] >= cap or streak["n"] >= max_errors:
                    n["waiting_for_a_check"] += 1
                    continue
                if _already_contacted_property(conn, p["id"]):
                    continue
                row = checked(p)
            if row and drops(row, cfg) and not row.get("acted_at"):
                n["dropped"].append({"agent": p.get("a_email"), "houses": _line(p, row), "card": False})
                n["excluded_before_card"] += 1
                if not dry_run:
                    _exclude(conn, p, row)
    except Exception as e:  # noqa: BLE001
        log.warning("condition: candidates: %s", e)
        n["error"] = str(e)[:200]
    if streak["n"] >= max_errors:
        n["warning"] = f"stopped after {max_errors} failures in a row: {n.get('last_error')}"
    n["cost_usd"] = round(getattr(llm, "session_cost", 0.0) or 0.0, 2)
    return n


def plain(n: dict[str, Any]) -> str:
    if n.get("skipped"):
        return f"Condition check skipped: {n['skipped']}"
    v = n.get("verdicts") or {}
    out = [f"Condition check: {n.get('checked', 0)} houses looked at ({n.get('by_photos', 0)} by photos, {n.get('by_remarks', 0)} from the remarks, "
           f"{n.get('no_photos', 0)} had no photos) · about ${n.get('cost_usd', 0):.2f}",
           "  what it saw: " + (", ".join(f"{WORDS.get(k, k)} {c}" for k, c in sorted(v.items(), key=lambda kv: -kv[1])) or "nothing new"),
           f"  Fresh Leads cards moved to Skip: {n.get('to_skip', 0)} · one house of two ruled out: {n.get('partly', 0)} · "
           f"ruled out before a card was made: {n.get('excluded_before_card', 0)} · you put back: {n.get('put_back_by_you', 0)}",
           f"  still waiting for a check: {n.get('waiting_for_a_check', 0)} · failed: {n.get('errors', 0)}"]
    for g in (n.get("dropped") or [])[:60]:
        out.append(f"  {'SKIP' if g['card'] else 'OUT '} {g['agent']}: {g['houses']}")
    if n.get("warning"):
        out.append(f"  WARNING: {n['warning']}")
    return "\n".join(out)
