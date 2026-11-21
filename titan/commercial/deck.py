"""The new deck: $9M to $12M (Rodrigo, 2026-11-21: "analyze and scrape all the properties on the market that are from $9 million to
$12 million that fit our 8 cap rate … add another board on our Asana … new properties, 9 to 12 million, new deck … run it through the
same process that we have … I'm going to give my investor access to this").

A second price box on top of the 1031 screen. Everything else is the screen he already tuned: the 8 cap floor, the asset types, the
airport limit, building quality, round one's income screen (vacant / owner-user / development / business with the building out, a
hotel's cap screened at 75%, small Miami-Dade multifamily out), score, back-solved cap, offer anchor, CLEAN / SUSPECT / NEEDS-OM,
evidence grade.

One run (`make deck`; `make deck-list` shows what it would do and touches nothing):
  1. PULL  every LoopNet search family for Florida with the price filter at the deck's box, through Apify. The rows are scored with
           the deck's box and stored; `in_deck` marks the ones that fit. `in_box` stays the 1031 box's answer ($3.5M to $8M), so
           the 1031 blast, the gone-marking of a full 1031 pull and the 1031 counts never see deck listings. A failed pull is
           reported and the run goes on with the deck listings already stored.
  2. BOARD "Titan 1031 · New Deck · $9M to $12M" (`commercial.asana.deck_project`): same columns as the 1031 board, read by the
           engine as part of it (working.py), so drags, sends, answers, books, reads and Needs Me work there. One card per listing,
           in New.
  3. EMAIL the settled process: a listing that SHOWS a cap carries the OM request on its card and goes out when he drags the card
           to "Approved · send OM request"; a listing with NO cap gets the eight-cap question, one email per broker for all of
           that broker's deck listings, queued at the commercial pace (never a hotel with no cap, never a broker Charles already
           emailed about anything, a do-not-contact or missing address: the card says why).
The site: deck listings are in deals.json (site.load_listings), so a card he drags to Good fit shows in the investor view."""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from collections import Counter
from typing import Any

from .. import db
from ..util import iso, log, money, now_utc, today_et
from .medical import _emailed, _row, _skip_reason
from .om_request import medical_request, validate_om_request

KV = "deck_campaign:"
DEFAULT_BOX = {"price_min": 9_000_000, "price_max": 12_000_000, "sweet_spot_max": 12_000_000}


def deck_cfg(cfg: Any) -> Any:
    """The same config with the deck's price box in place of the 1031 one, and its own reports folder."""
    raw = copy.deepcopy(cfg.raw)
    c = raw.setdefault("commercial", {})
    d = c.get("deck") or {}
    c["buyer_box"] = {**(c.get("buyer_box") or {}), **DEFAULT_BOX, **(d.get("buyer_box") or {})}
    c["outputs"] = {**(c.get("outputs") or {}), "dir": d.get("outputs_dir", "reports/commercial/deck")}
    return dataclasses.replace(cfg, raw=raw)


def box_words(cfg: Any) -> str:
    b = deck_cfg(cfg).get("commercial.buyer_box", {})
    return f"${b['price_min'] / 1e6:g}M to ${b['price_max'] / 1e6:g}M"


def pull(conn, cfg: Any, apify, gmail=None, email: bool = False) -> dict[str, Any]:
    """LoopNet at the deck's prices, scored with the deck's box. Marks nothing gone. Afterwards every pulled row gets `in_deck`
    and its `in_box` is put back to the 1031 box's answer."""
    if apify is None:
        return {"skipped": "no Apify client (APIFY_TOKEN missing)"}
    from .run import run
    from .scoring import in_box
    try:
        out = run(conn, deck_cfg(cfg), apify, asana=None, gmail=gmail, push_asana=False, email=email, site=False, full_pull=False)
    except Exception as e:  # noqa: BLE001
        log.exception("deck pull failed: %s", e)
        return {"error": f"{type(e).__name__}: {e}"[:200]}
    if out.get("error"):
        return {"error": out["error"]}
    for L in out.get("ranked") or []:
        ok, why = in_box(L, cfg)                       # what the 1031 box says about it (price outside the box)
        conn.execute("UPDATE commercial_listings SET in_deck=?, deck_note=?, in_box=?, box_note=? WHERE listing_id=?",
                     (1 if L.get("in_box") is True else 0, L.get("box_note") or "", None if ok is None else int(ok), why, L["listing_id"]))
    counts = out.get("counts") or {}
    return {"rows": counts.get("total"), "in_deck": counts.get("in_box"), "out": counts.get("out_of_box"), "undisclosed": counts.get("undisclosed"),
            "sheets_pending": counts.get("sheets_pending"), "cost_usd": (out.get("cost") or {}).get("total_usd"), "files": out.get("files"),
            "emailed": out.get("emailed")}


def listings(conn) -> list[dict[str, Any]]:
    """Active listings in the deck, each with a `lane`: "cap" (shows a cap, at or over the floor) or "no_cap". Stated caps first,
    then by score."""
    out = []
    for r in conn.execute("SELECT * FROM commercial_listings WHERE in_deck=1 AND gone_at IS NULL AND COALESCE(status,'active')='active'").fetchall():
        L = _row(db.row_to_dict(r))
        L["lane"] = "cap" if L.get("stated_cap") is not None else "no_cap"
        out.append(L)
    out.sort(key=lambda L: (L["lane"] != "cap", -(L.get("score") or 0)))
    return out


