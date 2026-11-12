"""Gmail API wrapper: drafts (with label), send, read threads. Plain-text only."""
from __future__ import annotations

import base64
import json
import re
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any

from ..util import log, retry


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
              cc: list[str] | None = None, html: str | None = None, attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        msg = EmailMessage()
        msg["To"] = to
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["From"] = formataddr((self.sender_name, self.sender))
        msg["Subject"] = subject
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = references or in_reply_to
        msg.set_content(body)   # text/plain (what agents get, and what the cloud session reads)
        if html:                # internal mail only (the morning report): a designed HTML version next to the text
            msg.add_alternative(html, subtype="html")
        for a in attachments or []:   # forwarded broker files (the books, an NDA) go along as real attachments
            mime = (a.get("mime") or "application/octet-stream").split(";")[0].strip() or "application/octet-stream"
            main, _, sub = mime.partition("/")
            msg.add_attachment(a.get("bytes") or b"", maintype=main or "application", subtype=sub or "octet-stream",
                               filename=a.get("filename") or "attachment")
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
             cc: list[str] | None = None, html: str | None = None, attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        # Rodrigo, 2026-11-12 (Eric Cederman): an email to an outside address that says a file is attached or signed, with
        # nothing attached, never leaves. No override: not "send it", not autopilot, not a fixed reply.
        if not _own_domain(to, self.sender):
            from ..claims import UnbackedClaim, unbacked_claims
            bad = unbacked_claims(body, attachments)
            if bad:
                raise UnbackedClaim(bad)
        message = self._mime(to, subject, body, in_reply_to, cc=cc, html=html, attachments=attachments)
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

    def archive_thread(self, thread_id: str) -> None:
        """Take a whole thread out of the inbox (Rodrigo, 2026-11-09: brokers that do not fit are archived so nobody has to look
        at them). The thread is still read by id, so a later answer from that broker is not lost."""
        retry(lambda: self.svc.users().threads().modify(userId="me", id=thread_id, body={"removeLabelIds": ["INBOX", "UNREAD"]}).execute(),
              what="gmail archive")

    # --- read ----------------------------------------------------------------------------------
    def thread_messages(self, thread_id: str) -> list[dict[str, Any]]:
        t = retry(lambda: self.svc.users().threads().get(userId="me", id=thread_id, format="full").execute(), what="gmail thread")
        out = []
        for m in t.get("messages", []):
            headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
            out.append({"id": m["id"], "thread_id": thread_id, "from": headers.get("from", ""), "to": headers.get("to", ""),
                        "subject": headers.get("subject", ""), "message_id_header": headers.get("message-id"),
                        "date": headers.get("date"), "internal_ts": int(m.get("internalDate", 0)) // 1000,
                        "body": _plain_body(m.get("payload", {})), "label_ids": m.get("labelIds", []),
                        "attachments": _attachment_parts(m.get("payload", {}))})
        return out

    def attachments(self, message_id: str) -> list[dict[str, Any]]:
        """Every file attached to one message: [{filename, mime, bytes}]. Used to file the books a broker sends (2026-11-09)."""
        m = retry(lambda: self.svc.users().messages().get(userId="me", id=message_id, format="full").execute(), what="gmail message")
        out = []
        for part in _attachment_parts(m.get("payload", {})):
            if part.get("attachment_id"):
                a = retry(lambda: self.svc.users().messages().attachments().get(userId="me", messageId=message_id, id=part["attachment_id"]).execute(),
                          what="gmail attachment")
                data = base64.urlsafe_b64decode(a.get("data", "") + "==")
            else:
                data = base64.urlsafe_b64decode(part.get("inline_data", "") + "==")
            out.append({"filename": part["filename"], "mime": part.get("mime"), "bytes": data})
        return out

    def search(self, q: str, max_results: int = 50) -> list[str]:
        res = retry(lambda: self.svc.users().messages().list(userId="me", q=q, maxResults=max_results).execute(), what="gmail search")
        return [m["id"] for m in res.get("messages", [])]

    def search_refs(self, q: str, max_results: int = 500) -> list[dict[str, str]]:
        """Message ids AND their thread ids for a query, paged (2026-11-11: the stray-answer sweep needs the thread to skip known ones)."""
        out: list[dict[str, str]] = []
        token = None
        while len(out) < max_results:
            res = retry(lambda: self.svc.users().messages().list(userId="me", q=q, maxResults=min(500, max_results - len(out)),
                                                                 pageToken=token).execute(), what="gmail search")
            out += [{"id": m["id"], "thread_id": m.get("threadId")} for m in res.get("messages", [])]
            token = res.get("nextPageToken")
            if not token:
                break
        return out

    def message(self, message_id: str) -> dict[str, Any]:
        m = retry(lambda: self.svc.users().messages().get(userId="me", id=message_id, format="full").execute(), what="gmail message")
        headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
        return {"id": m["id"], "thread_id": m.get("threadId"), "from": headers.get("from", ""), "to": headers.get("to", ""),
                "cc": headers.get("cc", ""), "subject": headers.get("subject", ""), "message_id_header": headers.get("message-id"),
                "date": headers.get("date"), "internal_ts": int(m.get("internalDate", 0)) // 1000,
                "body": _plain_body(m.get("payload", {})), "label_ids": m.get("labelIds", []),
                "attachments": _attachment_parts(m.get("payload", {}))}


def _own_domain(to: str, sender: str) -> bool:
    """Every recipient is on our own domain (reports and forwards to Rodrigo)."""
    dom = (sender or "").rpartition("@")[2].lower()
    addrs = re.findall(r"[\w.+-]+@([\w.-]+)", to or "")
    return bool(dom) and bool(addrs) and all(a.lower() == dom for a in addrs)


def _attachment_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The file parts of a message (name, mime, attachment id), without downloading them."""
    out: list[dict[str, Any]] = []

    def name_of(p):
        # Outlook sometimes leaves Gmail's filename empty and names the file only in the part headers (2026-11-11, Cameron
        # Cauley's OM / lease / income statement never reached the engine although he attached them twice)
        if p.get("filename"):
            return p["filename"]
        import re
        for h in p.get("headers", []) or []:
            if h.get("name", "").lower() in ("content-disposition", "content-type"):
                m = re.search(r'(?:file)?name\*?=(?:UTF-8\'\')?"?([^";]+)', h.get("value", ""), re.I)
                if m and (p.get("body", {}) or {}).get("attachmentId"):
                    return m.group(1).strip()
        return ""

    def walk(p):
        if name_of(p):
            body = p.get("body", {}) or {}
            out.append({"filename": name_of(p), "mime": p.get("mimeType"), "size": body.get("size"),
                        "attachment_id": body.get("attachmentId"), "inline_data": body.get("data")})
        for part in p.get("parts", []) or []:
            walk(part)
    walk(payload or {})
    return out


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
