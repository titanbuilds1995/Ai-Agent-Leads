"""`make doctor`: check every credential, setting and network path the live run needs, and say how to fix each one.

Never prints a secret value. Each check is a small function returning (status, detail, fix); the table is printed and
also returned so tests and the report can use it. Statuses: ok | warn | fail | skip.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

PLACEHOLDER_PHONE = "+13055550100"
PLACEHOLDER_ADDRESS = "Miami, FL"
SAMPLE_EMAIL_MARKERS = ("Hi Maria, Rodrigo here", "Hey John, quick one", "Hi Sandra, this is Rodrigo")

Check = tuple[str, str, str]   # status, detail, fix


def _ok(detail: str = "") -> Check: return ("ok", detail, "")
def _warn(detail: str, fix: str) -> Check: return ("warn", detail, fix)
def _fail(detail: str, fix: str) -> Check: return ("fail", detail, fix)
def _skip(detail: str, fix: str = "") -> Check: return ("skip", detail, fix)


# --- config ---------------------------------------------------------------------------------

def check_env_file(cfg) -> Check:
    p = cfg.root / ".env"
    if not p.exists():
        return _fail(".env missing", "make setup (copies .env.example → .env), then fill it in")
    missing = [k for k in ("APIFY_TOKEN", "ANTHROPIC_API_KEY", "GMAIL_SENDER", "REPORT_TO",
                           "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_MESSAGING_SERVICE_SID", "WEBHOOK_PUBLIC_URL") if not cfg.secret(k)]
    if missing:
        return _warn(f"{len(missing)} blank: {', '.join(missing)}", "fill the blank keys in .env (deploy/DEPLOY.md part A has where each comes from)")
    return _ok("all keys present")


def check_operator(cfg) -> Check:
    probs = []
    if cfg.get("operator.sender_phone") == PLACEHOLDER_PHONE:
        probs.append("sender_phone is the placeholder")
    if (cfg.get("operator.mailing_address") or "").strip() == PLACEHOLDER_ADDRESS:
        probs.append("mailing_address is the placeholder")
    examples = cfg.get("outreach.example_emails") or []
    if any(any(m in (e or "") for m in SAMPLE_EMAIL_MARKERS) for e in examples):
        probs.append("example_emails are still the built-in samples")
    if probs:
        return _fail("; ".join(probs), "edit config.yaml → operator (real cell, postal address) and outreach.example_emails (2–3 real emails)")
    return _ok("real phone, address and example emails")


def check_mode(cfg) -> Check:
    try:
        mode, ch = cfg.mode, cfg.channel
    except Exception as e:  # noqa: BLE001
        return _fail(str(e), "fix outreach.mode / outreach.channel in config.yaml")
    if mode != "DRAFT_FOR_APPROVAL":
        return _warn(f"{mode} / {ch}: messages WILL be sent", "set outreach.mode: DRAFT_FOR_APPROVAL until the first drafts have been reviewed")
    return _ok(f"{mode} / {ch}: nothing is sent")


# --- network + credentials ------------------------------------------------------------------

def check_reach(host: str, path: str = "/") -> Callable[[Any], Check]:
    """HTTPS reachability through whatever proxy the machine uses (plain DNS lookups lie inside proxied containers)."""
    def run(cfg) -> Check:
        import requests
        try:
            r = requests.get(f"https://{host}{path}", timeout=15, allow_redirects=False)
            return _ok(f"{host} answers (HTTP {r.status_code})")
        except requests.exceptions.ProxyError as e:
            return _fail(f"{host}: blocked by proxy/network policy", f"allow {host} in the network settings (cloud environment → Edit → Network access, or the VPS firewall)")
        except requests.RequestException as e:
            return _fail(f"{host}: {type(e).__name__}", f"this machine cannot reach {host}; check network / firewall / proxy allow-list")
    return run


def check_apify(cfg) -> Check:
    from .ingest.apify import ApifyClient
    tok = cfg.secret("APIFY_TOKEN")
    ok, detail = ApifyClient(tok).auth_ok()
    if ok:
        return _ok(detail + ("" if tok else " · via the environment's network secret (no variable needed)"))
    if not tok:
        return _skip("APIFY_TOKEN blank and no injected credential", "console.apify.com → Settings → Integrations → copy the token into .env (or add it as a network secret in the cloud environment)")
    if "401" in detail:
        return _fail("token rejected (401)", "regenerate the token in console.apify.com and paste the new one into .env")
    return _fail(detail, "network to api.apify.com is blocked or Apify is down (status.apify.com)")


def check_anthropic(cfg) -> Check:
    key = cfg.secret("ANTHROPIC_API_KEY")
    if not key:
        return _skip("ANTHROPIC_API_KEY blank (rules fallback scores everything)", "console.anthropic.com → API keys → paste into .env")
    try:
        import anthropic
    except Exception as e:  # noqa: BLE001
        return _fail(f"anthropic SDK missing: {e}", "make setup")
    model = cfg.get("scoring.model")
    try:
        client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=30.0)
        client.models.retrieve(model)
    except anthropic.AuthenticationError:
        return _fail("key rejected (401)", "create a new key at console.anthropic.com and paste it into .env")
    except anthropic.NotFoundError:
        return _fail(f"model {model} not found", "set scoring.model in config.yaml to a model your account can use")
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {str(e)[:120]}", "check network to api.anthropic.com and the account's billing status")
    return _ok(f"key accepted · model {model}")


def check_gmail(cfg) -> Check:
    oauth = cfg.secret("GMAIL_OAUTH_JSON")
    if not oauth or not cfg.secret("GMAIL_SENDER"):
        return _skip("GMAIL_OAUTH_JSON / GMAIL_SENDER blank", "download the OAuth client JSON (Desktop app) to secrets/ and set GMAIL_SENDER in .env")
    if not (cfg.root / oauth).exists():
        return _fail(f"{oauth} not found", "save the OAuth client JSON from Google Cloud Console at that path")
    token = cfg.root / (cfg.secret("GMAIL_TOKEN_JSON") or "secrets/gmail_token.json")
    if not token.exists():
        return _fail("no token yet", "run `make gmail-auth` once on a machine with a browser (sign in as the outreach mailbox)")
    try:
        from .outreach.gmail_client import GmailClient, GmailUnavailable
        g = GmailClient(cfg.root / oauth, token, cfg.secret("GMAIL_SENDER"), cfg.get("operator.sender_name"), interactive=False)
        prof = g.svc.users().getProfile(userId="me").execute()
    except GmailUnavailable as e:
        return _fail(str(e), "run `make gmail-auth` again")
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {str(e)[:120]}", "token may be revoked; delete secrets/gmail_token.json and run `make gmail-auth`")
    addr = prof.get("emailAddress", "")
    if addr.lower() != (cfg.secret("GMAIL_SENDER") or "").lower():
        return _fail(f"token is for {addr}, .env says {cfg.secret('GMAIL_SENDER')}", "re-run `make gmail-auth` and sign in as the outreach mailbox, or fix GMAIL_SENDER")
    return _ok(f"signed in as {addr}")


def check_twilio(cfg) -> Check:
    if not cfg.has("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_MESSAGING_SERVICE_SID"):
        return _skip("TWILIO_* blank", "console.twilio.com → Account info + Messaging → Services → paste the three values into .env")
    try:
        from twilio.rest import Client
        c = Client(cfg.secret("TWILIO_ACCOUNT_SID"), cfg.secret("TWILIO_AUTH_TOKEN"))
        acct = c.api.accounts(cfg.secret("TWILIO_ACCOUNT_SID")).fetch()
        svc = c.messaging.v1.services(cfg.secret("TWILIO_MESSAGING_SERVICE_SID")).fetch()
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "20003" in msg or "Authenticate" in msg:
            return _fail("credentials rejected", "check TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN in .env")
        if "20404" in msg:
            return _fail("Messaging Service SID not found", "copy the MG… SID from Messaging → Services")
        return _fail(f"{type(e).__name__}: {msg[:120]}", "check network to api.twilio.com")
    want = (cfg.secret("WEBHOOK_PUBLIC_URL") or "").rstrip("/") + "/twilio/inbound"
    inbound = getattr(svc, "inbound_request_url", None) or ""
    if cfg.secret("WEBHOOK_PUBLIC_URL") and inbound != want:
        return _warn(f"account {acct.status} · service ok · inbound URL is {inbound or 'unset'}",
                     f"in Twilio → Messaging → Services → Integration set the request URL to {want}")
    return _ok(f"account {acct.status} · service {svc.friendly_name}")


def check_webhook(cfg) -> Check:
    url = cfg.secret("WEBHOOK_PUBLIC_URL")
    if not url:
        return _skip("WEBHOOK_PUBLIC_URL blank", "set it once the server has a public https address (deploy/DEPLOY.md part B)")
    if not url.startswith("https://"):
        return _warn(f"{url} is not https", "put nginx + certbot in front (deploy/nginx-titan.conf); Twilio accepts http only for a first test")
    import requests
    try:
        r = requests.get(url.rstrip("/") + "/health", timeout=15)
        if r.status_code == 200 and r.json().get("ok"):
            return _ok(f"{url}/health answers · mode {r.json().get('mode')}")
        return _fail(f"/health returned {r.status_code}", "is titan-webhook.service running? `journalctl -u titan-webhook -n 50`")
    except Exception as e:  # noqa: BLE001
        return _fail(f"unreachable: {type(e).__name__}", "DNS, security group (443) or nginx; see deploy/DEPLOY.md part B step 3")


CHECKS: list[tuple[str, Callable[[Any], Check]]] = [
    (".env", check_env_file),
    ("config operator", check_operator),
    ("outreach mode", check_mode),
    ("reach api.apify.com", check_reach("api.apify.com", "/v2/users/me")),
    ("reach api.anthropic.com", check_reach("api.anthropic.com", "/v1/models")),
    ("Apify token", check_apify),
    ("Anthropic key", check_anthropic),
    ("Gmail", check_gmail),
    ("Twilio", check_twilio),
    ("webhook", check_webhook),
]


def run(cfg, checks: list[tuple[str, Callable[[Any], Check]]] | None = None) -> list[dict[str, str]]:
    rows = []
    for name, fn in (checks or CHECKS):
        try:
            status, detail, fix = fn(cfg)
        except Exception as e:  # noqa: BLE001 — a crashing check is itself a finding
            status, detail, fix = "fail", f"check crashed: {type(e).__name__}: {str(e)[:120]}", "report this"
        rows.append({"check": name, "status": status, "detail": detail, "fix": fix})
    return rows


ICON = {"ok": "✓", "warn": "!", "fail": "✗", "skip": "–"}


def render(rows: list[dict[str, str]]) -> str:
    w = max(len(r["check"]) for r in rows)
    out = []
    for r in rows:
        out.append(f"{ICON[r['status']]} {r['check']:<{w}}  {r['status']:<4}  {r['detail']}")
        if r["fix"]:
            out.append(f"  {'':<{w}}        → {r['fix']}")
    n = {s: sum(1 for r in rows if r["status"] == s) for s in ICON}
    out.append("")
    out.append(f"{n['ok']} ok · {n['warn']} warn · {n['fail']} fail · {n['skip']} not configured")
    if n["fail"] == 0 and n["skip"] == 0 and n["warn"] == 0:
        out.append("ready for a live run: make run-daily")
    return "\n".join(out)
