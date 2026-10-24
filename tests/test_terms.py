from titan.util import inspection_phrase
from titan.outreach.compose import template_email, compose_offer_email, EMAIL_SYSTEM
from titan.replies.responder import _terms_sentence


def test_inspection_phrase_follows_config():
    assert inspection_phrase({"inspection_contingency": False}) == "no inspection contingency"
    assert inspection_phrase({"inspection_contingency": True, "inspection_days": 7}) == "a 7-day inspection period"
    assert inspection_phrase({"inspection_contingency": True}) == "a short inspection period"


def test_nothing_the_engine_writes_waives_inspection_when_config_keeps_it(cfg):
    assert cfg["outreach"]["terms"]["inspection_contingency"] is True
    agent = {"id": 1, "name": "Maria Lopez", "email": "m@x.com"}
    prop = {"id": 1, "street": "1 Main St", "city": "Miami", "county": "Miami-Dade", "remarks": "needs work", "cited": "[]"}
    for seed in range(8):
        _, body, _ = template_email(cfg, agent, [prop], seed)
        assert "no inspection" not in body.lower() and "waive" not in body.lower()
    _, body, _ = template_email(cfg, agent, [prop, dict(prop, id=2, street="2 Main St")], 0)
    assert "no inspection" not in body.lower() and "waive" not in body.lower()
    offer = compose_offer_email(cfg, agent, prop, 200000)["body"]       # only after Rodrigo approves a price
    assert "no inspection" not in offer and "7-day inspection" in offer
    assert "7-day inspection" in _terms_sentence(cfg)
    assert "no inspection" not in EMAIL_SYSTEM.format(protocol="", sender="Charles", company="Titan", company_short="Titan")
    assert all("no inspection" not in e.lower() for e in cfg["outreach"]["example_emails"])
