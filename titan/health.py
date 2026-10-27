"""Alarms Rodrigo sees on Asana when the engine can't do its job on its own (today: Claude logged out for cron)."""
from __future__ import annotations

import logging
from typing import Any

from .util import today_et

log = logging.getLogger(__name__)


def alert_claude_login(cfg: Any, asana: Any, problem: str) -> bool:
    """One card per day in Claude Updates → Needs Rodrigo when cron can't reach Claude. Per-card comments alone were
    easy to miss and didn't say what to do."""
    a = cfg.get("outreach.asana", {}) or {}
    project, section = a.get("updates_project"), a.get("needs_rodrigo")
    if not (asana and project and section):
        return False
    mark = cfg.root / "logs" / "claude_login_alert.txt"
    day = today_et()
    try:
        if mark.read_text().strip() == day:
            return False
    except OSError:
        pass
    try:
        asana.create_task(project, section, "⚠ Claude is logged out on the Mac: card rewrites are stuck",
                          f"{problem}\n\nWhat this breaks: your comments on Email Approvals cards can't be applied (cards wait in Needs "
                          "Revision), reply cards aren't drafted, scoring falls back to rules.\n\nWhy it happens: the background jobs "
                          "(cron) try two logins, the token saved in Ai-Agent-Leads/.env and then the Mac's own Claude login, and "
                          "both were refused this time.\n\nFix (once, at the Mac, in Terminal):\n"
                          "  cd ~/Ai-Agent-Leads && make claude-login\n\nIt opens the Claude login, asks you to paste the token it "
                          "prints, saves it in .env and tests it the same way cron runs. The stuck cards rewrite on the next round.")
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(day)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("could not post the Claude login alert: %s", e)
        return False


def code_behind(root) -> str | None:
    """Why the Mac is not on the latest code, or None when it is. The cron self-update (`git pull --ff-only` every 10 min)
    fails silently into logs/cron.log; on 2026-10-27 an untracked report file blocked it all morning and every fix that day
    reached the Mac hours late. Checks: commits behind origin, and the last pull error in the log."""
    import subprocess
    try:
        subprocess.run(["git", "fetch", "-q"], cwd=root, capture_output=True, text=True, timeout=60)
        behind = subprocess.run(["git", "rev-list", "--count", "HEAD..@{u}"], cwd=root, capture_output=True, text=True, timeout=30)
        n = int((behind.stdout or "0").strip() or 0)
    except Exception as e:  # noqa: BLE001
        return f"couldn't check git ({str(e)[:80]})"
    if n == 0:
        return None
    reason = ""
    try:
        tail = (root / "logs" / "cron.log").read_text(errors="replace").splitlines()[-200:]
        errs = [ln for ln in tail if ln.startswith(("error:", "fatal:", "\t")) or "would be overwritten" in ln or "Aborting" in ln]
        reason = "\n".join(errs[-6:])
    except OSError:
        pass
    return f"{n} commit(s) behind the latest code" + (f". Last pull error:\n{reason}" if reason else "")


def alert_code_behind(cfg: Any, asana: Any, why: str) -> bool:
    """One card per day in Claude Updates → Needs Rodrigo when the Mac's self-update is stuck."""
    a = cfg.get("outreach.asana", {}) or {}
    project, section = a.get("updates_project"), a.get("needs_rodrigo")
    if not (asana and project and section):
        return False
    mark = cfg.root / "logs" / "code_behind_alert.txt"
    day = today_et()
    try:
        if mark.read_text().strip() == day:
            return False
    except OSError:
        pass
    try:
        asana.create_task(project, section, "⚠ The Mac is running old code: its self-update is stuck",
                          f"{why}\n\nWhat this breaks: every fix Claude pushes (card rules, sending, answers) does not reach the Mac, so "
                          "the board keeps behaving the old way.\n\nFix (at the Mac, in Terminal):\n"
                          "  cd ~/Ai-Agent-Leads && git stash -u && git pull && git stash pop\n\n"
                          "If it still fails, paste the error to Claude.")
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(day)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("could not post the code-behind alert: %s", e)
        return False
