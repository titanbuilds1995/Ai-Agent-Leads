from titan import cli
from titan.config import Config


def test_gmail_token_from_env_is_written_to_disk(tmp_path, conn, monkeypatch):
    """Cloud sessions keep the token file's contents in GMAIL_TOKEN_JSON_CONTENT; it is materialised before use."""
    seen = {}

    class FakeGmail:
        def __init__(self, oauth_json, token_json, sender, sender_name, interactive=False):
            seen["token"] = token_json.read_text()
            seen["sender"] = sender

    monkeypatch.setattr("titan.outreach.gmail_client.GmailClient", FakeGmail)
    cfg = Config(raw={"operator": {"sender_name": "Rodrigo Gayoso"}}, root=tmp_path,
                 secrets={"GMAIL_TOKEN_JSON_CONTENT": '{"refresh_token": "x"}', "GMAIL_SENDER": "charles@example.com"})
    out = cli.build_clients(cfg, conn, need=("gmail",))
    assert isinstance(out["gmail"], FakeGmail)
    assert seen == {"token": '{"refresh_token": "x"}', "sender": "charles@example.com"}
    assert (tmp_path / "secrets" / "gmail_token.json").exists()


def test_bare_access_token_is_accepted_and_checked(tmp_path, monkeypatch):
    """GMAIL_TOKEN_JSON_CONTENT sometimes holds a bare 'ya29...' access token instead of the token JSON: use it as-is
    (it works ~1 hour), and say plainly what to paste instead once it stops working."""
    import sys, types
    from titan.outreach import gmail_client

    calls = {"profile": 0}

    class FakeSvc:
        def users(self):
            return self

        def getProfile(self, userId):
            return self

        def execute(self):
            calls["profile"] += 1
            if calls["profile"] > 1:
                raise RuntimeError("401 invalid credentials")
            return {"emailAddress": "charles@example.com"}

    creds_mod = types.SimpleNamespace(Credentials=lambda token=None: types.SimpleNamespace(token=token, expired=False, refresh_token=None, valid=True))
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", types.SimpleNamespace(Request=object))
    monkeypatch.setitem(sys.modules, "google.oauth2.credentials", creds_mod)
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", types.SimpleNamespace(build=lambda *a, **k: FakeSvc()))
    token = tmp_path / "gmail_token.json"
    token.write_text("ya29.a0AX-bare-access-token")
    g = gmail_client.GmailClient(tmp_path / "oauth.json", token, "charles@example.com", "Charles Grant")
    assert g.bare_token is True
    import pytest
    with pytest.raises(gmail_client.GmailUnavailable, match="secrets/gmail_token.json"):
        gmail_client.GmailClient(tmp_path / "oauth.json", token, "charles@example.com", "Charles Grant")


def test_dump_threads_prints_each_message_once():
    from titan.outreach.gmail_client import dump_threads

    class FakeGmail:
        def search(self, q, max_results=50):
            return ["m1", "m2"]

        def message(self, mid):
            return {"thread_id": "t1"}

        def thread_messages(self, tid):
            return [{"from": "Charles <c@x.com>", "to": "lise@x.com", "subject": "Quick question", "internal_ts": 1792900000, "body": "How much work?"},
                    {"from": "Lise <lise@x.com>", "to": "c@x.com", "subject": "Re: Quick question", "internal_ts": 1792950000, "body": "Mostly cosmetic."}]

    text = dump_threads(FakeGmail(), "from:lise@x.com")
    assert text.count("THREAD t1") == 1 and "Mostly cosmetic." in text and "How much work?" in text
    assert dump_threads(type("G", (), {"search": lambda self, q, max_results=50: []})(), "from:nobody") == "no threads match: from:nobody"
