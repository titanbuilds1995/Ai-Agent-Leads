import json
from types import SimpleNamespace

from titan import dashboard_sync as ds


def test_snapshot_shape_and_ordering(loaded, cfg):
    snap = ds.build_snapshot(loaded, cfg)
    for k in ("generated_at", "mode", "funnel", "runs", "properties", "agents", "threads", "outreach", "events", "usage"):
        assert k in snap
    ratings = [p["rating"] for p in snap["properties"] if p["rating"]]
    order = {"HOT": 0, "WARM": 1, "COLD": 2}
    assert ratings == sorted(ratings, key=order.get)                      # HOT first
    p = snap["properties"][0]
    assert isinstance(p["cited"], list) and "raw" not in p and p["agent"] and "name" in p["agent"]
    assert len(p["remarks"]) <= 700
    assert snap["events"] and snap["events"][0]["ts"] >= snap["events"][-1]["ts"]
    assert any(e["type"] == "run" and e["title"].startswith("ingest") for e in snap["events"])
    json.dumps(snap, default=str)                                           # serialisable


def test_snapshot_never_contains_secrets(loaded, cfg, monkeypatch):
    monkeypatch.setitem(cfg.secrets, "APIFY_TOKEN", "apify_api_SECRETVALUE")
    monkeypatch.setitem(cfg.secrets, "ANTHROPIC_API_KEY", "sk-ant-SECRETVALUE")
    text = json.dumps(ds.build_snapshot(loaded, cfg), default=str)
    assert "SECRETVALUE" not in text


class FakeSession:
    def __init__(self, status=200):
        self.status, self.calls = status, []

    def put(self, url, data=None, timeout=None, headers=None):
        self.calls.append((url, headers, data))
        return SimpleNamespace(status_code=self.status, text="boom" if self.status >= 400 else "",
                               json=lambda: {"url": "https://abc.public.blob.vercel-storage.com/titan/snapshot.json", "pathname": "titan/snapshot.json"})


def test_upload_puts_fixed_path_with_overwrite(loaded, cfg):
    s = FakeSession()
    meta = ds.upload({"a": 1}, "tok", session=s)
    url, headers, data = s.calls[0]
    assert url.endswith("/titan/snapshot.json") and headers["authorization"] == "Bearer tok"
    assert headers["x-allow-overwrite"] == "1" and headers["x-add-random-suffix"] == "0" and json.loads(data) == {"a": 1}
    assert meta["pathname"] == "titan/snapshot.json"


def test_sync_writes_local_and_skips_upload_without_token(loaded, cfg, tmp_path, monkeypatch):
    monkeypatch.setitem(cfg.raw["paths"], "reports", str(tmp_path))
    res = ds.sync(loaded, cfg)
    assert res["uploaded"] is False and "skipped" in res and (tmp_path / "dashboard.json").exists()
    monkeypatch.setitem(cfg.secrets, "BLOB_READ_WRITE_TOKEN", "tok")
    res = ds.sync(loaded, cfg, session=FakeSession())
    assert res["uploaded"] is True and res["url"].startswith("https://")
    res = ds.sync(loaded, cfg, session=FakeSession(status=403))   # a failed upload never raises
    assert res["uploaded"] is False and "error" in res
