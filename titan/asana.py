"""Minimal Asana REST client for the email-approval board (free; personal access token in .env as ASANA_TOKEN).
Only the calls the approval flow needs."""
from __future__ import annotations

import logging
import time

from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)
API = "https://app.asana.com/api/1.0"
BOT = "🤖"   # prefix on comments the engine writes, so Rodrigo's own comments can be told apart (same Asana user)


class AsanaError(Exception):
    pass


class Asana:
    def __init__(self, token: str, session: requests.Session | None = None):
        self.s = session or requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def _req(self, method: str, path: str, **kw) -> Any:
        # 2026-10-29: a round reads comments on 100+ cards; Asana's 150 requests/min limit answered 429 and the whole
        # round died before the approved emails were sent ("it reads some and not others"). Wait and retry instead.
        for attempt in range(5):
            r = self.s.request(method, f"{API}{path}", timeout=60, **kw)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 4:
                wait = min(90, int((r.headers.get("Retry-After") or "0") or 0) or 5 * (attempt + 1))
                log.warning("asana %s %s: HTTP %s, retrying in %ss", method, path, r.status_code, wait)
                time.sleep(wait)
                continue
            if r.status_code >= 400:
                raise AsanaError(f"{method} {path}: HTTP {r.status_code} {r.text[:300]}")
            return (r.json() or {}).get("data")

    def create_task(self, project: str, section: str, name: str, notes: str) -> dict[str, Any]:
        return self._req("POST", "/tasks", json={"data": {"name": name, "notes": notes, "projects": [project],
                                                          "memberships": [{"project": project, "section": section}]}})

    def create_task_html(self, project: str, section: str, name: str, html_notes: str) -> dict[str, Any]:
        return self._req("POST", "/tasks", json={"data": {"name": name, "html_notes": html_notes, "projects": [project],
                                                          "memberships": [{"project": project, "section": section}]}})

    def comment_html(self, task: str, html_text: str) -> None:
        """html_text must be a <body>…</body> that starts with BOT, so the engine reads it as its own comment."""
        self._req("POST", f"/tasks/{task}/stories", json={"data": {"html_text": html_text}})

    def update_notes(self, task: str, notes: str) -> None:
        self._req("PUT", f"/tasks/{task}", json={"data": {"notes": notes}})

    def attach(self, task: str, path: Path, name: str | None = None) -> None:
        with open(path, "rb") as fh:
            self._req("POST", "/attachments", data={"parent": task, "resource_subtype": "asana"},
                      files={"file": (name or path.name, fh, "image/png")})

    def section_tasks(self, section: str) -> list[dict[str, Any]]:
        return self._req("GET", f"/sections/{section}/tasks", params={"opt_fields": "name,notes,created_at,modified_at", "limit": 100}) or []

    def project_tasks(self, project: str, completed_since: str = "now") -> list[dict[str, Any]]:
        """Every card on the board with its column (paged). completed_since "now" = open cards only; an ISO time also
        returns the cards completed since then."""
        out: list[dict[str, Any]] = []
        params: dict[str, Any] = {"opt_fields": "name,completed,completed_at,created_at,modified_at,memberships.section.name,"
                                                "memberships.project", "limit": 100, "completed_since": completed_since}
        while True:
            r = self.s.request("GET", f"{API}/projects/{project}/tasks", params=params, timeout=60)
            if r.status_code >= 400:
                raise AsanaError(f"GET /projects/{project}/tasks: HTTP {r.status_code} {r.text[:300]}")
            body = r.json() or {}
            out += body.get("data") or []
            nxt = (body.get("next_page") or {}).get("offset")
            if not nxt:
                return out
            params["offset"] = nxt

    def stories(self, task: str) -> list[dict[str, Any]]:
        """A card's full history: comments plus Asana's own "moved this task from X to Y" stories (section_changed)."""
        return self._req("GET", f"/tasks/{task}/stories", params={
            "opt_fields": "type,resource_subtype,text,created_at,old_section.name,new_section.name"}) or []

    def task_state(self, task: str) -> dict[str, Any] | None:
        """The card, or None when it was deleted (Asana answers 404)."""
        try:
            return self._req("GET", f"/tasks/{task}", params={"opt_fields": "name,completed,completed_at,memberships.project"}) or {}
        except AsanaError as e:
            if "HTTP 404" in str(e):
                return None
            raise

    def comments(self, task: str) -> list[dict[str, Any]]:
        stories = self._req("GET", f"/tasks/{task}/stories", params={"opt_fields": "type,resource_subtype,text,created_at"}) or []
        return [s for s in stories if s.get("type") == "comment" or s.get("resource_subtype") == "comment_added"]

    def comment(self, task: str, text: str) -> None:
        self._req("POST", f"/tasks/{task}/stories", json={"data": {"text": f"{BOT} {text}"}})

    def comment_raw(self, task: str, text: str) -> None:
        """A comment without the engine's prefix: the engine reads it as Rodrigo's own feedback (card chat forwards his change)."""
        self._req("POST", f"/tasks/{task}/stories", json={"data": {"text": text}})

    def complete(self, task: str) -> None:
        self._req("PUT", f"/tasks/{task}", json={"data": {"completed": True}})

    def task(self, task: str) -> dict[str, Any]:
        return self._req("GET", f"/tasks/{task}", params={"opt_fields": "name,notes"}) or {}

    def move(self, task: str, section: str) -> None:
        self._req("POST", f"/sections/{section}/addTask", json={"data": {"task": task}})

    def sections(self, project: str) -> list[dict[str, Any]]:
        return self._req("GET", f"/projects/{project}/sections", params={"opt_fields": "name"}) or []

    def ensure_section(self, project: str, name: str, insert_before: str | None = None) -> str:
        """The gid of the section called `name` on the board, created (before `insert_before`) when it does not exist yet."""
        for sec in self.sections(project):
            if (sec.get("name") or "").strip().lower() == name.strip().lower():
                return sec["gid"]
        data: dict[str, Any] = {"name": name}
        if insert_before:
            data["insert_before"] = insert_before
        return self._req("POST", f"/projects/{project}/sections", json={"data": data})["gid"]

    def rename_section(self, section: str, name: str) -> None:
        self._req("PUT", f"/sections/{section}", json={"data": {"name": name}})
