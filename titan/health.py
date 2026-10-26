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
