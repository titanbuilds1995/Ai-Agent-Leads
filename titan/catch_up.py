"""Catch-up when the Mac wakes (Rodrigo, 2026-10-30: "let's just make sure it happens when I turn the computer on").

cron skips any job whose minute passes while the Mac sleeps: the 5:30am run and the 7am report were lost for days at a time.
Every check-replies run (every 15 min while the Mac is on) asks `due()` what was missed today and, if anything, starts
`titan.cli catch-up` in the background: the daily run first, then the morning report. The evening report catches itself up
the same way (end_of_day.due). Nothing here ever sends an agent email: run-daily only drafts cards for Asana."""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from typing import Any

from .util import ET, iso, now_utc

RUN_STEP = "run_daily"      # written by a full run-daily (no --steps)
RETRY_MIN = 60              # a report whose email failed (Gmail down) is tried again at most once an hour
LAST_HOUR = 21              # after 9pm ET wait for the regular morning run


def _today(t: datetime) -> str:
    return t.strftime("%Y-%m-%d")


def _rows(conn, step: str, day: str) -> list[Any]:
    return conn.execute("SELECT started_at, finished_at, status, counts, notes FROM daily_runs WHERE step=? AND run_date=? "
                        "ORDER BY id DESC", (step, day)).fetchall()


def due(conn, cfg: Any, now: datetime | None = None) -> list[str]:
    """What today's schedule should already have done and didn't: [] / ["run_daily", "report"] / ["report"]."""
    t = (now or now_utc()).astimezone(ET)
    day = _today(t)
    if t.hour >= LAST_HOUR:
        return []
    out: list[str] = []
    runs = _rows(conn, RUN_STEP, day)
    if (t.hour, t.minute) >= (5, 30):
        stale = iso(t - timedelta(hours=4))     # a run the Mac fell asleep in the middle of
        if not runs or all(r[2] == "running" and r[0] < stale for r in runs):
            out.append("run_daily")
    if t.hour >= int(cfg.get("report.hour_et", 7)):
        reports = _rows(conn, "report", day)
        emailed = any("emailed" in (r[3] or "") for r in reports)
        tried_lately = any(r[0] > iso(t - timedelta(minutes=RETRY_MIN)) for r in reports)
        running = any(r[2] == "running" for r in runs) and "run_daily" not in out
        if not emailed and not tried_lately and not running:
            out.append("report")
    return out


def start_background(cfg: Any, todo: list[str]) -> bool:
    """Starts `titan.cli catch-up` detached so check-replies never waits on a long run. caffeinate keeps the Mac awake."""
    if not todo:
        return False
    cmd = [sys.executable, "-m", "titan.cli", "catch-up"]
    if sys.platform == "darwin":
        cmd = ["/usr/bin/caffeinate", "-i", *cmd]
    log_path = cfg.root / "logs" / "cron.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as fh:
        subprocess.Popen(cmd, cwd=str(cfg.root), stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
    return True
