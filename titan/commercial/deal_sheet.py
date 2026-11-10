"""The deal sheet of a 1031 listing (Rodrigo, 2026-11-10, Byronessa: "add this offer … into our website so I can see what numbers we
are negotiating, what numbers they are asking, where we land and all of the details we found … an internal and an external side:
internal tells me what we are getting it for and how it makes sense, the outside what works for them, the price we will sell it at").

The numbers live on the 1031 card on Asana, never in this repository (it is public): any comment on the card that contains the words
DEAL SHEET is read line by line (`Key: value`), oldest first, a later value replacing an earlier one, so Rodrigo or a session can post
a one-line update ("DEAL SHEET\nTheir counter: $4,100,000"). Rodrigo's own offer email in the broker thread (`outreach` kind om_rodrigo,
see outreach.record_rodrigo_message) fills "Our offer" by itself and marks it sent. Stored in `commercial_deals`; the site
(site.build_deals) shows the whole sheet in the private view and only the investor side (our price to the investor, the cap at that
price, the pitch) in the investor view, never our contract price, the asking price or the LoopNet link.

Keys: Asking · Our offer · Offer status · Their counter · Landed at · Our ceiling · Investor price · Investor cap · Investor NOI ·
Real NOI · and as many `Internal:`, `Investor:` and `Found:` lines as needed (a sheet that has any of one kind replaces that list)."""
from __future__ import annotations

import json
import re
from typing import Any

from .. import db
from ..util import iso, log

