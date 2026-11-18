"""Photo condition check (Rodrigo, 2026-11-18): freshly renovated / really good houses never reach Fresh Leads."""
import json

from titan import condition
from titan.approvals import parse_card, sync_feedback
from titan.learning import rate, scan_board
from titan.outreach.send import candidate_rows
from tests.test_approvals import _cards, _sections

JPG = (b"\xff\xd8\xff" + b"0" * 50, "image/jpeg")


class Eyes:
    """Stands in for Claude: says what `verdicts` holds per street, DATED otherwise."""
    session_cost = 0.0

    def __init__(self, verdicts=None, fail=False):
        self.verdicts, self.fail, self.calls = verdicts or {}, fail, 0

    def read_images(self, purpose, images, prompt, schema, system=None):
        self.calls += 1
        if self.fail:
            return None
        for street, v in self.verdicts.items():
            if street in prompt:
                return {"verdict": v[0], "confidence": v[1], "reason": "New kitchen, redone baths."}
        return {"verdict": "DATED", "confidence": "high", "reason": "Original kitchen."}


def _fetch(streets):
    def fn(p):
        streets.append(p["street"])
        return [JPG] * 5
    return fn


def _house(loaded, asana, gid):
    oid = parse_card(asana.tasks[gid]["notes"])["outreach_id"]
    pids = json.loads(loaded.execute("SELECT property_ids FROM outreach WHERE id=?", (oid,)).fetchone()[0])
    return oid, pids


def _name_in_prompt(loaded, monkeypatch):
    # the fake recognises a house by its street: put it where look() writes the facts
    real = condition.look
    monkeypatch.setattr(condition, "look", lambda llm, p, images: real(llm, {**p, "remarks": f"{p['street']} | {p.get('remarks') or ''}"}, images))
    loaded.execute("UPDATE properties SET remarks=replace(replace(remarks,'renovated','kept'),'remodeled','kept')")


def test_photo_picks_front_kitchens_baths_and_bigger_sizes():
    raw = {"photos": [{"href": f"https://ap.rdcpix.com/x{i}s.jpg", "tags": [{"label": lab}]} for i, lab in enumerate(
        ["house_view", "bedroom", "bedroom", "kitchen", "yard", "bathroom", "kitchen", "garage", "living_room", "bedroom", "porch", "pool"])]}
    urls = condition.photo_urls({"raw": json.dumps(raw)}, 8)
    assert urls[0].endswith("x0s.jpg") and urls[1].endswith("x3s.jpg") and urls[2].endswith("x6s.jpg") and urls[3].endswith("x5s.jpg")
    assert len(urls) == 8 and len(set(urls)) == 8
    assert condition._variants(urls[0])[0].endswith("x0od-w640_h480.jpg") and condition._variants(urls[0])[-1] == urls[0]
    cut = json.dumps(raw)[:300]                                        # a record cut off at the storage limit is still read
    assert condition.photo_urls({"raw": cut}, 8)
    assert condition.photo_urls({"raw": None}) == []


def test_remarks_settle_the_plain_cases_for_free():
    r = condition.remarks_verdict({"remarks": "Completely renovated 3/2 with new roof. Move right in!"})
    assert r["verdict"] == "RENOVATED" and r["source"] == "remarks"
    assert condition.remarks_verdict({"remarks": "Partially renovated, sold as-is, needs to be completed."}) is None
    assert condition.remarks_verdict({"remarks": "Updated kitchen, great location."}) is None


