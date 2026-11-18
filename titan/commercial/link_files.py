"""Documents a broker LINKS instead of attaching are opened and read (Rodrigo, 2026-11-18: "I thought we already worked on being able to
log into links from the emails we get from agents, we got several emails and they gave us links"; Tammy Huston / Mariner Plaza sent a
Dropbox folder after the NDA and it sat unread).

Every check-replies / approvals round, after the broker's answers are mirrored: each document link in a broker's own words (not the
quoted history) is opened ONCE on the Mac (the cloud's network blocks these hosts):
  - Dropbox file or folder (dl=1: a folder comes back as one ZIP), Google Drive file, Google Docs / Sheets / Slides (exported),
    OneDrive / SharePoint share links, a direct .pdf / .xlsx / .csv / .docx URL; Outlook "safelinks" are unwrapped first.
  - every PDF / spreadsheet / document / text file inside is filed in data/listings/<listing_id>/ like an emailed attachment,
    the thread goes to BOOKS, and the card gets one "🔗 Opened the link" comment. The normal 🧠 read then sees the files; for an
    answer that was already read, a fresh read is posted (no_reply: never an email to the broker because of it).
  - a link that needs a login (data rooms: Revere, Buildout, Crexi, DocSend, Box, a Drive folder, a sign-in page) is NOT logged into:
    one "🔐" comment on the card says so (open it in your browser and drop the files on the card, card_files.py reads them).
Network errors are retried on the next round (max 3 tries). Nothing here sends an email, signs anything or clicks "accept".
"""
from __future__ import annotations

import base64
import io
import re
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

import requests

from .. import db
from ..util import iso, log, now_utc

LINK_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
TAKE_EXT = (".pdf", ".xlsx", ".xls", ".xlsm", ".csv", ".docx", ".txt")
MAX_BYTES = 60_000_000
MAX_TRIES = 3
# Data rooms and portals: always a login, never tried.
LOGIN_HOST_RE = re.compile(r"reverecre|buildout|crexi|docsend|dealroom|(?<!drop)box\.com|sharefile|rcm1|ten-x|loopnet|catylist|digitalsigning|"
                           r"docusign|authentisign|hellosign|dotloop|wetransfer\.com/(?!downloads)", re.I)
DOC_HINT_RE = re.compile(r"dropbox\.com|drive\.google|docs\.google|1drv\.ms|onedrive|sharepoint\.com|\.(?:pdf|xlsx?|xlsm|csv|docx)(?:$|\?)|"
                         + LOGIN_HOST_RE.pattern, re.I)
QUOTE_START_RE = re.compile(r"^(?:>|On .{5,120} wrote:|From: |-----Original Message|_{8,}|\*From:\*)", re.M)


def own_words(body: str) -> str:
    """The broker's new text: everything before the quoted history (">" lines, "On … wrote:", Outlook's "From:" header)."""
    m = QUOTE_START_RE.search(body or "")
    return (body or "")[: m.start()] if m else (body or "")


def unwrap(url: str) -> str:
    """Outlook safelinks / Google redirect → the real URL; trailing punctuation trimmed."""
    url = url.rstrip(".,;:!>")
    p = urlparse(url)
    if "safelinks.protection.outlook.com" in p.netloc or (p.netloc.endswith("google.com") and p.path == "/url"):
        q = parse_qs(p.query)
        real = (q.get("url") or q.get("q") or [None])[0]
        if real:
            return unquote(real)
    return url


def doc_urls(body: str) -> list[str]:
    """Document links in the broker's own words, unwrapped, deduplicated, in order."""
    out: list[str] = []
    for raw in LINK_RE.findall(own_words(body)):
        u = unwrap(raw)
        if DOC_HINT_RE.search(u) and u not in out:
            out.append(u)
    return out[:8]


def plan(url: str) -> tuple[str, str | None]:
    """(kind, direct download URL). kind 'login' = a portal we never log into (URL None); 'skip' = not a document."""
    p = urlparse(url)
    host = p.netloc.lower()
    if LOGIN_HOST_RE.search(url):
        return "login", None
    if "dropbox.com" in host:
        q = parse_qs(p.query, keep_blank_values=True)
        q["dl"] = ["1"]
        return "dropbox", urlunparse(p._replace(query=urlencode(q, doseq=True)))
    if host == "drive.google.com":
        m = re.search(r"/file/d/([\w-]+)", p.path) or re.search(r"[?&]id=([\w-]+)", url)
        if "/folders/" in p.path or not m:
            return "login", None            # a Drive folder needs the Drive API (or his browser)
        return "drive", f"https://drive.usercontent.google.com/download?id={m.group(1)}&export=download&confirm=t"
    if host == "docs.google.com":
        m = re.search(r"/(document|spreadsheets|presentation)/d/([\w-]+)", p.path)
        if not m:
            return "skip", None
        fmt = {"document": "docx", "spreadsheets": "xlsx", "presentation": "pdf"}[m.group(1)]
        return "gdocs", f"https://docs.google.com/{m.group(1)}/d/{m.group(2)}/export?format={fmt}"
    if host == "1drv.ms" or "onedrive.live.com" in host:
        token = "u!" + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
        return "onedrive", f"https://api.onedrive.com/v1.0/shares/{token}/root/content"
    if host.endswith("sharepoint.com"):
        q = parse_qs(p.query, keep_blank_values=True)
        q["download"] = ["1"]
        return "sharepoint", urlunparse(p._replace(query=urlencode(q, doseq=True)))
    if re.search(r"\.(?:pdf|xlsx?|xlsm|csv|docx)$", p.path, re.I):
        return "direct", url
    return "skip", None


