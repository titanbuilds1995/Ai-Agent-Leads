from titan import db


def test_schema_idempotent(conn):
    db.init_db(conn)
    db.init_db(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"properties", "agents", "scores", "valuations", "outreach", "threads", "messages", "do_not_contact", "daily_runs", "llm_usage"} <= tables


def test_property_upsert_no_duplicates(conn):
    aid = db.upsert_agent(conn, "Jane Doe", "ABC Realty", "(305) 555-1212", "+13055551212")
    pid, created = db.upsert_property(conn, "1 main st|miami|fl|33125", {"street": "1 Main St", "list_price": 100000, "agent_id": aid})
    pid2, created2 = db.upsert_property(conn, "1 main st|miami|fl|33125", {"street": "1 Main St", "list_price": 95000})
    assert pid == pid2 and created and not created2
    assert conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0] == 1
    assert conn.execute("SELECT list_price FROM properties").fetchone()[0] == 95000


def test_agent_upsert_keeps_email_and_phone(conn):
    a1 = db.upsert_agent(conn, "Jane Doe", "ABC Realty", None, None, email="Jane@ABC.com")
    a2 = db.upsert_agent(conn, "jane doe", "ABC  Realty", "305-555-1212", "+13055551212")
    assert a1 == a2
    row = conn.execute("SELECT email, phone FROM agents WHERE id=?", (a1,)).fetchone()
    assert row["email"] == "jane@abc.com" and row["phone"] == "+13055551212"


def test_dnc(conn):
    db.add_dnc(conn, None, "+13055550000", None, "STOP")
    assert db.is_dnc(conn, phone="+13055550000")
    assert not db.is_dnc(conn, phone="+13055550001")
    db.add_dnc(conn, None, None, "Agent@X.com", "unsubscribe")
    assert db.is_dnc(conn, email="agent@x.com")
