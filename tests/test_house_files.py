"""Files on a house lead are read (Rodrigo, 2026-11-16, Celia Ruiz: the agent attached a lien search and a title search, the engine
logged "Please find attached" and answered his question without the files)."""
import json

from titan.approvals import BOT, parse_card, send_approved
from titan.card_chat import context
from titan.house_files import READ_PREFIX, file_gmail_attachments, files_for, pull_card_files
from titan.replies.classifier import classify_rules, parse_money
from titan.replies.inbox import check_replies
from titan.replies.writer import reader_case
from tests.test_approvals import SAT_10AM_ET, FakeAsana, FakeGmail, _cards, _sections

LIEN = ("MLS SUMMARY REPORT 5341 nw 180 terrace miami gardens FL 33055\nTAXES 2025 PAID\nUTILITIES Amount Due YES $191.81\n"
        "LIENS: Code enforcement lien(s) T013412 $11,553.00 LIEN PLACED\nPERMITS Case 1997070140 (open): Electrical\n")


class FilesAsana(FakeAsana):
    """FakeAsana whose cards can carry attachments (the base class uses `attachments` as the list of pictures it attached)."""
    def __init__(self):
        super().__init__()
        self.pictures = self.attachments            # the base class keeps the pictures it attached under this name
        del self.attachments                        # ... which would shadow the method below
        self.files, self.blobs, self.modified = {}, {}, {}
    def attach(self, task, path, name=None): self.pictures.append((task, name, path.exists()))
    def attachments(self, gid): return list(self.files.get(gid, []))
    def download(self, url): return self.blobs[url]
    def project_tasks(self, project):
        return [{"gid": g, "modified_at": self.modified.get(g, "2026-11-16T10:00:00Z"), "completed": t.get("completed", False)} for g, t in self.tasks.items()]
    def drop(self, gid, name, data, when="2026-11-16T12:00:00Z"):
        url = f"https://files/{gid}/{name}"
        self.blobs[url] = data
        self.files.setdefault(gid, []).append({"gid": f"att-{gid}-{len(self.files.get(gid, []))}", "name": name, "download_url": url, "size": len(data)})
        self.modified[gid] = when


class ReadLLM:
    available = True
    def __init__(self): self.calls = []
    def structured(self, purpose, system, user, schema, **k):
        self.calls.append((purpose, json.loads(user)))
        if purpose == "house_files_read":
            return {"read": "WHAT THE FILES ARE\n- Lien search, Skyline, 07/01/2026.\nWHAT STAYS WITH THE HOUSE\n- Code lien T013412 $11,553.",
                    "next_questions": ["Has the T013412 violation been corrected?"]}
        return None


def _lead(loaded, cfg, tmp_path, monkeypatch, asana=None):
    """One sent first email → the agent's thread exists; returns (asana, gid, outreach_id, agent_id, thread_row)."""
    monkeypatch.setenv("TITAN_MODE", "DRAFT_FOR_APPROVAL")
    cfg.root = tmp_path
    loaded.execute("UPDATE agents SET email=lower(replace(name,' ','.'))||'@example.com', email_confidence='high' WHERE email IS NULL")
    from tests.test_approvals import run_outreach
    asana = asana or FilesAsana()
    run_outreach(loaded, cfg, None, FakeGmail(), None, asana=asana)
    s = _sections(cfg)
    gid = list(asana.tasks)[0]
    asana.move(gid, s["approved"])
    send_approved(loaded, cfg, asana, FakeGmail(), now=SAT_10AM_ET)
    oid = parse_card(asana.tasks[gid]["notes"])["outreach_id"]
    aid = loaded.execute("SELECT agent_id FROM outreach WHERE id=?", (oid,)).fetchone()[0]
    t = loaded.execute("SELECT * FROM threads WHERE agent_id=?", (aid,)).fetchone()
    return asana, gid, oid, aid, dict(t)