SHEET_RE = re.compile(r"\bDEAL SHEET\b", re.I)
LINE_RE = re.compile(r"^\s*[-•*]?\s*([A-Za-z][A-Za-z /]{1,24}?)\s*:\s*(.*\S)?\s*$")
MONEY_RE = re.compile(r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(m|mm|million|k|thousand)?\b", re.I)
PCT_RE = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s*%?")
FIELDS = {"asking": "asking", "asking price": "asking", "our offer": "our_offer", "offer": "our_offer", "offer status": "offer_status",
          "their counter": "counter", "counter": "counter", "landed at": "landed", "landed": "landed", "contract price": "landed",
          "our ceiling": "ceiling", "ceiling": "ceiling", "investor price": "investor_price", "price to investor": "investor_price",
          "investor cap": "investor_cap", "investor noi": "investor_noi", "real noi": "real_noi", "noi": "real_noi"}
MONEY_FIELDS = {"asking", "our_offer", "counter", "landed", "ceiling", "investor_price", "investor_noi", "real_noi"}
LISTS = {"internal": "internal", "note": "internal", "investor": "investor", "pitch": "investor", "found": "found"}
WATCH_COLUMNS = ("Needs Me", "Good fit", "In touch with the agent", "Books received", "Offer out", "Closed")


def money(v: str) -> int | None:
    m = MONEY_RE.search(v or "")
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    n *= 1_000_000 if unit in ("m", "mm", "million") else 1_000 if unit in ("k", "thousand") else 1
    return int(round(n)) if n >= 1000 else None


def parse_sheet(text: str) -> dict[str, Any]:
    """One comment → {field: value, internal: [...], investor: [...], found: [...]} (only what the comment says)."""
    out: dict[str, Any] = {}
    for line in (text or "").splitlines():
        m = LINE_RE.match(line)
        if not m or not m.group(2):
            continue
        key, val = m.group(1).strip().lower(), m.group(2).strip()
        if key in LISTS:
            out.setdefault(LISTS[key], []).append(val)
            continue
        f = FIELDS.get(key)
        if not f:
            continue
        if f in MONEY_FIELDS:
            n = money(val)
            if n is None:
                continue
            out[f] = n
            note = re.search(r"\(([^)]+)\)", val)
            if f == "our_offer" and note:
                out.setdefault("offer_status", note.group(1).strip())
        elif f == "investor_cap":
            p = PCT_RE.search(val)
            if p:
                out[f] = float(p.group(1))
        else:
            out[f] = val
    return out


def merge(sheets: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """Oldest → newest; later fields win, a later list replaces the earlier one. `_at` keeps when each field was last set."""
    out: dict[str, Any] = {"_at": {}}
    for at, s in sorted(sheets, key=lambda x: x[0] or ""):
        for k, v in s.items():
            out[k] = v
            out["_at"][k] = at
    return out


def engine_offer(conn, listing_id: str) -> dict[str, Any] | None:
    """Rodrigo's latest offer email in the broker thread, as the engine logged it (kind om_rodrigo)."""
    from ..offers import offer_price
    row = conn.execute("SELECT price FROM commercial_listings WHERE listing_id=?", (listing_id,)).fetchone()
    ask = row[0] if row and row[0] else None
    for r in conn.execute("SELECT body, sent_at FROM outreach WHERE kind='om_rodrigo' AND property_ids LIKE ? ORDER BY sent_at DESC",
                          (f'%"loopnet:{listing_id}"%',)).fetchall():
        p = offer_price(r[0] or "", ask)
        if p and p >= (0.25 * ask if ask else 250_000):
            return {"price": p, "at": r[1]}
    return None


def computed(sheet: dict[str, Any], listing: dict[str, Any] | None = None, offer: dict[str, Any] | None = None) -> dict[str, Any]:
    """The sheet as the site shows it: engine offer folded in, asking from the listing, spread and investor NOI worked out."""
    d = {k: v for k, v in (sheet or {}).items() if k != "_at"}
    at = (sheet or {}).get("_at") or {}
    if offer and (not d.get("our_offer") or (offer.get("at") or "") >= (at.get("our_offer") or "")):
        d["our_offer"] = offer["price"]
        d["offer_status"] = f"sent {str(offer.get('at') or '')[:10]}".strip()
    if not d.get("asking") and listing and listing.get("price"):
        d["asking"] = listing["price"]
    if d.get("investor_price") and d.get("investor_cap") and not d.get("investor_noi"):
        d["investor_noi"] = int(round(d["investor_price"] * d["investor_cap"] / 100))
    basis = d.get("landed") or d.get("our_offer")
    if d.get("investor_price") and basis:
        d["spread"] = d["investor_price"] - basis
    if d.get("asking") and d.get("our_offer"):
        d["offer_pct_of_ask"] = round(d["our_offer"] / d["asking"] * 100, 1)
    for k in ("internal", "investor", "found"):
        d[k] = [str(x) for x in (d.get(k) or [])]
    return d


def load(conn) -> dict[str, dict[str, Any]]:
    out = {}
    for r in conn.execute("SELECT listing_id, data FROM commercial_deals").fetchall():
        try:
            out[r[0]] = json.loads(r[1] or "{}")
        except json.JSONDecodeError:
            continue
    return out


def sync(conn, cfg: Any, asana, publish: bool = True) -> dict[str, Any]:
    """Read the DEAL SHEET comments on the working cards, store them, republish the site when anything changed."""
    from .board import read_board
    res: dict[str, Any] = {"cards": 0, "sheets": 0, "changed": 0, "published": False}
    project = cfg.get("commercial.asana.project")
    if asana is None or not project:
        return res
    board = read_board(asana, project)
    for lid, info in board.items():
        if info.get("column") not in WATCH_COLUMNS:
            continue
        res["cards"] += 1
        try:
            comments = asana.comments(info["gid"])
        except Exception as e:  # noqa: BLE001
            log.warning("deal sheet comments %s: %s", lid, e)
            continue
        sheets = [(c.get("created_at") or f"{i:06d}", parse_sheet(c.get("text") or "")) for i, c in enumerate(comments) if SHEET_RE.search(c.get("text") or "")]
        sheet = merge([s for s in sheets if s[1]]) if sheets else {}
        offer = engine_offer(conn, lid)
        if not sheet.get("_at") and not offer:
            continue
        res["sheets"] += 1
        data = json.dumps({"sheet": sheet, "offer": offer, "card_gid": info["gid"]}, sort_keys=True, default=str)
        cur = conn.execute("SELECT data FROM commercial_deals WHERE listing_id=?", (lid,)).fetchone()
        if cur and cur[0] == data:
            continue
        conn.execute("INSERT INTO commercial_deals (listing_id, data, updated_at) VALUES (?,?,?) "
                     "ON CONFLICT(listing_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at", (lid, data, iso()))
        res["changed"] += 1
    if res["changed"] and publish:
        from .site import publish as publish_site
        out = publish_site(conn, cfg)
        res["published"] = bool(out.get("uploaded"))
        res["site"] = {k: out.get(k) for k in ("uploaded", "skipped", "error") if out.get(k)}
    return res


def row_for(conn, listing_id: str) -> dict[str, Any] | None:
    r = conn.execute("SELECT data FROM commercial_deals WHERE listing_id=?", (listing_id,)).fetchone()
    return db.row_to_dict(r) if r else None
