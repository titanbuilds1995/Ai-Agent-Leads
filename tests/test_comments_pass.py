"""Rodrigo, 2026-11-23: "I need it to be read faster so I can send out more offers, faster." His comments are read every
5 minutes by a pass of their own; that pass never sends and never runs next to a full approvals round."""
import json
from types import SimpleNamespace

from titan import approvals, cli


def _patch(monkeypatch, cfg, tmp_path, calls):
    cfg.root = tmp_path
    cfg["commercial"]["enabled"] = False
    monkeypatch.setattr(cli, "_open", lambda cfg: SimpleNamespace(commit=lambda: None))
    monkeypatch.setattr(cli, "_asana", lambda cfg: object())
    monkeypatch.setattr(cli, "build_clients", lambda cfg, conn, need=(): {"llm": "llm", "gmail": "gmail", "apify": None})
    monkeypatch.setattr(approvals, "sync_feedback", lambda *a, **k: calls.append("feedback") or {"revised": 1})
    monkeypatch.setattr(approvals, "send_approved", lambda *a, **k: calls.append("send") or {})


def test_the_comments_pass_reads_comments_and_sends_nothing(monkeypatch, cfg, tmp_path, capsys):
    calls = []
    _patch(monkeypatch, cfg, tmp_path, calls)
    cli.cmd_comments(cfg, SimpleNamespace())
    assert calls == ["feedback"]
    assert json.loads(capsys.readouterr().out)["feedback"] == {"revised": 1}


def test_the_comments_pass_steps_aside_while_a_round_is_running(monkeypatch, cfg, tmp_path, capsys):
    calls = []
    _patch(monkeypatch, cfg, tmp_path, calls)
    held = cli._round_lock(cfg)
    assert held is not None
    cli.cmd_comments(cfg, SimpleNamespace())
    assert calls == [] and "skipped" in capsys.readouterr().out
    held.close()
    cli.cmd_comments(cfg, SimpleNamespace())
    assert calls == ["feedback"]


def test_cron_reads_comments_every_five_minutes_into_the_evening(cfg, capsys):
    cli.cmd_install_cron(cfg, SimpleNamespace(dry_run=True))
    lines = capsys.readouterr().out.splitlines()
    assert any(ln.startswith("*/5 8-19 * * *") and "titan.cli comments" in ln for ln in lines)
    assert any(ln.startswith("*/20 8-17 * * *") and "titan.cli approvals" in ln for ln in lines)   # the send pace is unchanged
