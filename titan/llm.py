"""The only module that talks to Claude.

Providers (config scoring.provider, or env TITAN_LLM):
- claude_code: the `claude -p` CLI on this machine, billed to Rodrigo's Claude Code plan (no API spend). Default.
- api:         the Anthropic API with ANTHROPIC_API_KEY (pay per token).
- rules:       deterministic keyword fallback, used when nothing else is available and in tests.
Rule (Rodrigo, 2026-10-24): always prefer what costs nothing extra over paid API calls.

- structured JSON output via output_config.format (json_schema)
- retry wrapper on retryable errors
- token + cost counter persisted to llm_usage
- a deterministic 'rules' provider used when no key is configured, when the API fails, or in tests
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any

from .util import log, retry

try:  # the SDK is optional at import time so tests run without it installed
    import anthropic
except Exception:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]


class LLMUnavailable(Exception):
    pass


CLAUDE_CLI_PLACES = ["~/.local/bin/claude", "~/.claude/local/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude",
                     "~/.npm-global/bin/claude", "~/.volta/bin/claude"]


def find_claude_cli(name: str = "claude") -> str | None:
    """The Claude Code CLI, on PATH or in the usual install folders (make/cron often run with a short PATH)."""
    hit = shutil.which(name)
    if hit:
        return hit
    for place in CLAUDE_CLI_PLACES:
        p = os.path.expanduser(place)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


class LLM:
    def __init__(self, cfg: Any, conn=None, provider: str | None = None):
        self.cfg = cfg
        self.conn = conn
        self.model = cfg.get("scoring.model", "claude-sonnet-5-5")
        self.effort = cfg.get("scoring.effort", "low")
        self.pricing = cfg.get("llm_pricing_per_mtok", {})
        key = cfg.secret("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        forced = provider or os.environ.get("TITAN_LLM")
        wanted = forced or cfg.get("scoring.provider", "claude_code")
        self.cli = find_claude_cli(cfg.get("scoring.claude_code_cli", "claude"))
        if wanted == "claude_code" and self.cli:
            self.provider = "claude_code"
        elif wanted == "claude_code" and not forced:
            # Claude Code CLI missing on this machine. Paid API only if Rodrigo switched it on (cost rule).
            paid_ok = cfg.get("scoring.allow_paid_api", False)
            self.provider = "claude" if (paid_ok and key and anthropic is not None) else "rules"
        else:
            self.provider = {"api": "claude"}.get(wanted, wanted)
        self.limit_hit: str | None = None
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
        return self.provider in ("claude", "claude_code")

    def _claude_code(self, purpose: str, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        """One `claude -p` call on the Claude Code plan. The API key is removed from the child's environment so the plan,
        not API credit, is used (in -p mode an ANTHROPIC_API_KEY always wins). Runs in an empty folder so no project
        CLAUDE.md or settings leak into the prompt; tools are disabled."""
        cli = self.cli or self.cfg.get("scoring.claude_code_cli", "claude")
        cmd = [cli, "-p", "--model", self.cfg.get("scoring.claude_code_model", "sonnet"), "--output-format", "json",
               "--json-schema", json.dumps(schema), "--tools", "", "--max-turns", "3", "--no-session-persistence",
               "--system-prompt", system]
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        tok = self.cfg.secret("CLAUDE_CODE_OAUTH_TOKEN")
        if tok:   # `claude setup-token` (plan login that works from cron, where the Mac keychain may be locked)
            env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
        try:
            with tempfile.TemporaryDirectory() as tmp:
                proc = subprocess.run(cmd, input=user, capture_output=True, text=True, cwd=tmp, env=env,
                                      timeout=int(self.cfg.get("scoring.claude_code_timeout_s", 600)))
        except (OSError, subprocess.TimeoutExpired) as e:
            return self._cc_fail(purpose, f"{type(e).__name__}: {e}")
        try:
            env_out = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            env_out = {}
        text = f"{env_out.get('result') or ''} {proc.stderr or ''}"
        low = text.lower()
        if "usage limit" in low or "session limit" in low or "weekly limit" in low or "hit your limit" in low:
            self.limit_hit = text.strip()[:300]
            self.provider = "rules"          # stop calling until the next run; the rest are scored by rules
            log.warning("Claude Code plan limit reached: %s", self.limit_hit)
            return self._cc_fail(purpose, self.limit_hit)
        if proc.returncode != 0 or env_out.get("is_error"):
            return self._cc_fail(purpose, text.strip()[:300] or f"exit {proc.returncode}")
        out = env_out.get("structured_output")
        if out is None:
            try:
                out = json.loads(env_out.get("result") or "")
            except (json.JSONDecodeError, TypeError):
                return self._cc_fail(purpose, "no structured output")
        self.session_successes += 1
        self._record(purpose, 0, 0, ok=True, model=f"claude_code:{self.cfg.get('scoring.claude_code_model', 'sonnet')}")
        return out

    def _cc_fail(self, purpose: str, err: str) -> None:
        log.error("claude_code:%s failed: %s", purpose, err)
        self.session_failures += 1
        self.last_error = err
        self._record(purpose, 0, 0, ok=False, model="claude_code")
        return None

    def _cost(self, model: str, inp: int, out: int) -> float:
        if model.startswith("claude_code"):
            return 0.0            # included in the Claude Code plan
        p = self.pricing.get(model) or {"input": 3.0, "output": 15.0}
        return (inp * p["input"] + out * p["output"]) / 1_000_000

    def structured(self, purpose: str, system: str, user: str, schema: dict[str, Any], max_tokens: int = 1024) -> dict[str, Any] | None:
        """Return a dict matching schema, or None when the API is unavailable/refused (caller falls back to rules)."""
        if not self.available:
            return None
        if self.provider == "claude_code":
            return self._claude_code(purpose, system, user, schema)
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
