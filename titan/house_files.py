"""Files on a HOUSE lead are read (Rodrigo, 2026-11-16, Celia Ruiz / 5341 NW 180th Ter: the agent attached a lien search and a title
search, twice; the engine logged the words "Please find attached" and nothing else, then answered his question about the files without
them. "Why are we not able to read the attachments? Let's take a closer look at that.").

Until today only the 1031 pipeline read files (a broker's books from Gmail, what Rodrigo drops on a 1031 card). The house pipeline
read email TEXT only, and never looked at a card's attachments. Now, on every check-replies and approvals round:

  * an agent's email attachment (PDF, spreadsheet, Word, text, screenshot, ZIP) is downloaded with the reply and filed under
    data/houses/<outreach_id>/ (`file_gmail_attachments`, inside `check_replies`);
  * a file Rodrigo drops on a lead card (Email Approvals or Warm Leads board) is downloaded once (`pull_card_files`, approvals
    stage `house_files`, before his comments are read, so a question asked with the file gets answered with it);
  * the text is kept in `house_files` and goes into every card answer (`card_chat.context`), every drafted reply
    (`replies.writer`) and the second reader's case, as `files_on_this_lead`;
  * when Claude is reachable, one "🧠 READ FOR YOU" comment goes on the lead card: what each file is, what stays with the house
    (liens, mortgages, violations, open permits, judgments, who must sign), the amounts, what has to happen before closing, and
    what it means for our number. Internal, never sent to the agent: nothing here drafts or sends an email.

The engine's own pictures of the email (email_<id>.png) are never read. `make house-files` runs the card sweep by hand."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import db
from .commercial.card_files import IMAGE_TYPES, MAX_BYTES, TAKE_EXT, _safe_name, transcribe, unzip
from .commercial.documents import doc_text
from .util import iso, log, now_utc

BOT = "🤖"
READ_PREFIX = "🧠 READ FOR YOU"
ENGINE_PIC_RE = re.compile(r"^email_\d+(_v\d+)?\.png$", re.I)
MAX_TEXT = 60_000          # characters of a file kept in the database
PROMPT_TEXT = 14_000       # characters of each file shown to Claude per prompt
SEEN_KEY = "house_files_seen"

READ_SYSTEM = """You read documents a listing agent or Rodrigo attached to a single-family house lead for Titan Realty Investments
(a South Florida wholesaler; Rodrigo decides every number). Typical files: a municipal lien search, a title search / title
commitment, a survey, an inspection report, an estoppel, a seller's disclosure, a permit history, a payoff letter.
Write the read in plain English for Rodrigo (not a developer, not a lawyer), as short bold-headed sections, from THESE files only;
never invent an amount or a fact that is not in them, say "not in the files" where it is missing.
Sections, in this order, each a short list:
WHAT THE FILES ARE (each file: what it is, who issued it, the date it speaks as of, the property it covers);
WHO HAS TO SIGN (how title is vested, every owner, anything like divorce / estate / trust / power of attorney);
WHAT STAYS WITH THE HOUSE (every mortgage / HELOC / lien / judgment / code lien / open or expired permit / violation / unpaid
utility, with the holder, the recorded amount, the date, and whether it is paid off at closing from the seller's money or
survives the sale; code-enforcement liens and open violations run with the land);
WHAT HAS TO HAPPEN BEFORE CLOSING (the title company's requirements, in order, and who normally does each);
THE MONEY (a table-like list: total recorded encumbrances, what is unknown and must be asked for, the seller's rough net at
asking so we know whether the seller CAN close);
WHAT IT MEANS FOR OUR NUMBER (what to subtract, what to ask the agent, the one or two facts that change the price most; no offer
figure, Rodrigo sets it);
RED FLAGS (anything that kills or delays the deal: unsafe-structure orders, demolition orders, unpermitted square footage the
value leans on, a prior contract that fell through, lis pendens, probate).
Also return next_questions: up to 5 short questions Charles could ask the agent, each about something the files do not answer.
Also return cure, the legal-square-footage math the valuation needs, ONLY from facts in the files (0 or omit when the files do not say):
liens_total = every recorded code-enforcement lien / fine / judgment / unpaid utility that follows the house, in dollars;
demo_sqft = square feet of structures a recorded order says must be demolished; legalize_sqft = square feet that stays but needs
after-the-fact permits; legal_sqft = the living square footage a buyer can legally sell once that is done (the appraiser's living
area minus what must come down), and note = one line on where those numbers come from."""
CURE_SCHEMA = {"type": "object", "properties": {"liens_total": {"type": "number"}, "demo_sqft": {"type": "number"}, "legalize_sqft": {"type": "number"},
                                                "legal_sqft": {"type": "number"}, "note": {"type": "string"}},
               "required": ["liens_total", "demo_sqft", "legalize_sqft", "legal_sqft", "note"], "additionalProperties": False}
READ_SCHEMA = {"type": "object", "properties": {"read": {"type": "string"}, "next_questions": {"type": "array", "items": {"type": "string"}},
                                                "cure": CURE_SCHEMA},
               "required": ["read", "next_questions", "cure"], "additionalProperties": False}


def folder_for(cfg: Any, outreach_id: int | None, agent_id: int | None = None) -> Path:
    base = cfg.root / (cfg.get("outreach.files_dir", "data/houses") if hasattr(cfg, "get") else "data/houses")
    return base / (f"o{outreach_id}" if outreach_id else f"a{agent_id or 'unknown'}")


def lead_outreach(conn, agent_id: int | None) -> dict[str, Any]:
    """The lead card for an agent: the latest outreach row with a card (one card per lead, 2026-11-03)."""
    if not agent_id:
        return {}
    r = conn.execute("SELECT * FROM outreach WHERE agent_id=? AND asana_task_id IS NOT NULL ORDER BY id DESC LIMIT 1", (agent_id,)).fetchone()
    return db.row_to_dict(r) or {}


def _takeable(name: str) -> bool:
    n = (name or "").strip()
    return bool(n) and Path(n).suffix.lower() in TAKE_EXT and not ENGINE_PIC_RE.match(n)


def file_blob(conn, cfg: Any, llm, source: str, ext_id: str, name: str, data: bytes, agent_id: int | None, outreach_id: int | None,
              card_gid: str | None = None, now=None) -> list[str]:
    """Save one attachment (a ZIP is unpacked), extract its text, remember it. Returns the file names kept. Idempotent on ext_id."""
    if conn.execute("SELECT 1 FROM house_files WHERE ext_id=?", (ext_id,)).fetchone():
        return []
    now = now or now_utc()
    folder = folder_for(cfg, outreach_id, agent_id)
    folder.mkdir(parents=True, exist_ok=True)
    items = unzip(data) if Path(name).suffix.lower() == ".zip" else [(name, data)]
    if not items:
        raise RuntimeError("the ZIP holds no PDF, spreadsheet, document or screenshot I can read")
    kept: list[str] = []
    for i, (fname, blob) in enumerate(items):
        if len(blob) > MAX_BYTES:
            raise RuntimeError(f"too big ({len(blob) // 1_000_000} MB)")
        path = folder / _safe_name(fname)
        if path.exists() and path.read_bytes() != blob:
            path = folder / f"{path.stem} ({ext_id[-5:]}){path.suffix}"
        path.write_bytes(blob)
        if path.suffix.lower() in IMAGE_TYPES:        # a screenshot: Claude types it out; that text is what gets read
            txt = transcribe(llm, path, blob)
            if not txt:
                if len(items) > 1:
                    continue
                raise RuntimeError("it is a picture and Claude could not type it out (no API key on this machine, or the call failed)")
            path = txt
        try:
            text = (pdf_text_all(path, llm) if path.suffix.lower() == ".pdf" else doc_text(path) or "").strip()
        except Exception as e:  # noqa: BLE001
            log.warning("text of %s: %s", path, e)
            text = ""
        conn.execute("INSERT OR IGNORE INTO house_files (ext_id, source, agent_id, outreach_id, card_gid, name, path, text, created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?)",
                     (ext_id if i == 0 else f"{ext_id}#{i}", source, agent_id, outreach_id, card_gid, path.name, str(path), text[:MAX_TEXT], iso(now)))
        kept.append(path.name)
    if not kept:
        raise RuntimeError("nothing readable inside")
    return kept


OCR_PROMPT = ("This is one page of a recorded real-estate document (a deed, mortgage, lien, court order, notice of violation, tax bill). "
              "Type out ALL of its text exactly as printed, in reading order, keeping every name, date, dollar amount, book/page and case number. "
              "Do not summarize or interpret. If the page is blank or unreadable say so in one line.")
OCR_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}
MAX_OCR_PAGES = 20


def pdf_text_all(path: Path, llm=None, max_ocr_pages: int = MAX_OCR_PAGES, max_chars: int = MAX_TEXT) -> str:
    """Every page of a PDF (the shared reader stops at 60 pages: the title commitment sat on pages 73–87 of Celia's file). A scanned
    page with no text layer (the old recorded liens and deeds) is typed out by Claude when it is reachable, up to max_ocr_pages,
    marked "[page N, typed from the scan]"; otherwise "[page N: scanned image, no text]" so the reader knows what it is missing."""
    try:
        import pdfplumber
    except Exception:  # noqa: BLE001
        return doc_text(path)
    parts: list[str] = []
    total = ocr = 0
    try:
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                try:
                    text = (page.extract_text() or "").strip()
                except Exception:  # noqa: BLE001
                    text = ""
                if len(text) < 40:
                    if llm is not None and hasattr(llm, "transcribe_image") and ocr < max_ocr_pages:
                        try:
                            import io
                            buf = io.BytesIO()
                            page.to_image(resolution=110).original.convert("RGB").save(buf, format="PNG")
                            out = llm.transcribe_image("house_file_ocr", buf.getvalue(), "image/png", OCR_PROMPT, OCR_SCHEMA)
                            ocr += 1
                            if out and (out.get("text") or "").strip():
                                text = f"[page {i}, typed from the scan]\n{out['text'].strip()}"
                        except Exception as e:  # noqa: BLE001
                            log.warning("ocr page %s of %s: %s", i, path.name, e)
                    if len(text) < 40:
                        text = f"[page {i}: scanned image, no text]"
                else:
                    text = f"[page {i}]\n{text}"
                parts.append(text)
                total += len(text)
                if total > max_chars:
                    parts.append(f"[… {len(pdf.pages) - i} more pages not kept]")
                    break
    except Exception as e:  # noqa: BLE001
        log.warning("pdf pages of %s: %s", path, e)
        return doc_text(path)
    return "\n\n".join(parts)


def files_for(conn, agent_id: int | None, max_chars: int = PROMPT_TEXT) -> list[dict[str, Any]]:
    """What the prompts get: every file on this lead, newest last, with its text (trimmed)."""
    if not agent_id:
        return []
    out = []
    for r in conn.execute("SELECT name, source, created_at, text FROM house_files WHERE agent_id=? ORDER BY id", (agent_id,)).fetchall():
        text = (r["text"] or "").strip()
        out.append({"name": r["name"], "from": "the agent's email" if r["source"] == "gmail" else "Rodrigo (dropped on the card)",
                    "when": r["created_at"], "text": text[:max_chars] + (" …[cut]" if len(text) > max_chars else "") if text
                    else "(no readable text: a scanned image; open the file)"})
    return out


def _said(conn, agent_id: int | None) -> str:
    return "\n".join(f"- {n}" for (n,) in conn.execute("SELECT name FROM house_files WHERE agent_id=? ORDER BY id", (agent_id,)))


def read_for_rodrigo(conn, cfg: Any, asana, llm, outreach: dict[str, Any], new_names: list[str]) -> bool:
    """One 🧠 READ FOR YOU on the lead card, from every file on the lead. False when Claude is unreachable (the 📎 comment stays)."""
    gid = outreach.get("asana_task_id")
    if not gid or llm is None or not getattr(llm, "available", False):
        return False
    from .card_chat import context
    ctx = context(conn, {"outreach_id": outreach["id"]}, (asana.task(gid) or {}).get("notes") or "", [])
    ctx.pop("comment_thread_on_this_card", None)
    user = json.dumps({"new_files": new_names, **ctx}, ensure_ascii=False, default=str)
    out = llm.structured("house_files_read", READ_SYSTEM, user, READ_SCHEMA, max_tokens=3500)
    if not out or not (out.get("read") or "").strip():
        return False
    qs = [q for q in (out.get("next_questions") or []) if q.strip()][:5]
    tail = ("\n\nQUESTIONS THE FILES DON'T ANSWER (Charles can ask; comment the one you want)\n" + "\n".join(f"- {q}" for q in qs)) if qs else ""
    tail += _apply_cure_from_files(conn, cfg, outreach, out.get("cure") or {})
    asana.comment(gid, f"{READ_PREFIX} · {', '.join(new_names)}\n\n{out['read'].strip()}{tail}\n\n"
                       "Internal read, nothing sent. Ask anything about these files in a comment here; the answer uses them.")
    return True


def _apply_cure_from_files(conn, cfg: Any, outreach: dict[str, Any], c: dict[str, Any]) -> str:
    """The liens / demolition / legal-sf figures the read found → the house's cure record (Rodrigo's own values are never
    overwritten) → the valuation re-run on the legal square footage. Returns the lines for the card, "" when the files gave nothing."""
    from . import cure
    fields = {k: int(c[k]) for k in ("liens_total", "demo_sqft", "legalize_sqft", "legal_sqft") if c.get(k)}
    if not fields:
        return ""
    if c.get("note"):
        fields["note"] = str(c["note"])[:300]
    pids = json.loads(outreach.get("property_ids") or "[]")
    lines = []
    for pid in pids[:1]:
        rec = cure.set_cure(conn, pid, fields, source="files")
        res = cure.revalue(conn, cfg, pid)
        lines = cure.costs(rec, cfg)["lines"]
        if res:
            lines.append(f"ARV {res['arv']:,} · internal MAO {res['mao']:,} with the above taken off (same comps, legal size)")
    if not lines:
        return ""
    return ("\n\nLEGAL-SF MATH (from the files; comment your own figures to override, e.g. \"legal sf 1564, demolish 500 sf, no rehab, "
            "seller pays the liens\")\n" + "\n".join(f"- {l}" for l in lines))


def file_gmail_attachments(conn, cfg: Any, gmail, message_id: str, thread: dict[str, Any], asana=None, llm=None, now=None) -> list[str]:
    """Every readable file on an agent's email, filed and read. Returns the names kept (empty when none / no attachments API)."""
    if gmail is None or not callable(getattr(gmail, "attachments", None)) or not message_id:
        return []
    try:
        atts = [a for a in gmail.attachments(message_id) if _takeable(a.get("filename") or "")]
    except Exception as e:  # noqa: BLE001
        log.warning("gmail attachments %s: %s", message_id, e)
        return []
    if not atts:
        return []
    agent_id = thread.get("agent_id")
    o = lead_outreach(conn, agent_id)
    kept: list[str] = []
    failed: list[str] = []
    for a in atts:
        try:
            kept += file_blob(conn, cfg, llm, "gmail", f"gmail:{message_id}:{a.get('filename')}", a.get("filename") or "file", a.get("bytes") or b"",
                              agent_id, o.get("id"), o.get("asana_task_id"), now)
        except Exception as e:  # noqa: BLE001
            log.warning("file %s from %s: %s", a.get("filename"), message_id, e)
            failed.append(f"{a.get('filename')} ({str(e)[:80]})")
    if asana is not None and o.get("asana_task_id") and (kept or failed):
        who = (conn.execute("SELECT name FROM agents WHERE id=?", (agent_id,)).fetchone() or ["the agent"])[0]
        line = f"📎 {who} attached {len(kept)} file(s) to this email, filed and read: {', '.join(kept)}." if kept else ""
        if failed:
            line += f" Couldn't read: {'; '.join(failed)}."
        asana.comment(o["asana_task_id"], line.strip() + " Ask what they mean in a comment here.")
        if kept:
            try:
                read_for_rodrigo(conn, cfg, asana, llm, o, kept)
            except Exception as e:  # noqa: BLE001
                log.warning("house file read for %s: %s", o.get("asana_task_id"), e)
    return kept


def pull_card_files(conn, cfg: Any, asana, llm=None, now=None, since: str | None = None) -> dict[str, Any]:
    """Files Rodrigo drops on a lead card (Email Approvals + Warm Leads boards): downloaded once, read, 🧠 posted. Cards changed since
    the last look only (`kv.house_files_seen`); `since=""` looks at every card."""
    from .approvals import parse_card
    res: dict[str, Any] = {"cards": 0, "files": 0, "reads": 0, "errors": 0}
    if asana is None or not callable(getattr(asana, "attachments", None)) or not callable(getattr(asana, "project_tasks", None)):
        return res
    a = cfg.get("outreach.asana", {}) or {}
    projects = [p for p in (a.get("project"), (a.get("warm_leads") or {}).get("project")) if p]
    if not projects:
        return res
    now = now or now_utc()
    if since is None:
        row = conn.execute("SELECT value FROM kv WHERE key=?", (SEEN_KEY,)).fetchone()
        since = row[0] if row else ""
    started = iso(now)
    for project in projects:
        try:
            cards = asana.project_tasks(project)
        except Exception as e:  # noqa: BLE001
            res["errors"] += 1
            log.warning("project tasks %s: %s", project, e)
            continue
        for card in cards:
            if card.get("completed") or (since and (card.get("modified_at") or "") <= since):
                continue
            gid = card["gid"]
            try:
                atts = [x for x in asana.attachments(gid) if _takeable(x.get("name") or "")]
            except Exception as e:  # noqa: BLE001
                res["errors"] += 1
                log.warning("attachments on %s: %s", gid, e)
                continue
            new = [x for x in atts if not conn.execute("SELECT 1 FROM house_files WHERE ext_id=?", (f"asana:{x['gid']}",)).fetchone()]
            if not new:
                continue
            res["cards"] += 1
            pc = parse_card((asana.task(gid) or {}).get("notes") or "")
            o = db.row_to_dict(conn.execute("SELECT * FROM outreach WHERE id=?", (pc["outreach_id"],)).fetchone()) if pc else None
            if not o:
                asana.comment(gid, "📎 Got the file(s), but this card has no (outreach #N) line, so I can't tell which lead they belong to.")
                for x in new:
                    conn.execute("INSERT OR IGNORE INTO house_files (ext_id, source, card_gid, name, created_at) VALUES (?,?,?,?,?)",
                                 (f"asana:{x['gid']}", "asana", gid, x.get("name"), iso(now)))
                continue
            kept: list[str] = []
            for x in new:
                try:
                    if int(x.get("size") or 0) > MAX_BYTES:
                        raise RuntimeError(f"too big ({int(x['size']) // 1_000_000} MB)")
                    if not x.get("download_url"):
                        raise RuntimeError("no download link (a file linked from Dropbox / Drive is not attached)")
                    kept += file_blob(conn, cfg, llm, "asana", f"asana:{x['gid']}", x.get("name") or "file", asana.download(x["download_url"]),
                                      o.get("agent_id"), o["id"], gid, now)
                except Exception as e:  # noqa: BLE001
                    res["errors"] += 1
                    log.warning("download %s on %s: %s", x.get("name"), gid, e)
                    asana.comment(gid, f"📎 Couldn't read {x.get('name')}: {str(e)[:160]}.")
                    conn.execute("INSERT OR IGNORE INTO house_files (ext_id, source, agent_id, outreach_id, card_gid, name, created_at) VALUES (?,?,?,?,?,?,?)",
                                 (f"asana:{x['gid']}", "asana", o.get("agent_id"), o["id"], gid, x.get("name"), iso(now)))
            if not kept:
                continue
            res["files"] += len(kept)
            asana.comment(gid, f"📎 Filed your file(s) under data/houses/o{o['id']}/: {', '.join(kept)}. Reading them now; "
                               "every answer and draft on this card uses them from here on.")
            try:
                if read_for_rodrigo(conn, cfg, asana, llm, o, kept):
                    res["reads"] += 1
            except Exception as e:  # noqa: BLE001
                res["errors"] += 1
                log.warning("house file read on %s: %s", gid, e)
    if not res["errors"]:
        conn.execute("INSERT INTO kv(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (SEEN_KEY, started))
    conn.commit()
    return res
