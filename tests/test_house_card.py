"""House cards in the 1031 cards' layout (Rodrigo, 2026-11-12: "add the same design … into our MLS deals … so it's that organized")."""
from titan import house_card
from titan.approvals import card_notes, parse_card
from test_approvals import FakeAsana, _cards


def test_new_cards_have_the_agent_first_name_and_the_sections(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    gid = next(iter(asana.tasks))
    t = asana.tasks[gid]
    html = t["html_notes"]
    for head in ("THE NUMBERS", "DO WE BELIEVE IT?", "THE HOUSE", "AGENT", "FIRST EMAIL"):
        assert f"<strong>{head}</strong>" in html
    card = parse_card(t["notes"])                     # the sender still reads Asana's plain rendering
    assert card and card["outreach_id"] and card["body"]
    row = loaded.execute("SELECT agent_id FROM outreach WHERE asana_task_id=?", (gid,)).fetchone()
    name = loaded.execute("SELECT name FROM agents WHERE id=?", (row[0],)).fetchone()[0]
    assert t["name"].startswith(f"{name} · ") and "$" in t["name"] and "days" in t["name"]


def test_card_name_and_numbers():
    p = {"street": "529 23rd Ave N", "city": "Lake Worth Beach", "list_price": 299900, "days_on_market": 92, "price_cuts": 2,
         "beds": 3, "baths": 2, "sqft": 1200, "property_type": "single_family", "arv": 480000, "arv_method": "comps", "comp_count": 7,
         "mao": 230000, "comp_grade": "B", "lane": "SITTING"}
    agent = {"name": "Lise Freeman", "email": "lise@example.com", "phone": "+15615550100", "brokerage": "Small Realty"}
    assert house_card.card_name(agent, [p]) == "Lise Freeman · 3bd House · Lake Worth Beach · $300k · 92 days · 2 cuts"
    html = house_card.card_html([p], agent, {"hypothesis": "inherited, full gut"}, house_card.email_block("lise@example.com", "S", "Hey Lise,\n\nQ?", 4))
    assert "Internal MAO $230,000" in html and "77% of asking" in html and "Comps grade <strong>B</strong>" in html
    assert "inherited, full gut" in html
    p["arv_method"] = "comps_suspect"
    assert "don't trust it" in house_card.card_html([p], agent, None, "")


def test_older_cards_are_reformatted_once_keeping_his_edits(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    gid = next(iter(asana.tasks))
    card = parse_card(asana.tasks[gid]["notes"])
    # an old plain card with a hand edit
    asana.tasks[gid]["notes"] = card_notes(card["to"], card["subject"], "Hey there,\n\nMy own words?\n\nCharles", card["outreach_id"], "Listing: x")
    asana.tasks[gid]["name"] = "Old name · 1 Main St, Miami"
    loaded.execute("DELETE FROM card_format")
    n = house_card.refresh_cards(loaded, cfg, asana, limit=500)
    assert n["formatted"] >= 1
    t = asana.tasks[gid]
    assert "<strong>THE NUMBERS</strong>" in t["html_notes"] and parse_card(t["notes"])["body"] == "Hey there,\n\nMy own words?\n\nCharles"
    assert t["name"] != "Old name · 1 Main St, Miami"
    again = house_card.refresh_cards(loaded, cfg, asana, limit=500)
    assert again["formatted"] == 0
