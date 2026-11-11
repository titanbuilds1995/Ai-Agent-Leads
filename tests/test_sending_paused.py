"""Rodrigo, 2026-11-11: "pause everything right now, no more email sending out"."""
from datetime import datetime, timezone

from titan.approvals import in_window, sending_paused

WED_NOON_ET = datetime(2026, 11, 11, 17, 0, tzinfo=timezone.utc)


def test_pause_closes_the_send_window(cfg):
    assert in_window(cfg, WED_NOON_ET)
    cfg["outreach"]["sending_paused"] = True
    assert sending_paused(cfg)
    assert not in_window(cfg, WED_NOON_ET)                      # every sender waits, as it does outside the window
    assert in_window(cfg, WED_NOON_ET, even_if_paused=True)     # only the one-time delay note asks this way