def _name_from(resp: requests.Response, fallback: str) -> str:
    cd = resp.headers.get("Content-Disposition") or ""
    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
    name = unquote(m.group(1).strip().strip('"')) if m else fallback
    return re.sub(r"[^\w.\- ()]+", "_", name)[:120] or "document"


def _unzip(data: bytes) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            base = Path(info.filename).name
            if info.is_dir() or not base or base.startswith(".") or "__MACOSX" in info.filename or info.file_size > MAX_BYTES:
                continue
            if Path(base).suffix.lower() in TAKE_EXT:
                out.append((base, z.read(info)))
            if len(out) >= 60:
                break
    return out


def fetch(url: str, session: requests.Session | None = None, timeout: int = 60) -> dict[str, Any]:
    """Open one link. {status: ok|login|skip|error, files: [(name, bytes)], note}. Never submits a form or a password."""
    kind, direct = plan(url)
    if kind in ("login", "skip"):
        return {"status": kind, "files": [], "note": "needs a login" if kind == "login" else "not a document"}
    s = session or requests.Session()
    try:
        r = s.get(direct, timeout=timeout, stream=True, allow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        data = b""
        for chunk in r.iter_content(262144):
            data += chunk
            if len(data) > MAX_BYTES:
                return {"status": "error", "files": [], "note": f"over {MAX_BYTES // 1_000_000} MB", "final": True}
    except requests.RequestException as e:
        return {"status": "error", "files": [], "note": type(e).__name__}
    ctype = (r.headers.get("Content-Type") or "").lower()
    if r.status_code in (401, 403) or "text/html" in ctype or data[:15].lstrip().lower().startswith((b"<!doctype html", b"<html")):
        return {"status": "login", "files": [], "note": f"asked for a login (HTTP {r.status_code})"}
    if r.status_code >= 400:
        return {"status": "error", "files": [], "note": f"HTTP {r.status_code}", "final": r.status_code == 404}
    tail = Path(urlparse(url).path).name or "document"
    name = _name_from(r, tail)
    if data[:4] == b"PK\x03\x04" and (name.lower().endswith(".zip") or kind == "dropbox" and not Path(name).suffix.lower() in TAKE_EXT):
        try:
            files = _unzip(data)
        except zipfile.BadZipFile:
            return {"status": "error", "files": [], "note": "broken ZIP", "final": True}
        return {"status": "ok" if files else "skip", "files": files, "note": "" if files else "no PDF / spreadsheet / document inside"}
    if not Path(name).suffix:
        name += {"gdocs": "." + (parse_qs(urlparse(direct).query).get("format") or ["pdf"])[0]}.get(kind, ".pdf" if data[:4] == b"%PDF" else "")
    if Path(name).suffix.lower() not in TAKE_EXT:
        return {"status": "skip", "files": [], "note": f"not a document I read ({Path(name).suffix or 'no type'})"}
    return {"status": "ok", "files": [(name, data)], "note": ""}


def _table(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS commercial_link_files (url TEXT, thread_id INTEGER, listing_id TEXT, gmail_message_id TEXT,
                    status TEXT, tries INTEGER DEFAULT 0, files TEXT, note TEXT, created_at TEXT, updated_at TEXT, PRIMARY KEY (url, listing_id))""")


def opened_urls(conn) -> set[str]:
    """Links whose files are on file (books_review stops calling them 'linked, not opened')."""
    try:
        _table(conn)
        return {r[0] for r in conn.execute("SELECT url FROM commercial_link_files WHERE status='ok'").fetchall()}
    except Exception:  # noqa: BLE001
        return set()


def pull_link_files(conn, cfg: Any, asana=None, llm=None, now=None, session: requests.Session | None = None,
                    limit: int | None = None) -> dict[str, Any]:
    """Open every new document link brokers sent in an OM thread, file what is inside, read it. Runs every round (Mac)."""
    from .board import ensure_board
    from .books_review import review_thread
    from .outreach import regrade
    _table(conn)
    now = now or now_utc()
    limit = limit or int(cfg.get("commercial.links.per_round", 15))
    res: dict[str, Any] = {"links": 0, "opened": 0, "files": 0, "login": 0, "errors": 0, "reads": 0}
    doc_dir = cfg.root / cfg.get("commercial.documents.dir", "data/listings")
    rows = conn.execute("""SELECT m.gmail_message_id, m.body, m.created_at, t.* FROM commercial_messages m JOIN commercial_threads t ON t.id=m.thread_id
                           WHERE t.bounced=0 ORDER BY m.created_at DESC LIMIT 400""").fetchall()
    secs = None
    done_threads: set[int] = set()
    for row in rows:
        r = db.row_to_dict(row)
        if res["links"] >= limit:
            break
        for url in doc_urls(r.get("body") or ""):
            seen = conn.execute("SELECT status, tries FROM commercial_link_files WHERE url=? AND listing_id=?", (url, r["listing_id"])).fetchone()
            if seen and (seen[0] in ("ok", "login", "skip", "dead") or (seen[1] or 0) >= MAX_TRIES):
                continue
            res["links"] += 1
            got = fetch(url, session=session)
            status = "dead" if got["status"] == "error" and got.get("final") else got["status"]
            saved: list[str] = []
            if got["status"] == "ok":
                folder = doc_dir / str(r["listing_id"])
                folder.mkdir(parents=True, exist_ok=True)
                for name, blob in got["files"]:
                    path = folder / name
                    if path.exists() and path.read_bytes() != blob:
                        path = folder / f"{path.stem} (link){path.suffix}"
                    path.write_bytes(blob)
                    saved.append(path.name)
                res["opened"] += 1
                res["files"] += len(saved)
            elif got["status"] == "login":
                res["login"] += 1
            elif got["status"] == "error":
                res["errors"] += 1
            conn.execute("""INSERT INTO commercial_link_files (url, thread_id, listing_id, gmail_message_id, status, tries, files, note, created_at, updated_at)
                            VALUES (?,?,?,?,?,1,?,?,?,?) ON CONFLICT(url, listing_id) DO UPDATE SET status=excluded.status, tries=tries+1,
                            files=excluded.files, note=excluded.note, updated_at=excluded.updated_at""",
                         (url, r["id"], r["listing_id"], r["gmail_message_id"], status, "; ".join(saved), got.get("note") or "", iso(now), iso(now)))
            who = r.get("broker_name") or r.get("broker_email") or "The broker"
            host = urlparse(url).netloc
            if asana is None or not r.get("card_gid"):
                continue
            if saved:
                shown = saved[:10] + ([f"… {len(saved) - 10} more"] if len(saved) > 10 else [])
                asana.comment(r["card_gid"], f"🔗 Opened the {host} link {who} sent and filed {len(saved)} file(s) under "
                                             f"data/listings/{r['listing_id']}/: " + ", ".join(shown) + ". Reading them now.")
                conn.execute("UPDATE commercial_threads SET state='BOOKS', updated_at=? WHERE id=? AND state NOT IN ('CLOSED','BOUNCED')",
                             (iso(now), r["id"]))
                done_threads.add(r["id"])
            elif status == "login":
                asana.comment(r["card_gid"], f"🔐 {who} sent a {host} link that needs a login ({got.get('note')}). I don't log into "
                                             f"portals: open it in your browser, download the files and drop them on this card; I read them "
                                             f"from there.\n{url}")
            elif status in ("dead", "skip") or (status == "error" and (seen[1] if seen else 0) + 1 >= MAX_TRIES):
                asana.comment(r["card_gid"], f"🔗 Couldn't open the {host} link {who} sent ({got.get('note')}). Open it yourself and drop "
                                             f"the files on this card if it matters.\n{url}")
    for tid in done_threads:         # files are in: regrade, and post a fresh read if this answer was already read without them
        t = db.row_to_dict(conn.execute("""SELECT t.*, (SELECT gmail_message_id FROM commercial_messages WHERE thread_id=t.id ORDER BY created_at DESC, id DESC LIMIT 1)
                                           AS last_msg_id FROM commercial_threads t WHERE t.id=?""", (tid,)).fetchone())
        try:
            regrade(conn, cfg, t["listing_id"])
        except Exception as e:  # noqa: BLE001
            log.warning("regrade after link files %s: %s", t["listing_id"], e)
        read = conn.execute("SELECT 1 FROM commercial_reviews WHERE thread_id=? AND gmail_message_id=?", (tid, t.get("last_msg_id"))).fetchone()
        if read and asana is not None:
            try:
                if secs is None:
                    secs = ensure_board(asana, cfg.get("commercial.asana.project"))
                review_thread(conn, cfg, llm, t, asana, secs, no_reply=True)
                res["reads"] += 1
            except Exception as e:  # noqa: BLE001
                res["errors"] += 1
                log.exception("read after link files %s: %s", t["listing_id"], e)
    return res
