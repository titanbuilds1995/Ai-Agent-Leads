"""Twilio Messaging Service wrapper + webhook signature validation."""
from __future__ import annotations

from typing import Any

from ..util import log, retry


class TwilioUnavailable(Exception):
    pass


class TwilioClient:
    def __init__(self, account_sid: str, auth_token: str, messaging_service_sid: str, status_callback: str | None = None):
        try:
            from twilio.rest import Client
        except ImportError as e:  # pragma: no cover
            raise TwilioUnavailable(str(e)) from e
        if not (account_sid and auth_token and messaging_service_sid):
            raise TwilioUnavailable("TWILIO_* credentials missing")
        self._client = Client(account_sid, auth_token)
        self._auth_token = auth_token
        self.mss = messaging_service_sid
        self.status_callback = status_callback

    def send(self, to: str, body: str) -> dict[str, Any]:
        kw = {"messaging_service_sid": self.mss, "to": to, "body": body}
        if self.status_callback:
            kw["status_callback"] = self.status_callback
        msg = retry(lambda: self._client.messages.create(**kw), what="twilio send")
        return {"sid": msg.sid, "status": msg.status}

    def validate(self, url: str, params: dict[str, str], signature: str) -> bool:
        from twilio.request_validator import RequestValidator
        return RequestValidator(self._auth_token).validate(url, params, signature)
