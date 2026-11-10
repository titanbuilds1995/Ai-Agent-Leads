"""Financials Rodrigo drops on a 1031 card are read like the ones a broker emails (Rodrigo, 2026-11-10: "once I get all of the info
from the finances on the deals … should I drop them on the Asana card … yes let's do that, I'd rather put it there").

Every approvals round: the cards on the 1031 board that changed since the last look are checked for attachments; each PDF or
spreadsheet not seen before is downloaded into data/listings/<listing_id>/ (the same folder the broker's emailed files go to), the
listing is regraded off the books, and a fresh 🧠 READ FOR YOU is posted on the card (with the true-8-cap price, pros, cons, doubts
and what checks out). A read triggered by his own files never drafts an email to the broker. Images and other files are left alone."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .. import db
from ..util import iso, log, now_utc
from .board import LISTING_URL_RE

TAKE_EXT = (".pdf", ".xlsx", ".xls", ".xlsm", ".csv", ".docx", ".txt")
MAX_BYTES = 40_000_000


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w.\- ()]+", "_", (name or "file").strip())[:120]
    return name or "file"


def _card_map(conn) -> dict[str, str]:
    """card gid → listing id, from what the engine already knows (no Asana call)."""
    out: dict[str, str] = {}
    for gid, lid in conn.execute("SELECT card_gid, listing_id FROM commercial_threads WHERE card_gid IS NOT NULL"):
        out[gid] = lid
    for a, b, lid in conn.execute("SELECT asana_task_id, asana_om_task_id, listing_id FROM commercial_listings"):
        for gid in (a, b):
            if gid:
                out.setdefault(gid, lid)
    return out


def pull_card_files(conn, cfg: Any, asana, llm=None, now=None) -> dict[str, Any]:
    from ..asana import AsanaError
    from .books_review import review_thread
    from .board import ensure_board
    from .outreach import regrade, verdict_text
    res: dict[str, Any] = {"cards": 0, "files": 0, "reads": 0, "errors": 0}
    project = cfg.get("commercial.asana.project")
    if asana is None or not project:
        return res
    now = now or now_utc()
    row = conn.execute("SELECT value FROM commercial_kv WHERE key='card_files_seen'").fetchone()
    since = row[0] if row else ""
    known = _card_map(conn)
    doc_dir = cfg.root / cfg.get("commercial.documents.dir", "data/listings")
    started = iso(now)
    secs = None
    for card in asana.project_tasks(project):
        if card.get("completed") or (since and (card.get("modified_at") or "") <= since):
            continue
        gid = card["gid"]
        try:
            atts = [a for a in asana.attachments(gid) if Path(a.get("name") or "").suffix.lower() in TAKE_EXT]
        except (AsanaError, Exception) as e:  # noqa: BLE001
            res["errors"] += 1
            log.warning("attachments on %s: %s", gid, e)
            continue
        new = [a for a in atts if not conn.execute("SELECT 1 FROM commercial_card_files WHERE attachment_gid=?", (a["gid"],)).fetchone()]
        if not new:
            continue
        res["cards"] += 1
        lid = known.get(gid)
        if not lid:
            m = LISTING_URL_RE.search((asana.task(gid) or {}).get("notes") or "")
            lid = m.group(1) if m else None
        if not lid:
            asana.comment(gid, "📎 Got the file(s), but this card has no LoopNet link, so I can't tell which deal they belong to.")
            for a in new:
                conn.execute("INSERT OR IGNORE INTO commercial_card_files (attachment_gid, card_gid, name, created_at) VALUES (?,?,?,?)",
                             (a["gid"], gid, a.get("name"), iso(now)))
            continue
        folder = doc_dir / str(lid)
        folder.mkdir(parents=True, exist_ok=True)
        got = []
        for a in new:
            try:
                if int(a.get("size") or 0) > MAX_BYTES:
                    raise AsanaError(f"too big ({int(a['size']) // 1_000_000} MB)")
                if not a.get("download_url"):
                    raise AsanaError("no download link (a file linked from Dropbox / Drive is not attached)")
                data = asana.download(a["download_url"])
                path = folder / _safe_name(a.get("name"))
                if path.exists() and path.read_bytes() != data:
                    path = folder / f"{path.stem} ({a['gid'][-5:]}){path.suffix}"
                path.write_bytes(data)
                conn.execute("INSERT OR IGNORE INTO commercial_card_files (attachment_gid, listing_id, card_gid, name, path, created_at) VALUES (?,?,?,?,?,?)",
                             (a["gid"], lid, gid, a.get("name"), str(path), iso(now)))
                got.append(path.name)
                res["files"] += 1
            except Exception as e:  # noqa: BLE001
                res["errors"] += 1
                log.warning("download %s on %s: %s", a.get("name"), gid, e)
                asana.comment(gid, f"📎 Couldn't read {a.get('name')}: {str(e)[:160]}.")
                conn.execute("INSERT OR IGNORE INTO commercial_card_files (attachment_gid, listing_id, card_gid, name, created_at) VALUES (?,?,?,?,?)",
                             (a["gid"], lid, gid, a.get("name"), iso(now)))
        if not got:
            continue
        t = conn.execute("""SELECT t.*, (SELECT gmail_message_id FROM commercial_messages WHERE thread_id=t.id ORDER BY created_at DESC, id DESC LIMIT 1)
                            AS last_msg_id FROM commercial_threads t WHERE t.listing_id=? AND t.card_gid IS NOT NULL ORDER BY t.id DESC LIMIT 1""",
                         (lid,)).fetchone()
        if t:
            if secs is None:
                secs = ensure_board(asana, project)
            t = db.row_to_dict(t)
            t["card_gid"] = gid
            asana.comment(gid, "📎 Filed your file(s) under data/listings/" + str(lid) + "/: " + ", ".join(got) + ". Reading them now.")
            try:
                review_thread(conn, cfg, llm, t, asana, secs, no_reply=True)
                res["reads"] += 1
            except Exception as e:  # noqa: BLE001
                res["errors"] += 1
                log.exception("read after card files %s: %s", lid, e)
        else:        # no broker conversation yet: regrade off the books and say what they show
            try:
                v = regrade(conn, cfg, lid)
            except Exception as e:  # noqa: BLE001
                v = None
                log.warning("regrade %s: %s", lid, e)
            asana.comment(gid, "📎 Filed your file(s) under data/listings/" + str(lid) + "/: " + ", ".join(got) + "."
                          + (("\n\n" + verdict_text(v)) if v else "") + "\nAsk a question on this card for Claude's full read.")
    conn.execute("INSERT INTO commercial_kv (key, value) VALUES ('card_files_seen', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (started,))
    return res
