"""Titan 1031 · Working Deals: the second board (Rodrigo, 2026-11-10: "let's make another board where I can add everybody that I'm in
communication with and that I've already finished filtering").

He moves a card there himself from the 1031 board. Everything the engine does on the 1031 board keeps working on that card: comments,
reads, notes, files, Needs Me / Needs Revision / Reviewed, deal sheets, offers. The engine only ever MOVES it between the Working Deals
columns (never back to the 1031 board), and a card that sits on both boards is taken off the 1031 board so he sees it once.

How: every commercial stage gets the Asana client wrapped in `CommercialAsana`. A move to a 1031 column is sent to the same column on
Working Deals when the card lives there ("In touch with the agent" is called "Waiting on the broker" there; New / Approved · send OM
request / Good fit have no Working Deals column, so such a move leaves the card where he put it). Reading a 1031 column or the 1031
board also returns the Working Deals cards of that column."""
from __future__ import annotations

from typing import Any

from ..util import log
from .board import NEEDS_ME, NEEDS_REVISION, NOT_A_FIT, REVIEWED, column_key

BOARD_NAME = "Titan 1031 · Working Deals"
WAITING = "Waiting on the broker"
# Working Deals column → the 1031 column the engine already speaks of
COLUMNS = {NEEDS_ME: NEEDS_ME, NEEDS_REVISION: NEEDS_REVISION, REVIEWED: REVIEWED, WAITING: "In touch with the agent",
           "Books received": "Books received", "Offer out": "Offer out", "Closed": "Closed", NOT_A_FIT: NOT_A_FIT}


def ensure_working_board(asana, project: str) -> dict[str, str]:
    """{1031 column key: Working Deals section gid}, creating any column that is missing (in the order above, Not a fit last)."""
    have: dict[str, str] = {}
    for sec in asana.sections(project):
        key = column_key(sec.get("name"))
        if key:
            have[key] = sec["gid"]
    for name, key in COLUMNS.items():
        if key not in have:
            have[key] = asana.ensure_section(project, name)
    from .board import last_at_end
    last_at_end(asana, project, have.get(NOT_A_FIT))
    return have


class CommercialAsana:
    """The Asana client, with the 1031 board and Working Deals seen as one board (see the module doc)."""

    def __init__(self, inner, main: str, working: str):
        self._a, self.main, self.working = inner, str(main), str(working)
        self._loaded = False
        self._main_secs: dict[str, str] = {}      # 1031 section gid → column key
        self._work: dict[str, str] = {}           # column key → Working Deals section gid
        self._on_work: set[str] = set()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._a, name)

    @property
    def inner(self):
        return self._a

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        for sec in self._a.sections(self.main):
            key = column_key(sec.get("name"))
            if key:
                self._main_secs[sec["gid"]] = key
        self._work = ensure_working_board(self._a, self.working)
        for t in self._a.project_tasks(self.working):
            self._on_work.add(t["gid"])
            projects = {str((m.get("project") or {}).get("gid")) for m in t.get("memberships") or []}
            if self.main in projects:          # he added it to Working Deals and it is still on the 1031 board: one place only
                try:
                    self._a.remove_project(t["gid"], self.main)
                    log.info("working deals: %s taken off the 1031 board", t.get("name"))
                except Exception as e:  # noqa: BLE001
                    log.warning("working deals: could not take %s off the 1031 board: %s", t["gid"], e)

    def on_working(self, task: str) -> bool:
        self._load()
        return task in self._on_work

    def move(self, task: str, section: str) -> None:
        self._load()
        key = self._main_secs.get(section)
        if key and task in self._on_work:
            target = self._work.get(key)
            if target:
                return self._a.move(task, target)
            log.info("working deals: no %r column there, card %s stays where it is", key, task)
            return None
        return self._a.move(task, section)

    def section_tasks(self, section: str) -> list[dict[str, Any]]:
        self._load()
        out = list(self._a.section_tasks(section))
        key = self._main_secs.get(section)
        if key and self._work.get(key) and self._work[key] != section:
            seen = {t["gid"] for t in out}
            out += [t for t in self._a.section_tasks(self._work[key]) if t["gid"] not in seen]
        return out

    def project_tasks(self, project: str, completed_since: str = "now") -> list[dict[str, Any]]:
        out = list(self._a.project_tasks(project, completed_since))
        if str(project) == self.main:
            self._load()
            seen = {t["gid"] for t in out}
            out += [t for t in self._a.project_tasks(self.working, completed_since) if t["gid"] not in seen]
        return out


def wrap(cfg: Any, asana):
    """The client every commercial stage uses: wrapped when config `commercial.asana.working_project` is set."""
    main, working = cfg.get("commercial.asana.project"), cfg.get("commercial.asana.working_project")
    if asana is None or not main or not working or isinstance(asana, CommercialAsana):
        return asana
    return CommercialAsana(asana, main, working)
