"""`make status`: print the funnel."""
from __future__ import annotations

from typing import Any

from . import db


def render(conn, cfg: Any, run_date: str) -> str:
    f = db.funnel(conn, run_date)
    u = db.usage_summary(conn)
    lines = [f"Titan lead engine — {run_date}  mode={cfg.mode} channel={cfg.channel}", ""]
    rows = [("properties stored", f["properties"]), ("  seen today", f.get("today_properties_seen", 0)), ("  new today", f.get("today_new", 0)),
            ("scored", f["scored"]), ("valued (score ≥ min)", f["valued"]), ("  HOT", f["HOT"]), ("  WARM", f["WARM"]), ("  COLD", f["COLD"]),
            ("agents", f["agents"]), ("  with email", f["agents_with_email"]),
            ("emails drafted / sent", f"{f['emails_drafted']} / {f['emails_sent']}"), ("sms drafted / sent", f"{f['sms_drafted']} / {f['sms_sent']}"),
            ("replies received", f["replies"]), ("HOT_LEAD / NEEDS_CALL", f["hot_leads"]), ("do-not-contact", f["do_not_contact"]),
            ("LLM calls / cost", f"{u['calls']} / ${u['cost_usd']:.2f}")]
    w = max(len(k) for k, _ in rows)
    lines += [f"{k.ljust(w)}  {v}" for k, v in rows]
    last = conn.execute("SELECT step, status, finished_at FROM daily_runs ORDER BY id DESC LIMIT 6").fetchall()
    if last:
        lines += ["", "recent steps:"] + [f"  {r['step']:<14} {r['status']:<8} {r['finished_at'] or ''}" for r in last]
    return "\n".join(lines)
