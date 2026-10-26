"""SMS opt-ins: the form's records reach the agents, the planner only texts numbers with consent, first texts carry STOP."""
import json

import pytest

from titan import db
from titan.consent import pull_consents, record, summary
from titan.outreach.compose import STOP_LINE, compose_sms, validate_sms
from titan.outreach.send import plan


class _Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Session:
    """Fakes the Vercel Blob list call and the per-record fetch."""

    def __init__(self, records):
        self.records = records   # pathname -> dict
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append(url)
        if url.endswith("blob.vercel-storage.com/"):
            assert params["prefix"] == "titan/consents/" and headers["authorization"].startswith("Bearer ")
            return _Resp({"blobs": [{"pathname": p, "url": f"https://blob/{p}", "uploadedAt": "2026-10-26T12:00:00Z"} for p in self.records], "hasMore": False})
        return _Resp(self.records[url.split("https://blob/")[1]])


def _agent(conn, name="Lise Freeman", phone="+15615550123", email="lise@example.com"):
    now = "2026-10-20T00:00:00+00:00"
    cur = conn.execute("INSERT INTO agents(name,name_key,brokerage,brokerage_key,phone,email,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                       (name, name.lower(), "Brokerage", "brokerage", phone, email, now, now))
    return int(cur.lastrowid)


def _rec(**kw):
    base = {"id": "x", "action": "opt_in", "created_at": "2026-10-26T14:03:00.000Z", "name": "Lise Freeman", "phone": "+15615550123",
            "email": "", "property": "529 23rd Ave N", "agent_id": "", "ref": "", "consent_text": "By checking this box ...",
            "ip": "1.2.3.4", "user_agent": "Safari"}
    base.update(kw)
    return base


def test_pull_matches_agent_by_phone_and_is_idempotent(conn, cfg, monkeypatch):
    aid = _agent(conn)
    cfg.secrets["BLOB_READ_WRITE_TOKEN"] = "vercel_blob_rw_test"
    sess = _Session({"titan/consents/a.json": _rec()})
    res = pull_consents(conn, cfg, session=sess)
    assert res["new"] == 1 and res["matched"] == 1 and res["unmatched"] == 0 and res["total_opted_in"] == 1
    a = conn.execute("SELECT sms_consent_at, sms_consent_source FROM agents WHERE id=?", (aid,)).fetchone()
    assert a["sms_consent_at"].startswith("2026-10-26") and a["sms_consent_source"] == "form"
    again = pull_consents(conn, cfg, session=sess)
    assert again["new"] == 0 and conn.execute("SELECT COUNT(*) FROM sms_consents").fetchone()[0] == 1
    rows = summary(conn)
    assert rows[0]["agent_name"] == "Lise Freeman" and rows[0]["property"] == "529 23rd Ave N"


def test_pull_matches_by_email_then_keeps_unmatched(conn, cfg):
    aid = _agent(conn, phone=None, email="bob@example.com")
    cfg.secrets["BLOB_READ_WRITE_TOKEN"] = "t"
    sess = _Session({"titan/consents/b.json": _rec(name="Bob", phone="(305) 555-0199", email="Bob@Example.com"),
                     "titan/consents/c.json": _rec(name="Nobody", phone="3055550100")})
    res = pull_consents(conn, cfg, session=sess)
    assert res["matched"] == 1 and res["unmatched"] == 1
    a = conn.execute("SELECT phone, sms_consent_at FROM agents WHERE id=?", (aid,)).fetchone()
    assert a["phone"] == "+13055550199" and a["sms_consent_at"]      # the number they asked to be texted on


def test_opt_out_record_clears_consent_and_adds_dnc(conn, cfg):
    aid = _agent(conn)
    record(conn, "titan/consents/in.json", _rec())
    assert conn.execute("SELECT sms_consent_at FROM agents WHERE id=?", (aid,)).fetchone()[0]
    record(conn, "titan/consents/out.json", _rec(action="opt_out"))
    assert conn.execute("SELECT sms_consent_at FROM agents WHERE id=?", (aid,)).fetchone()[0] is None
    assert db.is_dnc(conn, phone="+15615550123")


def test_pull_without_token_skips_and_bad_record_is_counted(conn, cfg):
    res = pull_consents(conn, cfg)
    assert res["new"] == 0 and "skipped" in res
    cfg.secrets["BLOB_READ_WRITE_TOKEN"] = "t"
    sess = _Session({"titan/consents/bad.json": _rec(phone="not a number", name="")})
    res = pull_consents(conn, cfg, session=sess)
    assert res["new"] == 1 and res["unmatched"] == 1    # stored for the record even when it matches nobody


def test_plan_never_texts_without_consent(loaded, cfg, monkeypatch):
    conn = loaded
    conn.execute("UPDATE agents SET email=lower(replace(name,' ','.'))||'@example-brokerage.com', email_confidence='high' WHERE email IS NULL")
    conn.execute("UPDATE agents SET sms_consent_at=NULL")
    monkeypatch.setenv("TITAN_CHANNEL", "SMS_FIRST")
    todo = plan(conn, cfg, "2026-10-22")
    assert todo["sms"] == [] and todo["email"], "no opt-in → no text; those agents fall back to email"
    conn.execute("UPDATE agents SET sms_consent_at='2026-10-21T00:00:00+00:00' WHERE phone IS NOT NULL AND is_office_line=0")
    todo = plan(conn, cfg, "2026-10-22")
    assert todo["sms"]
    assert all(conn.execute("SELECT sms_consent_at FROM agents WHERE id=?", (i["agent"]["id"],)).fetchone()[0] for i in todo["sms"])


def test_plan_consent_switch_off(loaded, cfg, monkeypatch):
    loaded.execute("UPDATE agents SET sms_consent_at=NULL")
    monkeypatch.setenv("TITAN_CHANNEL", "SMS_FIRST")
    cfg.raw["outreach"]["sms_require_consent"] = False
    try:
        assert plan(loaded, cfg, "2026-10-22")["sms"]
    finally:
        cfg.raw["outreach"]["sms_require_consent"] = True


@pytest.mark.parametrize("street", ["107 NW 11th St", "12345 Southwest 147th Terrace Road Extension"])
def test_first_text_ends_with_stop_line_under_160(cfg, street):
    agent = {"id": 1, "name": "Christopher Montgomery", "phone": "+13055550100"}
    prop = {"id": 1, "street": street, "city": "Hollywood", "county": "Broward", "list_price": 400000, "days_on_market": 70}
    for seed in range(8):
        d = compose_sms(cfg, agent, prop, seed)
        assert d["body"].endswith(STOP_LINE.strip()) and len(d["body"]) <= 160 and validate_sms(d["body"]) == []
        assert "Titan Realty" in d["body"]
