"""Minimal Apify client: start an actor run, wait, page through dataset items. Retries with backoff."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

from ..util import log, retry

API = "https://api.apify.com/v2"


class ApifyError(Exception):
    pass


class ApifyClient:
    def __init__(self, token: str, timeout_seconds: int = 900, raw_dir: Path | None = None,
                 session: requests.Session | None = None, poll_seconds: float = 10.0):
        self._token = token
        self.timeout_seconds = timeout_seconds
        self.raw_dir = raw_dir
        self.s = session or requests.Session()
        self.poll_seconds = poll_seconds

    def _req(self, method: str, path: str, **kw) -> dict[str, Any] | list[Any]:
        def go():
            r = self.s.request(method, f"{API}{path}", params={"token": self._token, **kw.pop("params", {})},
                               timeout=60, **kw)
            if r.status_code >= 500 or r.status_code == 429:
                raise ApifyError(f"HTTP {r.status_code}")
            if r.status_code >= 400:
                # do not retry client errors; never echo the URL (it carries the token)
                raise RuntimeError(f"Apify {method} {path}: HTTP {r.status_code}: {r.text[:300]}")
            return r.json()
        return retry(go, attempts=4, retry_on=(ApifyError, requests.ConnectionError, requests.Timeout), what=f"apify {path}")

    def run_actor(self, actor_id: str, run_input: dict[str, Any], max_items: int = 3000, label: str = "") -> list[dict[str, Any]]:
        act = actor_id.replace("/", "~")
        log.info("apify: starting %s (%s)", actor_id, label)
        run = self._req("POST", f"/acts/{act}/runs", json=run_input, params={"memory": 2048})["data"]
        run_id, started = run["id"], time.time()
        while True:
            st = self._req("GET", f"/actor-runs/{run_id}")["data"]
            status = st["status"]
            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break
            if time.time() - started > self.timeout_seconds:
                self._req("POST", f"/actor-runs/{run_id}/abort")
                raise ApifyError(f"{actor_id} exceeded {self.timeout_seconds}s; aborted")
            time.sleep(self.poll_seconds)
        if status != "SUCCEEDED":
            raise ApifyError(f"{actor_id} ended with {status}")
        items = self.dataset_items(st["defaultDatasetId"], max_items)
        if self.raw_dir:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            out = self.raw_dir / f"{time.strftime('%Y-%m-%d')}_{act}_{label or 'run'}.json"
            out.write_text(json.dumps(items, ensure_ascii=False)[:50_000_000])
        log.info("apify: %s returned %d items", actor_id, len(items))
        return items

    def dataset_items(self, dataset_id: str, max_items: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset, limit = 0, 500
        while offset < max_items:
            page = self._req("GET", f"/datasets/{dataset_id}/items",
                             params={"clean": "true", "format": "json", "offset": offset, "limit": min(limit, max_items - offset)})
            if not isinstance(page, list) or not page:
                break
            items.extend(page)
            if len(page) < limit:
                break
            offset += limit
        return items