def dropped(conn) -> list[tuple[str, int]]:
    """Why the pulled rows that are not in the deck fell out, most common first (the process of elimination)."""
    c: Counter = Counter()
    for (note,) in conn.execute("SELECT deck_note FROM commercial_listings WHERE in_deck=0").fetchall():
        note = (note or "price not disclosed").split(":")[0]
        c["stated cap under the floor" if note.startswith("stated cap") else note.split(" (")[0]] += 1
    return c.most_common()


def plan(conn, cfg: Any, queue: bool = True) -> dict[str, Any]:
    """What a run would do, touching nothing: the listings, the queued email per broker, which cards carry an OM request to drag,
    and why a listing gets neither."""
    Ls = listings(conn)
    emailed = _emailed(conn)
    skip_assets = set(cfg.get("commercial.outreach.blast_skip_assets", []) or [])
    by: dict[str, list[dict[str, Any]]] = {}
    notes: dict[str, str] = {}
    drag: list[str] = []
    for L in Ls:
        lid, em = str(L["listing_id"]), (L.get("broker_email") or "").strip().lower()
        why = _skip_reason(conn, em, emailed)
        if not why and L["lane"] == "no_cap" and L.get("asset_class") in skip_assets:
            why = f"a {L.get('asset_class')} with no cap rate on the listing is not asked (none held an 8 cap in round one)"
        if why:
            notes[lid] = why
            continue
        by.setdefault(em, []).append(L)
    emails = []
    for em, group in by.items():
        if not any(L["lane"] == "no_cap" for L in group):
            drag += [str(L["listing_id"]) for L in group]          # a stated cap: the OM request is on the card, his drag sends it
            continue
        if not queue:
            for L in group:
                notes[str(L["listing_id"])] = "the eight-cap question is written but not queued (run without --no-queue to queue it)"
            continue
        try:
            msg = medical_request(cfg, group)                      # his approved wording: the cap question, or "which of them hold an eight"
        except ValueError:
            continue
        problems = validate_om_request(msg["subject"], msg["body"])
        if problems:
            log.warning("deck: %s skipped (%s)", em, "; ".join(problems))
            for L in group:
                notes[str(L["listing_id"])] = "the email did not pass the house rules (" + "; ".join(problems) + "): not queued"
            continue
        emails.append({"broker_email": em, "listings": group, **msg})
    return {"listings": Ls, "emails": emails, "notes": notes, "drag": drag}


