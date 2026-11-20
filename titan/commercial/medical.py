"""The medical campaign (Rodrigo, 2026-11-20: "a campaign where we focus on medical buildings in Florida … that fit the criteria that
we have, and ideally are triple net or can reach our 8 cap … build a new board on Asana with just medical buildings and email all
those medical agents today, see if we can get finances").

One run (`make medical`, or the approvals round on the date in `commercial.medical.run_on`):
  1. PULL  LoopNet's medical-office search for Florida through Apify (one family, about a dollar; never marks anything gone). When
           the pull fails the run goes on with the listings already stored.
  2. PICK  every stored listing that is in the buyer box, still active and MEDICAL: LoopNet's own type (medical office, dental,
           surgery, urgent care, dialysis, veterinary …) or a net-leased building whose name / type / description says so.
  3. BOARD "Titan 1031 · Medical" (`commercial.asana.medical_project`), same columns as the 1031 board and read by the engine as
           part of it (working.py): an open card of a medical listing on the 1031 board is moved over in its column, every other
           listing gets a card in New. Cards on Working Deals and in Not a fit / Closed stay where he put them.
  4. EMAIL one request for the financials per broker, naming all of their medical listings, into the commercial queue with
           priority (the approvals rounds send it from Charles@ at the commercial pace, inside the send window). Never to a broker
           Charles already emailed about anything, a bounced or do-not-contact address. When it goes out the card gets the 📤
           comment and moves to "Sent · no answer yet"; the answer, the books and the read land on that card like any 1031 card."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .. import db
from ..util import iso, log, now_utc, today_et
from .board import NOT_A_FIT, column_key
from .om_request import medical_request, validate_om_request

# what LoopNet calls it (type / subtype / the listing's own name): any of these is medical
LABEL_RE = re.compile(r"medical|dental|dentist|surg|clinic|urgent care|dialysis|veterinar|animal hospital|health ?care|physician|"
                      r"\bMOB\b|hospital|outpatient|imaging|radiolog|dermatolog|orthop|pediatric|cardiolog|oncolog|\bDaVita\b|Fresenius", re.I)
# in the description only a phrase that says what the BUILDING is counts ("two blocks from the hospital" does not)
TEXT_RE = re.compile(r"medical (?:office|building|center|plaza|condo|campus|facility|tenant|use|practice)|dental (?:office|practice|building|clinic)|"
                     r"surgery center|surgical center|urgent care|dialysis|veterinar|animal hospital|physician(?:s)? (?:office|practice|group)|"
                     r"\bDaVita\b|Fresenius|outpatient (?:clinic|center|facility)|imaging center", re.I)
NNN_RE = re.compile(r"\bnnn\b|triple[- ]net|net[- ]leased?|absolute net", re.I)
KV = "medical_campaign:"
STAY = (NOT_A_FIT, "Closed")          # an open card he put there is not carried over


def is_medical(L: dict[str, Any]) -> bool:
    if (L.get("asset_class") or "") == "medical":
        return True
    label = " ".join(str(L.get(k) or "") for k in ("property_type", "property_subtype", "name"))
    if LABEL_RE.search(label):
        return True
    tenants = L.get("tenant_names") or L.get("tenants") or ""
    text = " ".join([str(L.get("description") or ""), " ".join(tenants) if isinstance(tenants, list) else str(tenants),
                     " ".join(str(h) for h in (L.get("highlights") or [])) if isinstance(L.get("highlights"), list) else str(L.get("highlights") or "")])
    return bool(TEXT_RE.search(text))


def is_nnn(L: dict[str, Any]) -> bool:
    return (L.get("asset_class") == "nnn" or bool(NNN_RE.search(" ".join(str(L.get(k) or "") for k in ("lease_type", "description", "name")))))


def listings(conn) -> list[dict[str, Any]]:
    """In the box, active, medical; triple net first, then a stated cap, then the engine's score."""
    out = []
    for r in conn.execute("SELECT * FROM commercial_listings WHERE in_box=1 AND gone_at IS NULL AND COALESCE(status,'active')='active'").fetchall():
        r = db.row_to_dict(r)
        try:
            L = json.loads(r.get("data") or "{}")
        except json.JSONDecodeError:
            L = {}
        for k, v in r.items():
            if k not in ("data", "raw") and v not in (None, "") and L.get(k) in (None, ""):
                L[k] = v
        for k in ("listing_id", "score", "tag", "evidence_grade", "broker_email", "broker_name", "broker_phone", "brokerage", "price", "stated_cap",
                  "asana_task_id", "asana_om_task_id", "board_column"):
            L[k] = r.get(k)
        for k in ("tag_reasons", "docs_missing", "highlights", "motivation", "credit_tenants", "documents"):
            if isinstance(L.get(k), str):
                try:
                    L[k] = json.loads(L[k])
                except json.JSONDecodeError:
                    pass
        if is_medical(L):
            L["medical_nnn"] = is_nnn(L)
            out.append(L)
    out.sort(key=lambda L: (not L["medical_nnn"], L.get("stated_cap") is None, -(L.get("score") or 0)))
    return out