def test_renovated_card_goes_to_skip_and_dated_ones_stay(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    _name_in_prompt(loaded, monkeypatch)
    s = _sections(cfg)
    gids = list(asana.tasks)
    oid, pids = _house(loaded, asana, gids[0])
    streets = [r[0] for r in loaded.execute(f"SELECT street FROM properties WHERE id IN ({','.join('?' * len(pids))})", pids)]
    eyes = Eyes({st: ("RENOVATED", "high") for st in streets})
    seen = []
    out = condition.run(loaded, cfg, asana, eyes, fetch=_fetch(seen))
    assert out["to_skip"] == 1 and asana.where[gids[0]] == s["skip"]
    assert any("Condition check" in c and "freshly renovated" in c for c in asana.stories[gids[0]])
    assert loaded.execute("SELECT status FROM outreach WHERE id=?", (oid,)).fetchone()[0] == "skipped"
    assert {r[0] for r in loaded.execute(f"SELECT lead_status FROM properties WHERE id IN ({','.join('?' * len(pids))})", pids)} == {"EXCLUDED"}
    assert all(asana.where[g] == s["to_review"] for g in gids[1:])
    # not his vote on the email
    scan_board(loaded, cfg, asana)
    sync_feedback(loaded, cfg, asana, None)
    assert rate(loaded, "first_touch", 30)["skipped"] == 0
    # every house is looked at once, never twice
    calls = eyes.calls
    condition.run(loaded, cfg, asana, eyes, fetch=_fetch(seen))
    assert eyes.calls == calls
    # he drags it back: the house is kept for good and its email can go again
    asana.move(gids[0], s["to_review"])
    out = condition.run(loaded, cfg, asana, eyes, fetch=_fetch(seen))
    assert out["put_back_by_you"] == 1 and asana.where[gids[0]] == s["to_review"]
    assert loaded.execute("SELECT status FROM outreach WHERE id=?", (oid,)).fetchone()[0] == "pending_approval"
    assert "EXCLUDED" not in {r[0] for r in loaded.execute(f"SELECT lead_status FROM properties WHERE id IN ({','.join('?' * len(pids))})", pids)}
    condition.run(loaded, cfg, asana, eyes, fetch=_fetch(seen))
    assert asana.where[gids[0]] == s["to_review"]


def test_low_confidence_list_mode_and_failures_move_nothing(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    _name_in_prompt(loaded, monkeypatch)
    s = _sections(cfg)
    everything = {r[0]: ("GOOD_SHAPE", "medium") for r in loaded.execute("SELECT street FROM properties")}
    # Claude not answering: nothing stored, nothing moved, stops early
    cfg["condition"]["stop_after_errors"] = 2
    out = condition.run(loaded, cfg, asana, Eyes(fail=True), fetch=_fetch([]))
    assert out["checked"] == 0 and out["errors"] == 2 and out.get("warning")
    assert loaded.execute("SELECT COUNT(*) FROM condition_checks").fetchone()[0] == 0
    # "good shape" at medium confidence is not enough to take a house off
    out = condition.run(loaded, cfg, asana, Eyes(everything), fetch=_fetch([]))
    assert out["checked"] >= 1 and out["to_skip"] == 0 and all(asana.where[g] == s["to_review"] for g in asana.tasks)
    # --list: says what it would do, moves nothing
    loaded.execute("UPDATE condition_checks SET verdict='RENOVATED', confidence='high'")
    out = condition.run(loaded, cfg, asana, Eyes(), fetch=_fetch([]), dry_run=True)
    assert out["to_skip"] >= 1 and all(asana.where[g] == s["to_review"] for g in asana.tasks)
    assert loaded.execute("SELECT COUNT(*) FROM properties WHERE lead_note LIKE 'condition:%'").fetchone()[0] == 0


def test_outreach_only_cards_houses_that_were_looked_at(loaded, cfg, monkeypatch):
    loaded.execute("UPDATE agents SET email=lower(replace(name,' ','.'))||'@example.com', email_confidence='high' WHERE email IS NULL")
    _name_in_prompt(loaded, monkeypatch)
    cfg["condition"]["gate_outreach"] = True
    before = candidate_rows(loaded, cfg, gated=False)
    assert before and candidate_rows(loaded, cfg) == []                 # nothing looked at yet: nothing is carded
    flip = before[0]["street"]
    out = condition.run(loaded, cfg, None, Eyes({flip: ("RENOVATED", "medium")}), fetch=_fetch([]))
    assert out["excluded_before_card"] == 1
    after = {p["street"] for p in candidate_rows(loaded, cfg)}
    assert flip not in after and after == {p["street"] for p in before} - {flip}
    # too few photos = can't tell: the house stays
    loaded.execute("DELETE FROM condition_checks")
    loaded.execute("UPDATE properties SET lead_status='NONE' WHERE lead_status='EXCLUDED' AND lead_note LIKE 'condition:%'")
    condition.run(loaded, cfg, None, Eyes({flip: ("RENOVATED", "high")}), fetch=lambda p: [JPG])
    assert flip in {p["street"] for p in candidate_rows(loaded, cfg)}
