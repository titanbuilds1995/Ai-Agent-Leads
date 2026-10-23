"""`make dashboard`: serve the dashboard from this machine (http://127.0.0.1:8765), reading the local snapshot.

No password, no Vercel, no network: the site in dashboard/public is served as-is and /api/snapshot returns
reports/dashboard.json, refreshed from the database on every request so the Live tab is really live.
"""
from __future__ import annotations

import http.server
import json
import threading
import webbrowser
from pathlib import Path
from typing import Any

from . import db
from .dashboard_sync import build_snapshot
from .util import log


def serve(cfg, port: int = 8765, open_browser: bool = True) -> None:
    root = Path(__file__).resolve().parent.parent / "dashboard" / "public"
    lock = threading.Lock()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a: Any, **k: Any):
            super().__init__(*a, directory=str(root), **k)

        def do_GET(self):
            if self.path.startswith("/api/snapshot"):
                with lock:
                    conn = db.connect(cfg.db_path)
                    try:
                        body = json.dumps(build_snapshot(conn, cfg), default=str).encode()
                    finally:
                        conn.close()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/api/health"):
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(b'{"ok":true,"local":true}'); return
            return super().do_GET()

        def log_message(self, *a: Any) -> None:  # quiet
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    log.info("dashboard: serving %s (local, no password) — Ctrl+C to stop", url)
    print(f"Dashboard: {url}   (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
