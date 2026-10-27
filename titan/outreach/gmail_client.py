"""Gmail API wrapper: drafts (with label), send, read threads. Every message goes out as text + the same email in HTML
(`email_html.body_html`), so it reads like a normal email in Gmail instead of a narrow plain-text column."""
from __future__ import annotations

import base64
import json
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any

from ..util import log, retry
from .email_html import body_html


class _RetryableSendError(Exception):
    """Placeholder: with a thread id we do not retry at all on the first pass, so a 404 falls straight to the fallback."""


def _is_not_found(e: BaseException) -> bool:
    status = getattr(getattr(e, "resp", None), "status", None) or getattr(e, "status_code", None)
    return status == 404 or "404" in str(e)[:80] or "Requested entity was not found" in str(e)

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class GmailUnavailable(Exception):
    pass


class GmailClient:
    def __init__(self, oauth_json: str | Path, token_json: str | Path, sender: str, sender_name: str, interactive: bool = False):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError as e:  # pragma: no cover
            raise GmailUnavailable(f"google libraries missing: {e}") from e
        self.sender, self.sender_name = sender, sender_name
        token_path = Path(token_json)
        creds = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                token_path.write_text(creds.to_json())
            except Exception:  # revoked, or 7-day expiry while the Google Cloud app is in "Testing": sign in again
                creds = None
        if not creds or not creds.valid:
            if not interactive:
                raise GmailUnavailable("Gmail token missing or invalid; run `make gmail-auth` once on a machine with a browser")
            from google_auth_oauthlib.flow import InstalledAppFlow
            if not Path(oauth_json).exists():
                raise GmailUnavailable(f"GMAIL_OAUTH_JSON not found at {oauth_json}")
            flow = InstalledAppFlow.from_client_secrets_file(str(oauth_json), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=False)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json())
        self.svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self._labels: dict[str, str] = {}
        # Rodrigo, 2026-10-26: an email went to an agent from his own mailbox with Charles's signature because the token on
        # disk had been re-authorised as the wrong Google account. Never again: the logged-in mailbox must BE the sender.
        try:
            self.account = (self.svc.users().getProfile(userId="me").execute().get("emailAddress") or "").lower()
        except Exception as e:  # noqa: BLE001
            raise GmailUnavailable(f"could not read the Gmail profile: {e}") from e
        if sender and self.account != sender.lower():
            raise GmailUnavailable(f"Gmail is logged in as {self.account}, not {sender}. Nothing is sent from the wrong mailbox. "
                                   f"Fix: rm {token_path} && make gmail-auth, and sign in as {sender}.")

    # --- labels ---------------------------------------------------------------------------
    def label_id(self, name: str) -> str:
        if name in self._labels:
            return self._labels[name]
        labels = self.svc.users().labels().list(userId="me").execute().get("labels", [])
        for l in labels:
            self._labels[l["name"]] = l["id"]
        if name not in self._labels:
            created = self.svc.users().labels().create(userId="me", body={"name": name, "labelListVisibility": "labelShow",
                                                                            "messageListVisibility": "show"}).execute()
            self._labels[name] = created["id"]
        return self._labels[name]

    # --- compose ----------------------------------------------------------------------------
    def _mime(self, to: str, subject: str, body: str, in_reply_to: str | None = None, references: str | None = None,
              cc: list[str] | None = None, html: str | None = None) -> dict[str, Any]:
        msg = EmailMessage()
        msg["To"] = to
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["From"] = formataddr((self.sender_name, self.sender))
        msg["Subject"] = subject
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = references or in_reply_to
        msg.set_content(body)   # text/plain first (what the cloud session and reply readers parse)
        # Rodrigo, 2026-10-27: the same email in HTML next to the text, like Gmail's composer does, so agents see normal
        # paragraphs and a bulleted terms list instead of Gmail's narrow plain-text rendering. The morning report passes
        # its own designed HTML.
        msg.add_alternative(html or body_html(body), subtype="html")
        return {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}

    def create_draft(self, to: str, subject: str, body: str, label: str | None = None, thread_id: str | None = None,
                     in_reply_to: str | None = None) -> dict[str, Any]:
        message = self._mime(to, subject, body, in_reply_to)
        if thread_id:
            message["threadId"] = thread_id
        draft = retry(lambda: self.svc.users().drafts().create(userId="me", body={"message": message}).execute(), what="gmail draft")
        if label:
            mid = draft["message"]["id"]
            retry(lambda: self.svc.users().messages().modify(userId="me", id=mid, body={"addLabelIds": [self.label_id(label)]}).execute(), what="gmail label")
        return {"draft_id": draft["id"], "message_id": draft["message"]["id"], "thread_id": draft["message"].get("threadId")}

    def send(self, to: str, subject: str, body: str, thread_id: str | None = None, in_reply_to: str | None = None,
             cc: list[str] | None = None, html: str | None = None) -> dict[str, Any]:
        message = self._mime(to, subject, body, in_reply_to, cc=cc, html=html)
        if thread_id:
            message["threadId"] = thread_id
        try:
            sent = retry(lambda: self.svc.users().messages().send(userId="me", body=message).execute(), what="gmail send",
                         retry_on=(_RetryableSendError,) if thread_id else (Exception,))
        except _RetryableSendError:
            raise
        except Exception as e:  # noqa: BLE001
            if thread_id and _is_not_found(e):
                # the stored thread id is not in this mailbox (stale or from another account): send it as a fresh message
                # with the same subject rather than never answering; the agent's mail client threads by subject anyway
                log.warning("gmail send: thread %s not found in this mailbox, sending without threadId", thread_id)
                message.pop("threadId", None)
                sent = retry(lambda: self.svc.users().messages().send(userId="me", body=message).execute(), what="gmail send")
            else:
                raise
        return {"message_id": sent["id"], "thread_id": sent.get("threadId")}

    # --- read ----------------------------------------------------------------------------------
    def thread_messages(self, thread_id: str) -> list[dict[str, Any]]:
        t = retry(lambda: self.svc.users().threads().get(userId="me", id=thread_id, format="full").execute(), what="gmail thread")
        out = []
        for m in t.get("messages", []):
            headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
            out.append({"id": m["id"], "thread_id": thread_id, "from": headers.get("from", ""), "to": headers.get("to", ""),
                        "subject": headers.get("subject", ""), "message_id_header": headers.get("message-id"),
                        "date": headers.get("date"), "internal_ts": int(m.get("internalDate", 0)) // 1000,
                        "body": _plain_body(m.get("payload", {})), "label_ids": m.get("labelIds", [])})
        return out

    def search(self, q: str, max_results: int = 50) -> list[str]:
        res = retry(lambda: self.svc.users().messages().list(userId="me", q=q, maxResults=max_results).execute(), what="gmail search")
        return [m["id"] for m in res.get("messages", [])]

    def message(self, message_id: str) -> dict[str, Any]:
        m = retry(lambda: self.svc.users().messages().get(userId="me", id=message_id, format="full").execute(), what="gmail message")
        headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
        return {"id": m["id"], "thread_id": m.get("threadId"), "from": headers.get("from", ""), "subject": headers.get("subject", ""),
                "message_id_header": headers.get("message-id"), "internal_ts": int(m.get("internalDate", 0)) // 1000,
                "body": _plain_body(m.get("payload", {})), "label_ids": m.get("labelIds", [])}


def _plain_body(payload: dict[str, Any]) -> str:
    def walk(p):
        if p.get("mimeType") == "text/plain" and p.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(p["body"]["data"] + "==").decode("utf-8", "replace")
        for part in p.get("parts", []) or []:
            t = walk(part)
            if t:
                return t
        if p.get("mimeType") == "text/html" and p.get("body", {}).get("data"):
            import re
            html = base64.urlsafe_b64decode(p["body"]["data"] + "==").decode("utf-8", "replace")
            return re.sub(r"<[^>]+>", " ", html)
        return ""
    return strip_quoted(walk(payload) or "")


def strip_quoted(text: str) -> str:
    """Drop quoted history ('On ... wrote:' even when Gmail wraps it over two lines, '>' lines, signature separators)."""
    import re
    text = text or ""
    m = re.search(r"(?:^|\n)\s*On\s[^\n]{3,200}(?:\n[^\n]{0,120})?\bwrote:\s*(?:\n|$)", text)
    if m:
        text = text[:m.start()]
    lines = []
    for line in text.splitlines():
        if line.strip() == "-- " or re.match(r"^-{3,}\s*Original Message", line, re.I):
            break
        if line.lstrip().startswith(">"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()
