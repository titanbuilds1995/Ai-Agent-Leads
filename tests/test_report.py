from titan.report import build_report, run_report
from titan.status import render
from titan import db
from titan.outreach.send import ensure_thread, log_message


def test_report_renders_and_writes(loaded, cfg, tmp_path):
    a = loaded.execute("SELECT a.id, p.id pid FROM agents a JOIN properties p ON p.agent_id=a.id JOIN valuations v ON v.property_id=p.id WHERE v.rating='HOT' LIMIT 1").fetchone()
    tid = ensure_thread(loaded, a["id"], "email", "gt", [a["pid"]])
    log_message(loaded, tid, "in", "We have a cash offer at 310, can you beat it?", external_id="x1", classification="COUNTER_WITH_NUMBER")
    db.set_lead_status(loaded, a["pid"], "HOT_LEAD", "counter 310000 within 10% of MAO")
    cfg.raw["paths"]["reports"] = str(tmp_path)
    path = run_report(loaded, cfg, gmail=None)
    text = path.read_text()
    assert "Conversations for you (1)" in text and "cash offer at 310" in text and "Say this" in text
    assert "approve a price" in text and "not an offer" in text                # conversation first: Rodrigo prices it
    assert "| HOT |" in text and "API cost" in text
    assert "<" not in text.split("## Pipeline")[0].replace("—", "")  # plain text, no html in the lead section
    assert "Titan lead engine" in render(loaded, cfg, "2026-10-22")


def test_report_without_leads(loaded, cfg):
    text = build_report(loaded, cfg, "2026-10-22")
    assert "No conversations need you yet" in text
