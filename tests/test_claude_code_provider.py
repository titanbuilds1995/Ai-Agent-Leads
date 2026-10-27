"""Scoring through `claude -p` on the Claude Code plan (Rodrigo, 2026-10-24: free / already-paid first). Fake CLI only."""
import json
import subprocess

from titan import llm as llm_mod
from titan.llm import LLM
from titan.scoring import BATCH_SCHEMA, run_scoring


class Proc:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _cc(cfg, monkeypatch, handler):
    monkeypatch.setattr(llm_mod.shutil, "which", lambda name: "/usr/local/bin/claude")
    monkeypatch.setattr(llm_mod.subprocess, "run", handler)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-passed")
    return LLM(cfg, None, provider="claude_code")


def test_claude_code_uses_the_plan_not_the_api_key(cfg, monkeypatch):
    seen = {}

    def run(cmd, input, capture_output, text, cwd, env, timeout):
        seen.update(cmd=cmd, env=env, input=input)
        return Proc(json.dumps({"result": "", "structured_output": {"ok": 1}, "total_cost_usd": 0.01}))
    llm = _cc(cfg, monkeypatch, run)
    assert llm.available and llm.provider == "claude_code"
    assert llm.structured("score_t", "sys", "hello", {"type": "object"}) == {"ok": 1}
    assert "ANTHROPIC_API_KEY" not in seen["env"]                      # otherwise -p bills the API key
    c = seen["cmd"]
    assert c[1] == "-p" and c[0].endswith("claude") and "--json-schema" in c and c[c.index("--tools") + 1] == "" and "--no-session-persistence" in c
    assert seen["input"] == "hello" and llm.session_cost == 0.0