def pull(conn, cfg: Any, apify) -> dict[str, Any]:
    """LoopNet's medical search only. A failed or empty pull is reported, never fatal: the stored listings are still worked."""
    if apify is None:
        return {"skipped": "no Apify client (APIFY_TOKEN missing)"}
    from .run import run
    try:
        out = run(conn, cfg, apify, asana=None, gmail=None, families=["medical"], push_asana=False, email=False, site=False, full_pull=False)
    except Exception as e:  # noqa: BLE001
        log.exception("medical pull failed: %s", e)
        return {"error": f"{type(e).__name__}: {e}"[:200]}
    if out.get("error"):
        return {"error": out["error"]}
    return {"rows": (out.get("counts") or {}).get("total"), "in_box": (out.get("counts") or {}).get("in_box"), "cost_usd": (out.get("cost") or {}).get("total_usd")}


def _emailed(conn) -> dict[str, str]:
    """{broker email: when Charles first wrote them} for every commercial thread."""
    return {r[0]: (r[1] or "")[:10] for r in conn.execute(
        "SELECT LOWER(broker_email), MIN(sent_at) FROM commercial_threads WHERE broker_email IS NOT NULL GROUP BY LOWER(broker_email)").fetchall()}


def _skip_reason(conn, em: str, emailed: dict[str, str]) -> str | None:
    if not em:
        return "no email on the listing: call the broker"
    if em in emailed:
        return f"Charles already emailed this broker on {emailed[em]}: not emailed again (the follow-up and the answers run on that conversation)"
    if db.is_dnc(conn, email=em):
        return "this broker asked us to stop: not emailed"
    return None


def plan(conn, cfg: Any) -> dict[str, Any]:
    """What a run would do, touching nothing: the listings, one email per broker, and why a broker gets none."""
    Ls = listings(conn)
    emailed = _emailed(conn)
    by: dict[str, list[dict[str, Any]]] = {}
    notes: dict[str, str] = {}
    for L in Ls:
        em = (L.get("broker_email") or "").strip().lower()
        why = _skip_reason(conn, em, emailed)
        if why:
            notes[str(L["listing_id"])] = why
            continue
        by.setdefault(em, []).append(L)
    emails = []
    for em, group in by.items():
        try:
            msg = medical_request(cfg, group)
        except ValueError:
            continue
        problems = validate_om_request(msg["subject"], msg["body"])
        if problems:
            log.warning("medical: %s skipped (%s)", em, "; ".join(problems))
            for L in group:
                notes[str(L["listing_id"])] = "the request did not pass the house rules (" + "; ".join(problems) + "): not queued"
            continue
        emails.append({"broker_email": em, "listings": group, **msg})
    return {"listings": Ls, "emails": emails, "notes": notes}


