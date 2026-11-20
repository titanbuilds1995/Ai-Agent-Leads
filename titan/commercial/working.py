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
from .board import COLUMNS as MAIN_COLUMNS, NEEDS_ME, NEEDS_REVISION, NOT_A_FIT, REVIEWED, column_key

BOARD_NAME = "Titan 1031 · Working Deals"
WAITING = "Waiting on the broker"
# Working Deals column → the 1031 column the engine already speaks of
COLUMNS = {NEEDS_ME: NEEDS_ME, NEEDS_REVISION: NEEDS_REVISION, REVIEWED: REVIEWED, WAITING: "In touch with the agent",
           "Books received": "Books received", "Offer out": "Offer out", "Closed": "Closed", NOT_A_FIT: NOT_A_FIT}


MEDICAL_NAME = "Titan 1031 · Medical"
# Rodrigo, 2026-11-20: "a new board with just medical buildings". Same columns as the 1031 board, so everything works there as it
# does on the 1031 board: drag to "Approved · send OM request" sends, answers / books / reads land on the card (medical.py).
MEDICAL_COLUMNS = {c: c for c in MAIN_COLUMNS}


def ensure_working_board(asana, project: str, columns: dict[str, str] | None = None) -> dict[str, str]:
    """{1031 column key: section gid on that board}, creating any column that is missing (in the order above, Not a fit last)."""
    columns = columns or COLUMNS
    have: dict[str, str] = {}
    for sec in asana.sections(project):
        key = column_key(sec.get("name"))
        if key:
            have[key] = sec["gid"]
    for name, key in columns.items():
        if key not in have:
            have[key] = asana.ensure_section(project, name)
    from .board import last_at_end
    last_at_end(asana, project, have.get(NOT_A_FIT))
    return have


class CommercialAsana:
    """The Asana client, with the 1031 board, Working Deals and the Medical board seen as one board (see the module doc)."""

    def __init__(self, inner, main: str, working: str | None = None, medical: str | None = None):
        self._a, self.main = inner, str(main)
        self.working = str(working) if working else None
        self.medical = str(medical) if medical else None
        self._loaded = False
        self._main_secs: dict[str, str] = {}              # 1031 section gid → column key
        self._secs: dict[str, dict[str, str]] = {}        # other board → {column key: its section gid}
        self._board_of: dict[str, str] = {}               # card gid → the other board it lives on

    def __getattr__(self, name: str) -> Any:
        return getattr(self._a, name)

    @property
    def inner(self):
        return self._a

    def _others(self) -> list[tuple[str, dict[str, str]]]:
        out = []
        if self.working:
            out.append((self.working, COLUMNS))
        if self.medical:
            out.append((self.medical, MEDICAL_COLUMNS))
        return out

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        for sec in self._a.sections(self.main):
            key = column_key(sec.get("name"))
            if key:
                self._main_secs[sec["gid"]] = key
        for board, columns in self._others():
            self._secs[board] = ensure_working_board(self._a, board, columns)
            for t in self._a.project_tasks(board):
                self._board_of.setdefault(t["gid"], board)
                projects = {str((m.get("project") or {}).get("gid")) for m in t.get("memberships") or []}
                if self.main in projects:          # it was put on the other board and is still on the 1031 board: one place only
                    try:
                        self._a.remove_project(t["gid"], self.main)
                        log.info("%s: %s taken off the 1031 board", board, t.get("name"))
                    except Exception as e:  # noqa: BLE001
                        log.warning("%s: could not take %s off the 1031 board: %s", board, t["gid"], e)

    def on_working(self, task: str) -> bool:
        self._load()
        return bool(self.working) and self._board_of.get(task) == self.working

    def on_medical(self, task: str) -> bool:
        self._load()
        return bool(self.medical) and self._board_of.get(task) == self.medical

    def medical_sections(self) -> dict[str, str]:
        """{column: section gid} on the Medical board ({} when there is none)."""
        self._load()
        return dict(self._secs.get(self.medical or "", {}))

    def placed(self, task: str, board: str) -> None:
        """A card was just created on / moved to one of the other boards: engine moves stay there from now on."""
        self._load()
        self._board_of[task] = str(board)

    def move(self, task: str, section: str) -> None:
        self._load()
        key = self._main_secs.get(section)
        board = self._board_of.get(task)
        if key and board:
            target = self._secs.get(board, {}).get(key)
            if target:
                return self._a.move(task, target)
            log.info("%s: no %r column there, card %s stays where it is", board, key, task)
            return None
        return self._a.move(task, section)

    def section_tasks(self, section: str) -> list[dict[str, Any]]:
        self._load()
        out = list(self._a.section_tasks(section))
        key = self._main_secs.get(section)
        if key:
            seen = {t["gid"] for t in out}
            for board, _ in self._others():
                other = self._secs.get(board, {}).get(key)
                if other and other != section:
                    more = [t for t in self._a.section_tasks(other) if t["gid"] not in seen]
                    seen |= {t["gid"] for t in more}
                    out += more
        return out

    def project_tasks(self, project: str, completed_since: str = "now") -> list[dict[str, Any]]:
        out = list(self._a.project_tasks(project, completed_since))
        if str(project) == self.main:
            self._load()
            seen = {t["gid"] for t in out}
            for board, _ in self._others():
                more = [t for t in self._a.project_tasks(board, completed_since) if t["gid"] not in seen]
                seen |= {t["gid"] for t in more}
                out += more
        return out


def wrap(cfg: Any, asana):
    """The client every commercial stage uses: wrapped when `commercial.asana.working_project` or `medical_project` is set."""
    main, working, medical = (cfg.get("commercial.asana.project"), cfg.get("commercial.asana.working_project"),
                              cfg.get("commercial.asana.medical_project"))
    if asana is None or not main or not (working or medical) or isinstance(asana, CommercialAsana):
        return asana
    return CommercialAsana(asana, main, working, medical)
