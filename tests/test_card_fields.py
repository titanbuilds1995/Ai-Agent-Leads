"""Phone + Address custom fields on every lead card (Rodrigo, 2026-11-19)."""
from tests.test_approvals import FakeAsana, _cards

from titan import card_fields


class FieldAsana(FakeAsana):
    def __init__(self):
        super().__init__()
        self.fields, self.on_board, self.values = {}, set(), {}

    def ensure_text_field(self, name):
        return self.fields.setdefault(name, f"f-{name.lower()}")

    def ensure_project_field(self, project, field):
        self.on_board.add((project, field))

    def set_fields(self, task, values):
        self.values.setdefault(task, {}).update(values)


def test_cards_get_phone_and_address_once(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    fa = FieldAsana()
    fa.__dict__.update({k: v for k, v in asana.__dict__.items()})
    loaded.execute("UPDATE agents SET phone='+13055550100'")
    out = card_fields.sync(loaded, cfg, fa)
    assert out["written"] == len(asana.tasks) >= 1
    assert (str(cfg["outreach"]["asana"]["project"]), "f-phone") in fa.on_board
    gid = next(iter(fa.values))
    v = fa.values[gid]
    assert v["f-phone"] == "(305) 555-0100" and ", " in v["f-address"]
    assert card_fields.sync(loaded, cfg, fa)["written"] == 0          # nothing changed: no calls
    loaded.execute("UPDATE agents SET phone='+15615550199'")
    assert card_fields.sync(loaded, cfg, fa)["written"] >= 1          # a new phone is written
    assert fa.values[gid]["f-phone"] == "(561) 555-0199"
