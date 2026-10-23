from titan import db
from titan.enrich import Enricher, Hit, apply_pattern, candidate_patterns, detect_pattern, name_matches


def test_name_matching():
    assert name_matches("maria.lopez@kw.com", "Maria Lopez")
    assert name_matches("mlopez@kw.com", "Maria Lopez")
    assert name_matches("maria@kw.com", "Maria Lopez")
    assert not name_matches("john.smith@kw.com", "Maria Lopez")
    assert not name_matches("info@kw.com", "Maria Lopez")


def test_pattern_detection_and_apply():
    known = [("Maria Lopez", "maria.lopez@kw.com"), ("John Carter", "john.carter@kw.com"), ("Ana Ruiz", "aruiz@kw.com")]
    assert detect_pattern(known) == "first.last"
    assert apply_pattern("first.last", "sandra", "pena", "kw.com") == "sandra.pena@kw.com"
    assert detect_pattern([("Maria Lopez", "maria.lopez@kw.com")]) is None       # one sample is not a pattern
    assert candidate_patterns("sandra", "pena", "kw.com")[0] == "sandra.pena@kw.com"


def _agent(conn, name, brokerage, email=None, phone=None):
    aid = db.upsert_agent(conn, name, brokerage, phone, phone, email)
    return db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (aid,)).fetchone())


def test_chain_stops_at_payload(conn, cfg):
    a = _agent(conn, "Maria Lopez", "KW Miami", email="maria.lopez@kwmiami.com")
    calls = []
    e = Enricher(cfg, conn, fetch=lambda u: calls.append(u) or None, mx=lambda d: [], rcpt=lambda h, m: None, search=lambda q: [])
    hit = e.enrich_agent(a)
    assert hit.email == "maria.lopez@kwmiami.com" and hit.confidence == "high" and not calls


def test_brokerage_site_rung(conn, cfg):
    _agent(conn, "Maria Lopez", "KW Miami", email="maria.lopez@kwmiami.com")   # gives us the brokerage domain
    a = _agent(conn, "John Carter", "KW Miami")
    pages = {"https://kwmiami.com": "<p>Contact info@kwmiami.com</p>",
             "https://www.kwmiami.com/agents": "<li>John Carter – jcarter@kwmiami.com</li><li>Maria Lopez - maria.lopez@kwmiami.com</li>"}
    e = Enricher(cfg, conn, fetch=lambda u: pages.get(u), mx=lambda d: [], rcpt=lambda h, m: None, search=lambda q: [])
    hit = e.enrich_agent(a)
    assert hit.email == "jcarter@kwmiami.com" and hit.confidence == "high" and hit.source.startswith("brokerage_site")


def test_portal_rung_via_search(conn, cfg):
    a = _agent(conn, "Sandra Pena", "RE/MAX Advance")
    pages = {"https://www.realtor.com/realestateagents/sandra-pena": "mailto:sandra.pena@remaxadvance.com"}
    e = Enricher(cfg, conn, fetch=lambda u: pages.get(u), mx=lambda d: [], rcpt=lambda h, m: None,
                 search=lambda q: ["https://www.realtor.com/realestateagents/sandra-pena"] if "realtor.com" in q else [])
    hit = e.enrich_agent(a)
    assert hit.email == "sandra.pena@remaxadvance.com" and hit.source == "portal:www.realtor.com"


def test_pattern_rung_with_smtp(conn, cfg):
    _agent(conn, "Maria Lopez", "KW Miami", email="maria.lopez@kwmiami.com")
    _agent(conn, "John Carter", "KW Miami", email="john.carter@kwmiami.com")
    a = _agent(conn, "Sandra Pena", "KW Miami")
    verified = {"sandra.pena@kwmiami.com": True}
    e = Enricher(cfg, conn, fetch=lambda u: None, mx=lambda d: ["mx.kwmiami.com"], rcpt=lambda h, m: verified.get(m, False), search=lambda q: [])
    hit = e.enrich_agent(a)
    assert hit.email == "sandra.pena@kwmiami.com" and hit.confidence == "medium" and hit.source == "pattern+smtp"


def test_pattern_rung_rejects_when_smtp_says_no(conn, cfg):
    _agent(conn, "Maria Lopez", "KW Miami", email="maria.lopez@kwmiami.com")
    _agent(conn, "John Carter", "KW Miami", email="john.carter@kwmiami.com")
    a = _agent(conn, "Sandra Pena", "KW Miami")
    e = Enricher(cfg, conn, fetch=lambda u: None, mx=lambda d: ["mx.kwmiami.com"], rcpt=lambda h, m: False, search=lambda q: [])
    assert e.enrich_agent(a) is None


def test_run_updates_only_hot_warm_agents(loaded, cfg):
    e = Enricher(cfg, loaded, fetch=lambda u: None, mx=lambda d: [], rcpt=lambda h, m: None, search=lambda q: [])
    counts = e.run()
    assert counts["agents_tried"] >= 1
    assert loaded.execute("SELECT COUNT(*) FROM agents WHERE enrich_attempts>0").fetchone()[0] == counts["agents_tried"]
