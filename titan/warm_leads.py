"""Rodrigo's "Titan Warm Leads" board (2026-10-28): agents who really answered, moved there by hand off the Email Approvals
board. Before this the engine never looked at it, so a comment on Lise's card sat unanswered for a day and Peter's card
never got its status (Rodrigo, 2026-10-29: "are we making sure the cron job is ... reading the needs me?").

`WarmBoard` wraps the Asana client for one round: the warm cards are read like lead cards on the approvals board (each
column stands in for its approvals twin), and every move the engine makes on one of them lands in the matching warm column
instead, so the card never jumps back to the approvals board. Reply cards stay on the approvals board (sending happens there)."""
from __future__ import annotations

from typing import Any


def warm_cfg(cfg: Any) -> dict[str, str]:
    return (cfg.get("outreach.asana", {}) or {}).get("warm_leads") or {}


class WarmBoard:
    def __init__(self, asana, cfg: Any, nm: str, nr: str, sent: str | None, answered: str | None):
        w = warm_cfg(cfg)
        self._a, self.w = asana, w
        self.to_warm = {k: v for k, v in {nm: w.get("needs_me"), nr: w.get("needs_revision"), sent: w.get("waiting"),
                                          answered: w.get("closed")}.items() if k and v}
        self.from_warm = {k: v for k, v in {w.get("needs_me"): nm, w.get("needs_revision"): nr, w.get("waiting"): sent,
                                            w.get("offer_out"): sent, w.get("closed"): answered}.items() if k and v}
        self.here: dict[str, str] = {}      # warm card gid -> the warm column it sits in

    def __getattr__(self, name):
        return getattr(self._a, name)

    def lead_cards(self) -> list[tuple[dict[str, Any], str]]:
        """Every card on the warm board, paired with the approvals column it stands in for."""
        out = []
        for sec, twin in self.from_warm.items():
            for t in self._a.section_tasks(sec):
                self.here[t["gid"]] = sec
                out.append((t, twin))
        return out

    def is_warm(self, task: str) -> bool:
        return task in self.here

    def move(self, task: str, section: str) -> None:
        if task not in self.here:
            return self._a.move(task, section)
        dest = self.to_warm.get(section, section)
        cur = self.here[task]
        if dest == cur or (cur == self.w.get("offer_out") and dest == self.w.get("waiting")):
            return None                      # already there; a card he put in "Offer out" stays there while we wait on the agent
        self._a.move(task, dest)
        if dest in self.from_warm:
            self.here[task] = dest
        else:
            self.here.pop(task, None)        # sent somewhere else on purpose (e.g. Approved): no longer a warm card

    def offer_out(self, task: str) -> bool:
        """Waiting on the agent with an offer on the table → the "Offer out" column."""
        dest = self.w.get("offer_out")
        if not dest or self.here.get(task) in (None, dest):
            return False
        self._a.move(task, dest)
        self.here[task] = dest
        return True


def wrap(cfg: Any, asana, nm: str, nr: str, sent: str | None, answered: str | None):
    """The client unchanged when no warm board is configured, else a WarmBoard (never wrapped twice)."""
    if isinstance(asana, WarmBoard) or not warm_cfg(cfg).get("project"):
        return asana
    return WarmBoard(asana, cfg, nm, nr, sent, answered)
