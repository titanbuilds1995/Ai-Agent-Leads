from fastapi.testclient import TestClient

from titan.outreach.send import ensure_thread, log_message
from titan.webhook import build_app


def test_webhook_inbound_and_health(loaded, cfg, monkeypatch):
    monkeypatch.setenv("TITAN_MODE", "FULL_AUTO")
    a = loaded.execute("SELECT a.id, a.phone, p.id pid FROM agents a JOIN properties p ON p.agent_id=a.id JOIN valuations v ON v.property_id=p.id "
                       "WHERE v.rating IN ('HOT','WARM') AND a.is_office_line=0 LIMIT 1").fetchone()
    tid = ensure_thread(loaded, a["id"], "sms", a["phone"], [a["pid"]])
    log_message(loaded, tid, "out", "hi", external_id="SM0")
    sent = []

    class T:
        def send(self, to, body): sent.append((to, body)); return {"sid": "SM9", "status": "queued"}
        def validate(self, url, params, sig): return sig == "good"

    app = build_app(cfg, conn=loaded, twilio=T(), gmail=object(), llm=object(), validate=True)
    client = TestClient(app)
    assert client.get("/health").json()["ok"] is True
    bad = client.post("/twilio/inbound", data={"From": a["phone"], "Body": "STOP", "MessageSid": "SM-bad"}, headers={"X-Twilio-Signature": "nope"})
    assert bad.status_code == 403
    ok = client.post("/twilio/inbound", data={"From": a["phone"], "Body": "STOP", "MessageSid": "SM-ok"}, headers={"X-Twilio-Signature": "good"})
    assert ok.status_code == 200 and "<Response>" in ok.text
    assert loaded.execute("SELECT state FROM threads WHERE id=?", (tid,)).fetchone()[0] == "STOPPED"
    assert loaded.execute("SELECT COUNT(*) FROM do_not_contact WHERE phone=?", (a["phone"],)).fetchone()[0] == 1
    st = client.post("/twilio/status", data={"MessageSid": "SM0", "MessageStatus": "undelivered"})
    assert st.status_code == 204