def test_plan_limit_stops_calls_without_errors_flood(cfg, monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(1)
        return Proc(json.dumps({"result": "You've hit your session limit · resets 3pm", "is_error": True}), returncode=1)
    llm = _cc(cfg, monkeypatch, run)
    assert llm.structured("score_t", "s", "u", {}) is None
    assert llm.limit_hit and not llm.available and llm.structured("score_t", "s", "u", {}) is None and len(calls) == 1


def test_missing_cli_never_falls_back_to_paid_api_unless_allowed(cfg, monkeypatch):
    monkeypatch.delenv("TITAN_LLM", raising=False)
    monkeypatch.setattr(llm_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(llm_mod, "CLAUDE_CLI_PLACES", [])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert LLM(cfg, None).provider == "rules"
    monkeypatch.setenv("TITAN_LLM", "rules")


def test_batched_scoring_through_claude_code(loaded, cfg, monkeypatch):
    loaded.execute("UPDATE properties SET days_on_market=90, price_cuts=1")      # everyone is a target
    n = loaded.execute("SELECT COUNT(*) FROM properties WHERE lead_status NOT IN ('CLOSED','DNC','EXCLUDED')").fetchone()[0]
    cfg.raw["scoring"]["claude_code_batch_size"] = 5
    batches = []

    def run(cmd, input, **kw):
        listings = json.loads(input)
        batches.append(len(listings))
        schema = json.loads(cmd[cmd.index("--json-schema") + 1])
        assert schema == BATCH_SCHEMA
        return Proc(json.dumps({"structured_output": {"results": [
            {"id": l["id"], "score": 72, "cited": [{"signal": "needs_work", "phrase": "needs work"}], "negatives": [], "summary": "s"}
            for l in listings]}}))
    llm = _cc(cfg, monkeypatch, run)
    out = run_scoring(loaded, cfg, llm, force=True)
    assert sum(batches) == out["claude"] >= 5 and max(batches) <= 5 and len(batches) == -(-sum(batches) // 5)
    assert out["llm_cost_usd"] == 0 and out["claude"] + out["rules"] == out["scored"]
    assert loaded.execute("SELECT model FROM scores WHERE method='claude' LIMIT 1").fetchone()[0] == "claude_code:sonnet"


def test_refused_token_falls_back_to_the_mac_login(cfg, monkeypatch):
    """Rodrigo, 2026-10-26: 'token refused' kept coming back. A bad token no longer stops a rewrite: the Mac's own
    Claude login is tried next, and the one that works goes first for the rest of the run."""
    monkeypatch.setitem(cfg.secrets, "CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-bad")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-stale-shell")
    seen = []

    def run(cmd, input, capture_output, text, cwd, env, timeout):
        tok = env.get("CLAUDE_CODE_OAUTH_TOKEN")
        seen.append(tok)
        if tok:
            return Proc(json.dumps({"result": 'API Error: 401 {"type":"authentication_error","message":"Invalid bearer token"}',
                                    "is_error": True}), returncode=1)
        return Proc(json.dumps({"result": "", "structured_output": {"ok": 1}}))
    llm = _cc(cfg, monkeypatch, run)
    assert llm.structured("score_t", "s", "u", {}) == {"ok": 1}
    assert seen == ["sk-ant-oat01-bad", None] and llm.login_used == "mac_login"
    assert llm.structured("score_t", "s", "u", {}) == {"ok": 1} and seen[-1] is None and len(seen) == 3   # no wasted retry


def test_both_logins_refused_reports_both(cfg, monkeypatch):
    from titan.llm import login_problem
    monkeypatch.setitem(cfg.secrets, "CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-bad")

    def run(cmd, **kw):
        return Proc(json.dumps({"result": "Not logged in · Please run /login", "is_error": True}), returncode=1)
    llm = _cc(cfg, monkeypatch, run)
    assert llm.structured("score_t", "s", "u", {}) is None
    assert "token:" in llm.last_error and "mac_login:" in llm.last_error
    assert "both Claude logins" in login_problem(cfg, llm.last_error)


def test_timeout_error_does_not_carry_the_prompt(cfg, monkeypatch):
    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 600)
    llm = _cc(cfg, monkeypatch, run)
    assert llm.structured("score_t", "secret system prompt 401 oauth token", "u", {}) is None
    assert "prompt" not in llm.last_error and "TimeoutExpired" in llm.last_error


def test_every_login_refused_falls_back_to_the_paid_api_only_when_switched_on(cfg, monkeypatch):
    """Rodrigo, 2026-10-27: the Mac login keeps breaking; with the switch on, card work goes to the paid API instead of stalling."""
    def run(cmd, **kw):
        return Proc(json.dumps({"result": "API Error: 401 OAuth access token is invalid", "is_error": True}), returncode=1)
    cfg.raw["scoring"]["paid_fallback_when_login_fails"] = False
    cfg.raw["scoring"]["communication_provider"] = "claude_code"
    llm = _cc(cfg, monkeypatch, run)
    assert llm.structured("revise_email", "s", "u", {}) is None and llm.login_fallbacks == 0
    cfg.raw["scoring"]["paid_fallback_when_login_fails"] = True
    llm = _cc(cfg, monkeypatch, run)
    seen = {}
    llm._api = lambda purpose, system, user, schema, max_tokens=1024: seen.update(purpose=purpose) or {"ok": 1}
    assert llm.structured("revise_email", "s", "u", {}) == {"ok": 1} and seen["purpose"] == "revise_email" and llm.login_fallbacks == 1
    assert llm.structured("score_batch", "s", "u", {}) is None            # scoring never spends money on this path


def test_communication_runs_on_the_api_scoring_on_the_plan(cfg, monkeypatch):
    """Rodrigo, 2026-10-27: 'let's have it run on the API' for the communication part; the Mac keeps the heavy lifting."""
    calls = []

    def run(cmd, **kw):
        calls.append(1)
        return Proc(json.dumps({"result": "", "structured_output": {"scored": 1}}))
    cfg.raw["scoring"]["communication_provider"] = "api"
    llm = _cc(cfg, monkeypatch, run)
    llm._api = lambda purpose, system, user, schema, max_tokens=1024: {"api": purpose}
    assert llm.structured("revise_email", "s", "u", {}) == {"api": "revise_email"} and not calls
    assert llm.structured("draft_reply", "s", "u", {}) == {"api": "draft_reply"} and not calls
    assert llm.structured("score_batch", "s", "u", {}) == {"scored": 1} and len(calls) == 1