def run_campaign(conn, cfg: Any, apify, asana, *, pull_first: bool = True, queue: bool = True, run_date: str | None = None, gmail=None,
                 email: bool = False, site: bool = False) -> dict[str, Any]:
    """Pull, board, queue (see the module doc). `asana` is the wrapped commercial client (working.wrap)."""
    from .asana_push import already_on_board, create_card
    from .blast import HEAVY, QUEUE_DIR
    run_date = run_date or today_et()
    res: dict[str, Any] = {"pull": pull(conn, cfg, apify, gmail, email) if pull_first else {"skipped": "not asked"}, "cards": 0, "already": 0, "queued": 0}
    p = plan(conn, cfg, queue)
    Ls = p["listings"]
    res.update(listings=len(Ls), with_cap=sum(L["lane"] == "cap" for L in Ls), drag=len(p["drag"]), not_emailed=len(p["notes"]))
    project = cfg.get("commercial.asana.deck_project")
    cards: dict[str, str] = {}
    if asana is not None and project and hasattr(asana, "deck_sections"):
        secs = asana.deck_sections()
        tag = asana.ensure_tag(cfg.get("commercial.tag", "1031-buyer")) if hasattr(asana, "ensure_tag") else None
        on_board = already_on_board(asana, cfg.get("commercial.asana.project"))      # the wrapped client reads every commercial board
        queued_to = {str(L["listing_id"]): e["broker_email"] for e in p["emails"] for L in e["listings"]}
        for L in reversed(Ls):                                    # the best one is posted last
            lid = str(L["listing_id"])
            gid = on_board.get(lid) or L.get("asana_task_id") or L.get("asana_om_task_id")
            if gid:
                cards[lid] = gid
                res["already"] += 1
                continue
            head = f"NEW DECK · {box_words(cfg)}: "
            if lid in queued_to:
                extra = head + (f"Charles's eight-cap question to {queued_to[lid]} is queued; it goes out at the next rounds inside the send window. "
                                "Nothing to drag.")
            elif lid in p["drag"]:
                extra = head + "the listing shows a cap rate. Drag the card to \"Approved · send OM request\" and Charles asks for the books."
            else:
                extra = head + p["notes"].get(lid, "not emailed") + "."
            try:
                made = create_card(asana.inner, project, secs["New"], cfg, L, with_email=lid in p["drag"], extra=extra)
            except Exception as e:  # noqa: BLE001
                log.error("deck: card for %s failed: %s", lid, e)
                res["errors"] = res.get("errors", 0) + 1
                continue
            gid = made.get("gid")
            cards[lid] = gid
            asana.placed(gid, project)
            if tag:
                try:
                    asana.tag_task(gid, tag)
                except Exception:  # noqa: BLE001
                    pass
            col = "asana_om_task_id" if lid in p["drag"] else "asana_task_id"
            conn.execute(f"UPDATE commercial_listings SET {col}=COALESCE({col}, ?), board_column=COALESCE(board_column,'New') WHERE listing_id=?", (gid, lid))
            res["cards"] += 1
    if queue:
        entries = []
        for e in p["emails"]:
            ids = [str(L["listing_id"]) for L in e["listings"]]
            slim = [{k: v for k, v in L.items() if k not in HEAVY and k != "raw"} for L in e["listings"]]
            entries.append({"id": hashlib.sha1((e["broker_email"] + "|" + ",".join(sorted(ids))).encode()).hexdigest()[:12], "campaign": "deck",
                            "priority": 1, "broker_email": e["broker_email"], "broker_name": e["listings"][0].get("broker_name"),
                            "brokerage": e["listings"][0].get("brokerage"), "listing_ids": ids,
                            "streets": [f"{L.get('street')}, {L.get('city')}" for L in e["listings"]],
                            "score": max(L.get("score") or 0 for L in e["listings"]), "subject": e["subject"], "body": e["body"],
                            "cards": {i: cards[i] for i in ids if cards.get(i)}, "listings": slim})
        entries.sort(key=lambda e: -e["score"])
        folder = cfg.root / cfg.get("commercial.outreach.queue_dir", QUEUE_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{run_date}-deck.json").write_text(json.dumps({"built_at": iso(now_utc()), "entries": entries}, ensure_ascii=False, indent=1, default=str))
        res["queued"] = len(entries)
    if site:
        from .site import publish
        res["site"] = {k: v for k, v in publish(conn, cfg, run_date).items() if k in ("uploaded", "deals", "skipped", "error")}
    conn.execute("INSERT OR REPLACE INTO commercial_kv (key, value) VALUES (?, ?)", (KV + run_date, json.dumps(res, default=str)))
    return res


def text(conn, cfg: Any, res: dict[str, Any], p: dict[str, Any]) -> str:
    """The run and the analysis in plain words (the CLI prints it)."""
    pl = res.get("pull") or {}
    Ls = p["listings"]
    lines = [f"NEW DECK · {box_words(cfg)} · 8 cap floor · Florida",
             "LoopNet pull: " + (f"{pl.get('rows')} listings read, about ${pl.get('cost_usd') or 0:.2f}" if pl.get("rows") is not None
                                 else "NOT done (" + str(pl.get("error") or pl.get("skipped")) + "); worked from the stored listings")]
    lines.append(f"{len(Ls)} fit the deck · {sum(L['lane'] == 'cap' for L in Ls)} show a cap of 8 or better · {sum(L['lane'] == 'no_cap' for L in Ls)} show no cap "
                 f"· {sum(L.get('tag') == 'CLEAN' for L in Ls)} CLEAN · {sum(L.get('tag') == 'SUSPECT' for L in Ls)} SUSPECT · "
                 f"{sum(L.get('detail_level') != 'full' for L in Ls)} still without their LoopNet sheet")
    by_asset = Counter(L.get("asset_class") or "?" for L in Ls)
    if by_asset:
        lines.append("By type: " + " · ".join(f"{k} {v}" for k, v in by_asset.most_common()))
    out = dropped(conn)
    if out:
        lines.append("Dropped: " + " · ".join(f"{k} {v}" for k, v in out))
    if "cards" in res:
        lines.append(f"Board: {res['cards']} new cards, {res['already']} already there")
    lines.append(f"Emails: {len(p['emails'])} brokers queued for the eight-cap question · {len(p['drag'])} cards carry an OM request to drag · "
                 f"{len(p['notes'])} listings not emailed")
    lines.append("")
    for L in Ls:
        why = p["notes"].get(str(L["listing_id"]))
        bs = f" (NOI/price {L['back_solved_cap']}%)" if L.get("back_solved_cap") else ""
        lines.append(f"  {L.get('score') or 0:>3} {L.get('tag') or '?':<8} {L.get('evidence_grade') or '?'} {(L.get('asset_class') or '?'):<11} "
                     f"{(str(L.get('stated_cap')) + '%') if L.get('stated_cap') else 'no cap':>7}{bs} · {money(L.get('price'))} · "
                     f"anchor {money(L['offer_anchor']) if L.get('offer_anchor') else 'none'} · {L.get('street')}, {L.get('city')} · {L.get('broker_name')}"
                     + (f" · NOT EMAILED: {why}" if why else ""))
        if L.get("tag_reasons"):
            lines.append("        " + "; ".join(str(r) for r in L["tag_reasons"]))
    for e in p["emails"]:
        lines.append(f"\n--- queued to {e['broker_email']} · {len(e['listings'])} listing(s) ---\nSubject: {e['subject']}\n{e['body']}")
    return "\n".join(lines)
