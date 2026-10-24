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
