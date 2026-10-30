"""Catch-up when the Mac wakes (Rodrigo, 2026-10-30): the 5:30am run and the 7am report happen at the first check after."""
from datetime import datetime, timezone

from titan import db
from titan.catch_up import RUN_STEP, due


def at(h, m=0, day=30):     # UTC; ET = UTC-4 in October
    return datetime(2026, 10, day, h, m, tzinfo=timezone.utc)


def _row(conn, step, started, status="ok", counts=None, day="2026-10-30"):
    conn.execute("INSERT INTO daily_runs(run_date,step,started_at,status,counts) VALUES(?,?,?,?,?)",
                 (day, step, started, status, db.j(counts) if counts else None))


def test_nothing_before_the_schedule(conn, cfg):
    assert due(conn, cfg, now=at(9, 15)) == []          # 5:15am ET


def test_mac_slept_through_the_morning_runs_both(conn, cfg):
    assert due(conn, cfg, now=at(13)) == ["run_daily", "report"]     # turned on at 9am ET
    assert due(conn, cfg, now=at(10)) == ["run_daily"]               # 6am ET: report not due yet


def test_done_today_means_nothing_to_do(conn, cfg):
    _row(conn, RUN_STEP, "2026-10-30T09:30:00Z")
    _row(conn, "report", "2026-10-30T11:00:00Z", counts={"status": "written+emailed"})
    assert due(conn, cfg, now=at(14)) == []


def test_report_waits_for_a_daily_run_in_progress(conn, cfg):
    _row(conn, RUN_STEP, "2026-10-30T12:50:00Z", status="running")
    assert due(conn, cfg, now=at(13)) == []
    assert due(conn, cfg, now=at(18)) == ["run_daily", "report"]     # 5h later: the Mac slept mid-run, start over


def test_failed_report_email_retries_once_an_hour(conn, cfg):
    _row(conn, RUN_STEP, "2026-10-30T09:30:00Z")
    _row(conn, "report", "2026-10-30T12:30:00Z", counts={"status": "written; email failed: token"})
    assert due(conn, cfg, now=at(13)) == []
    assert due(conn, cfg, now=at(13, 45)) == ["report"]


def test_late_at_night_waits_for_the_morning(conn, cfg):
    assert due(conn, cfg, now=at(1, day=31)) == []       # 9pm ET Oct 30