def test_a_file_dropped_on_the_lead_card_is_filed_read_and_used_in_every_prompt(loaded, cfg, tmp_path, monkeypatch):
    asana, gid, oid, aid, _ = _lead(loaded, cfg, tmp_path, monkeypatch)
    asana.drop(gid, "Lien Search (1).txt", LIEN.encode())
    asana.drop(gid, "email_107_v3.png", b"\x89PNG not a document")           # the engine's own picture: never read
    llm = ReadLLM()
    res = pull_card_files(loaded, cfg, asana, llm)
    assert res["files"] == 1 and res["reads"] == 1 and res["errors"] == 0
    assert (tmp_path / "data" / "houses" / f"o{oid}" / "Lien Search (1).txt").exists()
    files = files_for(loaded, aid)
    assert len(files) == 1 and "T013412" in files[0]["text"] and files[0]["from"].startswith("Rodrigo")
    last = asana.stories[gid][-1]
    assert last.startswith(f"{BOT} {READ_PREFIX}") and "$11,553" in last and "Has the T013412" in last
    assert any("📎 Filed" in s for s in asana.stories[gid])
    purpose, user = llm.calls[0]
    assert purpose == "house_files_read" and user["files_on_this_lead"][0]["name"] == "Lien Search (1).txt"
    # every later prompt sees it: the card chat, the reply writer's second reader
    assert context(loaded, {"outreach_id": oid}, "", [])["files_on_this_lead"][0]["name"] == "Lien Search (1).txt"
    row = loaded.execute("SELECT * FROM outreach WHERE id=?", (oid,)).fetchone()
    case = reader_case(loaded, dict(row), {"name": "Celia"}, [])
    assert "T013412" in case["files_on_this_lead"][0]["text"]
    # second round: nothing new, nothing re-read, no new comment
    n = len(asana.stories[gid])
    res = pull_card_files(loaded, cfg, asana, llm)
    assert res["files"] == 0 and len(asana.stories[gid]) == n and len(llm.calls) == 1


def test_an_agents_email_attachment_is_filed_with_the_reply(loaded, cfg, tmp_path, monkeypatch):
    asana, gid, oid, aid, thread = _lead(loaded, cfg, tmp_path, monkeypatch)

    class MailWithFiles(FakeGmail):
        def thread_messages(self, ext_id):
            return [{"id": "in-1", "from": "celia@example.com", "body": "Please find attached,", "internal_ts": 5, "label_ids": []}]
        def attachments(self, mid):
            return [{"filename": "Title Search.txt", "mime": "text/plain", "bytes": b"ALTA Commitment. Title vested in Julio Piloto and Rosa Piloto, tenants in common by reason of divorce. Mortgage $65,000.00 First NLC."},
                    {"filename": "logo.gif", "mime": "image/gif", "bytes": b"x"}]

    loaded.execute("UPDATE threads SET external_id='gm-1' WHERE id=?", (thread["id"],))
    counts = check_replies(loaded, cfg, MailWithFiles(), None, None, now=SAT_10AM_ET, asana=asana)
    assert counts["new_replies"] == 1 and counts["files"] == 1
    files = files_for(loaded, aid)
    assert len(files) == 1 and files[0]["from"] == "the agent's email" and "tenants in common" in files[0]["text"]
    assert any("📎" in s and "Title Search.txt" in s for s in asana.stories[gid])
    # the same message again never files twice
    counts = check_replies(loaded, cfg, MailWithFiles(), None, None, now=SAT_10AM_ET, asana=asana)
    assert counts.get("files", 0) == 0 and len(files_for(loaded, aid)) == 1


def test_a_phone_or_street_address_in_the_signature_is_not_a_counter():
    celia = ("Good morning, Charles,\n\nThanks for following up. Yes, the seller is still open to reviewing an offer. Please send it over, "
             "and I'll present it to the seller.\nBest,\n\nCelia Ruiz, P.A.\nBroker Associate\n(305)965-1882\ncelia@morethanthesale.com\n"
             "1200 E Las Olas Blvd, Suite 103, Fort Lauderdale, FL 33301")
    assert parse_money(celia, 435000) is None
    assert classify_rules(celia, 435000).label == "ASK_FOR_OFFER"
    assert parse_money("Seller would take 325 for it. 5341 NW 180th Ter", 435000) == 325000