def _card_column(asana, gid: str, main: str) -> tuple[str | None, bool, bool]:
    """(column on the 1031 board, is the card open, is it on the 1031 board)."""
    try:
        t = asana.task(gid, fields="completed,memberships.project.gid,memberships.section.name") or {}
    except Exception as e:  # noqa: BLE001
        log.warning("medical: could not read card %s: %s", gid, e)
        return None, False, False
    for m in t.get("memberships") or []:
        if str((m.get("project") or {}).get("gid")) == str(main):
            return column_key((m.get("section") or {}).get("name")) or "New", not t.get("completed"), True
    return None, not t.get("completed"), False


def run_campaign(conn, cfg: Any, apify, asana, *, pull_first: bool = True, run_date: str | None = None) -> dict[str, Any]:
    """Pull, board, queue (see the module doc). `asana` is the wrapped commercial client (working.wrap)."""
    from .asana_push import already_on_board, create_card
    from .blast import HEAVY, QUEUE_DIR
    run_date = run_date or today_et()
    res: dict[str, Any] = {"pull": pull(conn, cfg, apify) if pull_first else {"skipped": "not asked"}, "cards": 0, "moved": 0, "already": 0,
                           "queued": 0, "not_emailed": 0}
    p = plan(conn, cfg)
    res["listings"], res["nnn"] = len(p["listings"]), sum(L["medical_nnn"] for L in p["listings"])
    res["with_cap"] = sum(L.get("stated_cap") is not None for L in p["listings"])
    res["not_emailed"] = len(p["notes"])
    main, project = cfg.get("commercial.asana.project"), cfg.get("commercial.asana.medical_project")
    cards: dict[str, str] = {}
    if asana is not None and project and hasattr(asana, "medical_sections"):
        secs = asana.medical_sections()
        tag = asana.ensure_tag(cfg.get("commercial.tag", "1031-buyer")) if hasattr(asana, "ensure_tag") else None
        on_board = already_on_board(asana, main)                 # the wrapped client reads all three boards
        queued_to = {str(L["listing_id"]): e["broker_email"] for e in p["emails"] for L in e["listings"]}
        for L in p["listings"]:
            lid = str(L["listing_id"])
            gid = on_board.get(lid) or L.get("asana_task_id") or L.get("asana_om_task_id")
            if gid:
                cards[lid] = gid
                if asana.on_medical(gid) or asana.on_working(gid):
                    res["already"] += 1
                    continue
                col, is_open, on_main = _card_column(asana, gid, main)
                if not is_open or not on_main or col in STAY or not secs.get(col or ""):
                    res["already"] += 1                          # cleared, his Not a fit / Closed, or not on the 1031 board: left alone
                    continue
                try:
                    asana.inner.move(gid, secs[col])             # same column on the Medical board …
                    asana.inner.remove_project(gid, main)        # … and off the 1031 board: one card, one place
                    asana.placed(gid, project)
                    res["moved"] += 1
                except Exception as e:  # noqa: BLE001
                    log.warning("medical: could not move card %s: %s", gid, e)
                continue
            if lid in queued_to:
                extra = (f"MEDICAL CAMPAIGN: Charles's request for the financials to {queued_to[lid]} is queued; it goes out at the next rounds "
                         "inside the send window. Nothing to drag.")
            else:
                extra = "MEDICAL CAMPAIGN: " + p["notes"].get(lid, "not emailed") + "."
            try:
                made = create_card(asana.inner, project, secs["New"], cfg, L, with_email=False, extra=extra)
            except Exception as e:  # noqa: BLE001
                log.error("medical: card for %s failed: %s", lid, e)
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
            conn.execute("UPDATE commercial_listings SET asana_task_id=COALESCE(asana_task_id, ?), board_column=COALESCE(board_column,'New') WHERE listing_id=?",
                         (gid, lid))
            res["cards"] += 1
    entries = []
    for e in p["emails"]:
        ids = [str(L["listing_id"]) for L in e["listings"]]
        slim = [{k: v for k, v in L.items() if k not in HEAVY and k != "raw"} for L in e["listings"]]
        entries.append({"id": hashlib.sha1((e["broker_email"] + "|" + ",".join(sorted(ids))).encode()).hexdigest()[:12], "campaign": "medical",
                        "priority": 1, "broker_email": e["broker_email"], "broker_name": e["listings"][0].get("broker_name"),
                        "brokerage": e["listings"][0].get("brokerage"), "listing_ids": ids,
                        "streets": [f"{L.get('street')}, {L.get('city')}" for L in e["listings"]],
                        "score": max(L.get("score") or 0 for L in e["listings"]), "subject": e["subject"], "body": e["body"],
                        "cards": {i: cards[i] for i in ids if cards.get(i)}, "listings": slim})
    entries.sort(key=lambda e: -e["score"])
    folder = cfg.root / cfg.get("commercial.outreach.queue_dir", QUEUE_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{run_date}-medical.json").write_text(json.dumps({"built_at": iso(now_utc()), "entries": entries}, ensure_ascii=False, indent=1, default=str))
    res["queued"] = len(entries)
    conn.execute("INSERT OR REPLACE INTO commercial_kv (key, value) VALUES (?, ?)", (KV + run_date, json.dumps({k: v for k, v in res.items()}, default=str)))
    return res


def due(conn, cfg: Any) -> bool:
    """The approvals round runs the campaign once on the date in `commercial.medical.run_on` (a claim is written first, so two
    overlapping rounds never both run it)."""
    day = str(cfg.get("commercial.medical.run_on") or "")
    if not cfg.get("commercial.asana.medical_project") or day != today_et():
        return False
    return not conn.execute("SELECT 1 FROM commercial_kv WHERE key=?", (KV + day,)).fetchone()


def run_if_due(conn, cfg: Any, apify, asana) -> dict[str, Any]:
    if not due(conn, cfg):
        return {"skipped": "not due"}
    conn.execute("INSERT OR REPLACE INTO commercial_kv (key, value) VALUES (?, ?)", (KV + today_et(), json.dumps({"started": iso(now_utc())})))
    conn.commit()
    return run_campaign(conn, cfg, apify, asana)


def text(res: dict[str, Any], p: dict[str, Any] | None = None) -> str:
    """The run in plain words (the CLI prints it)."""
    pl = res.get("pull") or {}
    lines = ["LoopNet medical pull: " + (f"{pl.get('rows')} listings read, about ${pl.get('cost_usd') or 0:.2f}" if pl.get("rows") is not None
                                          else "NOT done (" + str(pl.get("error") or pl.get("skipped")) + "); worked from the stored listings")]
    if "listings" in res:
        lines.append(f"{res['listings']} medical listings in the box · {res['nnn']} triple net · {res['with_cap']} show a cap rate")
        lines.append(f"Medical board: {res['cards']} new cards, {res['moved']} moved over from the 1031 board, {res['already']} left where they are")
        lines.append(f"Emails: {res['queued']} brokers queued · {res['not_emailed']} listings not emailed (already emailed / no email / asked us to stop)")
    for e in (p or {}).get("emails", []):
        lines.append(f"\n--- to {e['broker_email']} · {len(e['listings'])} listing(s) ---\nSubject: {e['subject']}\n{e['body']}")
    for L in (p or {}).get("listings", []):
        why = (p or {}).get("notes", {}).get(str(L["listing_id"]))
        lines.append(f"  {'NNN ' if L['medical_nnn'] else '    '}{(str(L.get('stated_cap')) + '%') if L.get('stated_cap') else 'no cap':>7} · ${(L.get('price') or 0):,} · "
                     f"{L.get('street')}, {L.get('city')} · {L.get('broker_name')}" + (f" · NOT EMAILED: {why}" if why else ""))
    return "\n".join(lines)
