"""Minimal Asana REST client for the email-approval board (free; personal access token in .env as ASANA_TOKEN).
Only the calls the approval flow needs."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

API = "https://app.asana.com/api/1.0"
BOT = "🤖"   # prefix on comments the engine writes, so Rodrigo's own comments can be told apart (same Asana user)


class AsanaError(Exception):
    pass


class Asana:
    def __init__(self, token: str, session: requests.Session | None = None):
        self.s = session or requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def _req(self, method: str, path: str, **kw) -> Any:
        r = self.s.request(method, f"{API}{path}", timeout=60, **kw)
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
