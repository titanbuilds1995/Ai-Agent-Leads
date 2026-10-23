import json
from types import SimpleNamespace

from titan.ingest.apify import ApifyClient


class FakeSession:
    """Accepts either the token param or an 'injected' flag set by the test; records the calls."""
    def __init__(self, injected=False, token="good"):
        self.injected, self.token, self.calls = injected, token, []

    def get(self, url, params=None, timeout=None):
        return self.request("GET", url, params=params or {}, timeout=timeout)

    def request(self, method, url, params=None, timeout=None, **kw):
        params = params or {}
        self.calls.append((method, url, dict(params)))
        if params.get("token") == self.token or (self.injected and "token" not in params):
            return SimpleNamespace(status_code=200, json=lambda: {"data": {"username": "titan", "plan": {"id": "STARTER"}}}, text="")
        return SimpleNamespace(status_code=401, json=lambda: {"error": {"type": "user-or-token-not-found"}}, text="nope")


def test_token_in_env_is_sent_as_query_param():
    s = FakeSession()
    ok, detail = ApifyClient("good", session=s).auth_ok()
    assert ok and "titan" in detail and "STARTER" in detail
    assert s.calls[0][2]["token"] == "good"


def test_blank_token_with_injected_credential_authenticates_without_param():
    s = FakeSession(injected=True)
    ok, _ = ApifyClient(None, session=s).auth_ok()
    assert ok and "token" not in s.calls[0][2]
    # and a normal API call also goes out without a token param
    ApifyClient("", session=s)._req("GET", "/users/me")
    assert "token" not in s.calls[-1][2]


def test_blank_token_without_injection_is_rejected():
    ok, detail = ApifyClient(None, session=FakeSession()).auth_ok()
    assert not ok and "401" in detail


def test_bad_token_is_rejected():
    ok, detail = ApifyClient("bad", session=FakeSession()).auth_ok()
    assert not ok and "401" in detail
