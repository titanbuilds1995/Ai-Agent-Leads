"""FastAPI app for Twilio inbound SMS + delivery status. Run: `make webhook` (uvicorn), expose with ngrok or the VPS IP."""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Form, Header, Request, Response

from . import config, db
from .llm import LLM, LLMUnavailable
from .replies.inbox import inbound_sms
from .util import log, setup_logging


def build_app(cfg=None, conn=None, twilio=None, gmail=None, llm=None, validate: bool = True) -> FastAPI:
    cfg = cfg or config.load()
    setup_logging(cfg.path("logs"))
    app = FastAPI(title="Titan lead engine webhook")
    state: dict[str, Any] = {"conn": conn, "twilio": twilio, "gmail": gmail, "llm": llm}

    def _conn():
        if state["conn"] is None:
            state["conn"] = db.connect(cfg.db_path)
            db.init_db(state["conn"])
        return state["conn"]

    def _clients():
        from .cli import build_clients
        if state["twilio"] is None and state["gmail"] is None and state["llm"] is None:
            c = build_clients(cfg, _conn(), need=("twilio", "gmail", "llm"))
            state.update(c)
        return state

    @app.get("/health")
    def health():
        return {"ok": True, "mode": cfg.mode}

    @app.post("/twilio/inbound")
    async def inbound(request: Request, From: str = Form(...), Body: str = Form(""), MessageSid: str = Form(None),
                      x_twilio_signature: str | None = Header(default=None)):
        st = _clients()
        if validate and st.get("twilio") is not None:
            form = dict((await request.form()).items())
            url = (cfg.secret("WEBHOOK_PUBLIC_URL") or "").rstrip("/") + "/twilio/inbound" if cfg.secret("WEBHOOK_PUBLIC_URL") else str(request.url)
            if not st["twilio"].validate(url, form, x_twilio_signature or ""):
                log.warning("twilio signature invalid")
                return Response(status_code=403)
        res = inbound_sms(_conn(), cfg, From, Body, MessageSid, llm=st.get("llm"), gmail=st.get("gmail"), twilio=st.get("twilio"))
        log.info("inbound sms from %s: %s", From[-4:], {k: v for k, v in res.items() if k != "reply"})
        return Response(content="<?xml version='1.0' encoding='UTF-8'?><Response></Response>", media_type="application/xml")

    @app.post("/twilio/status")
    async def status(MessageSid: str = Form(...), MessageStatus: str = Form(...)):
        c = _conn()
        if MessageStatus in ("failed", "undelivered"):
            c.execute("UPDATE outreach SET status='failed', error=? WHERE twilio_sid=?", (MessageStatus, MessageSid))
        return Response(status_code=204)

    return app


app = build_app() if os.environ.get("TITAN_WEBHOOK_AUTOLOAD") else None
