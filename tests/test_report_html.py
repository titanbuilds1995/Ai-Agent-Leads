"""The morning report is a designed email in the house style (Rodrigo, 2026-10-27), with the text version alongside."""
from titan.report import collect_warnings, run_report
from titan.report_html import build_html


def test_html_report_has_the_house_style_and_sections(loaded, cfg, tmp_path):
    cfg.root = tmp_path
    loaded.execute("UPDATE properties SET lead_status='NEEDS_REVIEW', lead_status_at='2026-10-27' WHERE id IN (SELECT id FROM properties LIMIT 2)")
    h = build_html(loaded, cfg)
    for part in ("Morning Report", "Conversations for you", "Scoreboard", "Trust ladder", "Last 14 days", "From email to offer", "Top targets", "Engine health",
                 "#0C1C35", "#B99455", "Next step", "Needs you"):
        assert part in h, part
    assert "BLOCKERS" not in h and "TITAN-DATA" not in h and "<script" not in h


def test_repeated_warnings_collapse(conn):
    for _ in range(28):
        conn.execute("INSERT INTO daily_runs(step, run_date, status, started_at) VALUES('check_replies','2026-10-27','partial','x')")
    w = collect_warnings(conn, "2026-10-27")
    assert w == ["step check_replies: partial (×28 today)"]


def test_report_email_carries_html_and_the_text_data_block(loaded, cfg, tmp_path, monkeypatch):
    cfg.root = tmp_path
    monkeypatch.setitem(cfg.secrets, "REPORT_TO", "rodrigo@example.com")
    sent = {}

    class G:
        def send(self, to, subject, body, **kw):
            sent.update(to=to, body=body, html=kw.get("html"))
            return {"message_id": "m", "thread_id": "t"}
    run_report(loaded, cfg, G())
    assert sent["html"] and "Morning Report" in sent["html"] and "BEGIN TITAN-DATA-B64" in sent["body"]
    assert "## BLOCKERS.md" not in sent["body"]
