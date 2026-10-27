"""The morning report is a designed email in the house style (Rodrigo, 2026-10-27), with the text version alongside."""
from titan.report import collect_warnings, run_report
from titan.report_html import build_html


def test_html_report_has_the_house_style_and_sections(loaded, cfg, tmp_path):
    cfg.root = tmp_path
    loaded.execute("UPDATE properties SET lead_status='NEEDS_REVIEW', lead_status_at='2026-10-27' WHERE id IN (SELECT id FROM properties LIMIT 2)")
    h = build_html(loaded, cfg)
    for part in ("Morning Report", "Conversations for you", "Scoreboard", "Trust ladder", "Day by day", "From email to offer", "Top targets", "Engine health",
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


def test_day_chart_is_rows_with_the_numbers_written_out():
    """Rodrigo, 2026-10-27: the 14-day column chart was unreadable in the email. Now one row per day, newest first,
    every bar followed by its number, zero days in plain words, the week before summarised in a sentence."""
    from titan.report_html import day_chart
    series = [{"date": f"2026-10-{d:02d}", "emails_sent": s, "replies": r}
              for d, s, r in zip(range(14, 28), [0] * 10 + [2, 2, 1, 4], [0] * 10 + [0, 1, 0, 2])]
    h = day_chart(series)
    assert "Today" in h and "Mon 26" in h and "Wed 21" in h and "Tue 20" not in h       # last 7 days only
    assert h.index("Today") < h.index("Mon 26") < h.index("Sat 24")                      # newest first
    assert "4 sent" in h and "2 replies" in h and "1 reply" in h and "0 sent" in h and "0 replies" in h
    assert "The 7 days before that: 0 emails sent, 0 replies." in h
    assert ">9<" in h and ">3<" in h and "33%" in h                                      # week totals and reply rate
    assert "height:90" not in h and "height:110" not in h                                # no pixel-high column bars


def test_funnel_explains_each_drop_in_words():
    from titan.report_html import funnel
    h = funnel([("Emails sent", 9), ("Agents who replied", 3), ("Answered by us", 3), ("Offers sent", 1)],
               ["", "wrote back", "got an answer from Charles", "got a number"])
    assert "3 of 9 wrote back · 33%" in h and "3 of 3 got an answer from Charles · 100%" in h and "1 of 3 got a number · 33%" in h
    assert 'width="33%"' in h and 'width="11%"' in h                                      # bars share one scale
    assert "No emails sent yet" in funnel([("Emails sent", 0), ("Agents who replied", 0)])
