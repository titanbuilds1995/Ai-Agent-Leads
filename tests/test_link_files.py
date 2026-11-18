"""Documents brokers LINK instead of attaching are opened and filed (Rodrigo, 2026-11-18, Tammy Huston's Dropbox for Mariner Plaza)."""
import io
import zipfile

from titan.commercial import link_files as lf
from titan.util import iso
from tests.test_approvals import SAT_10AM_ET
from tests.test_commercial import FakeOMGmail, _om_board

TAMMY = ("Charles,\n\nThank you for getting the NDA back to us.\n\nPlease see the link below for review of Mariner Plaza.\n\n"
         "https://www.dropbox.com/scl/fo/vvhsd/AK028?rlkey=wqqu3&st=tbci&dl=0\n\nThanks!\n\nTammy Huston\n\n"
         "From: Charles Grant <charles@titanrealtyinvestments.com>\nSent: Thursday\n"
         "https://drive.google.com/file/d/OLDQUOTED/view\n")


class Resp:
    def __init__(self, data, ctype="application/zip", status=200, name=None):
        self.data, self.status_code = data, status
        self.headers = {"Content-Type": ctype}
        if name:
            self.headers["Content-Disposition"] = f'attachment; filename="{name}"'

    def iter_content(self, n):
        yield self.data


class Session:
    def __init__(self, routes):
        self.routes, self.got = routes, []

    def get(self, url, **kw):
        self.got.append(url)
        for key, resp in self.routes.items():
            if key in url:
                return resp
        return Resp(b"<html>sign in</html>", "text/html")


def _zip(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    return b.getvalue()


def test_plan_rewrites_share_links_and_never_logs_in():
    k, u = lf.plan("https://www.dropbox.com/scl/fo/x/y?rlkey=r&dl=0")
    assert k == "dropbox" and "dl=1" in u and "rlkey=r" in u
    assert lf.plan("https://drive.google.com/file/d/ABC_1/view?usp=sharing")[1].endswith("id=ABC_1&export=download&confirm=t")
    assert lf.plan("https://docs.google.com/spreadsheets/d/S1/edit#gid=0")[1] == "https://docs.google.com/spreadsheets/d/S1/export?format=xlsx"
    assert lf.plan("https://1drv.ms/b/s!AbC")[1].startswith("https://api.onedrive.com/v1.0/shares/u!")
    for gated in ("https://platform.reverecre.com/deal/1", "https://drive.google.com/drive/folders/F1", "https://buildout.com/x.pdf",
                  "https://docsend.com/view/abc"):
        assert lf.plan(gated) == ("login", None)
    safe = "https://nam13.safelinks.protection.outlook.com/?url=https%3A%2F%2Fwww.dropbox.com%2Fs%2Fq%2FOM.pdf%3Fdl%3D0&data=x"
    assert lf.unwrap(safe) == "https://www.dropbox.com/s/q/OM.pdf?dl=0"


def test_only_the_brokers_own_words_count():
    assert lf.doc_urls(TAMMY) == ["https://www.dropbox.com/scl/fo/vvhsd/AK028?rlkey=wqqu3&st=tbci&dl=0"]
    assert lf.doc_urls("see https://www.linkedin.com/in/tammy and https://naitalcor.com/") == []


def test_dropbox_folder_is_filed_read_and_the_thread_goes_to_books(conn, cfg, tmp_path):
    from titan.commercial.outreach import poll_om_replies, send_om_requests
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    gm = FakeOMGmail()
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    conn.execute("UPDATE commercial_threads SET sent_at=?", (iso(SAT_10AM_ET),))
    gm.threads["t1"].append({"id": "r1", "from": "Tammy Huston <tammy@talcor.com>", "body": TAMMY,
                             "internal_ts": int(SAT_10AM_ET.timestamp()) + 3600, "label_ids": [], "attachments": []})
    poll_om_replies(conn, cfg, gm, asana)
    pkg = _zip({"Mariner Plaza OM.pdf": b"%PDF-1.4 om", "Financials/T12 2025.xlsx": b"PK\x03\x04xx", "photos/front.jpg": b"jpg",
                "__MACOSX/._x.pdf": b"junk"})
    s = Session({"dropbox.com": Resp(pkg, name="Mariner Plaza.zip")})
    out = lf.pull_link_files(conn, cfg, asana, llm=None, session=s)
    assert out["opened"] == 1 and out["files"] == 2 and "dl=1" in s.got[0]
    folder = tmp_path / "data" / "listings" / "28440860"
    assert (folder / "Mariner Plaza OM.pdf").exists() and (folder / "T12 2025.xlsx").exists() and not (folder / "front.jpg").exists()
    assert any("🔗 Opened the www.dropbox.com link" in c for c in asana.stories[gid])
    assert conn.execute("SELECT state FROM commercial_threads").fetchone()[0] == "BOOKS"
    assert lf.opened_urls(conn) == {"https://www.dropbox.com/scl/fo/vvhsd/AK028?rlkey=wqqu3&st=tbci&dl=0"}
    # next round: never opened twice
    assert lf.pull_link_files(conn, cfg, asana, session=s)["links"] == 0 and len(s.got) == 1


def test_login_page_is_reported_once_and_network_errors_retry(conn, cfg, tmp_path):
    from titan.commercial.outreach import poll_om_replies, send_om_requests
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    gm = FakeOMGmail()
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    gm.threads["t1"].append({"id": "r1", "from": "Adam <adam@trustrealtyusa.com>", "internal_ts": int(SAT_10AM_ET.timestamp()) + 60,
                             "body": "Data room: https://platform.reverecre.com/deal/77\nOM: https://example.com/files/OM.pdf", "label_ids": [], "attachments": []})
    poll_om_replies(conn, cfg, gm, asana)

    class Boom(Session):
        def get(self, url, **kw):
            import requests
            raise requests.ConnectionError("blocked")
    out = lf.pull_link_files(conn, cfg, asana, session=Boom({}))
    assert out["login"] == 1 and out["errors"] == 1
    assert sum("🔐" in c for c in asana.stories[gid]) == 1
    s = Session({"example.com": Resp(b"%PDF-1.4", "application/pdf")})
    out2 = lf.pull_link_files(conn, cfg, asana, session=s)
    assert out2["opened"] == 1 and sum("🔐" in c for c in asana.stories[gid]) == 1
