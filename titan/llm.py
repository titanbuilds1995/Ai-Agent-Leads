"""The only module that talks to the Claude API.

- structured JSON output via output_config.format (json_schema)
- retry wrapper on retryable errors
- token + cost counter persisted to llm_usage
- a deterministic 'rules' provider used when no key is configured, when the API fails, or in tests
"""
from __future__ import annotations

import json
import os
from typing import Any

from .util import log, retry

try:  # the SDK is optional at import time so tests run without it installed
    import anthropic
except Exception:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]


class LLMUnavailable(Exception):
    pass


class LLM:
    def __init__(self, cfg: Any, conn=None, provider: str | None = None):
        self.cfg = cfg
        self.conn = conn
        self.model = cfg.get("scoring.model", "claude-sonnet-5-5")
        self.effort = cfg.get("scoring.effort", "low")
        self.pricing = cfg.get("llm_pricing_per_mtok", {})
        key = cfg.secret("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        forced = provider or os.environ.get("TITAN_LLM")
        if forced:
            self.provider = forced
        else:
            self.provider = "claude" if (key and anthropic is not None) else "rules"
        self._client = None
        if self.provider == "claude":
            if anthropic is None or not key:
                raise LLMUnavailable("anthropic SDK or ANTHROPIC_API_KEY missing")
            self._client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=120.0)
        self.session_cost = 0.0
        self.session_calls = 0
        self.session_failures = 0
        self.session_successes = 0
        self.last_error: str | None = None

    @property
    def available(self) -> bool:
        return self.provider == "claude"

    def _cost(self, model: str, inp: int, out: int) -> float:
        p = self.pricing.get(model) or {"input": 3.0, "output": 15.0}
        return (inp * p["input"] + out * p["output"]) / 1_000_000

    def structured(self, purpose: str, system: str, user: str, schema: dict[str, Any], max_tokens: int = 1024) -> dict[str, Any] | None:
        """Return a dict matching schema, or None when the API is unavailable/refused (caller falls back to rules)."""
        if not self.available:
            return None
        assert self._client is not None
        a = anthropic

        def call():
            return self._client.messages.create(
                model=self.model, max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
            )

        try:
            resp = retry(call, attempts=4, base=2.0,
                         retry_on=(a.RateLimitError, a.InternalServerError, a.APIConnectionError, a.APITimeoutError),
                         what=f"claude:{purpose}")
        except a.AuthenticationError as e:
            self._record(purpose, 0, 0, ok=False)
            raise LLMUnavailable(f"authentication failed: {e.message}") from e
        except Exception as e:  # noqa: BLE001
            log.error("claude:%s failed: %s", purpose, e)
            self.session_failures += 1
            self.last_error = f"{type(e).__name__}: {getattr(e, 'message', e)}"[:300]
            self._record(purpose, 0, 0, ok=False)
            return None
        usage = getattr(resp, "usage", None)
        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        self.session_successes += 1
        self._record(purpose, inp, out, ok=True, model=resp.model)
        if resp.stop_reason == "refusal":
            log.warning("claude:%s refused (%s)", purpose, getattr(getattr(resp, "stop_details", None), "category", None))
            return None
        if resp.stop_reason == "max_tokens":
            log.warning("claude:%s hit max_tokens", purpose)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            log.error("claude:%s returned non-JSON", purpose)
            return None

    def _record(self, purpose: str, inp: int, out: int, ok: bool, model: str | None = None) -> None:
        model = model or self.model
        cost = self._cost(model, inp, out)
        self.session_cost += cost
        self.session_calls += 1
        if self.conn is not None:
            from . import db
            db.record_usage(self.conn, model, purpose, inp, out, cost, ok)
