"""Promises an email cannot keep (Rodrigo, 2026-11-12, Eric Cederman: "we told them we would attach a signed document and we did not").

Charles never attaches anything to an agent or broker, and never signs anything. So any outgoing email that says a file is
attached / enclosed / signed / sent over, while nothing is attached to the message, is false. `GmailClient.send` checks every
email to an outside address with `unbacked_claims()` and refuses to send it: no "send it", no autopilot, no fixed reply gets
past this. Mail to our own domain (reports, forwards to Rodrigo) is not checked: forwards carry the real files.
"""
from __future__ import annotations

import re

CLAIM_RE = re.compile(
    r"\b(?:attached|attaching|i(?:'ve| have) attached|we(?:'ve| have) attached|please find|find (?:it |them )?(?:attached|enclosed)|"
    r"see (?:the )?attached|enclosed|here (?:is|are) the (?:signed|executed)|"
    r"(?:signed|executed|countersigned) (?:copy|nda|ca|confidentiality|agreement|contract|loi|document|form|psa)|"
    r"i(?:'ve| have) (?:just )?(?:signed|sent (?:it|them|that|the \w+) over|uploaded)|we(?:'ve| have) (?:just )?(?:signed|sent (?:it|them|that|the \w+) over|uploaded)|"
    r"(?:i'?ll send|we'?ll send|will send) (?:it|them|the \w+(?: \w+)?) (?:over|shortly|now|right over|back signed))\b", re.I)
QUOTE_START_RE = re.compile(r"^\s*(?:>|On .+wrote:\s*$|-{2,}\s*Original Message|From: .+@)", re.I)
SIGNATURE_RE = re.compile(r"\s*(?:Charles Grant|Junior Acquisitions|Titan Realty|Office Line)", re.I)


class UnbackedClaim(Exception):
    """An email says something is attached or signed and nothing is attached. Never sent, never retried as is."""

    def __init__(self, phrases: list[str]):
        self.phrases = phrases
        super().__init__(stop_message(phrases))


def own_text(body: str) -> str:
    """Our words only: quoted history and the signature dropped."""
    out = []
    for ln in (body or "").splitlines():
        if QUOTE_START_RE.match(ln):
            break
        if not SIGNATURE_RE.match(ln):
            out.append(ln)
    return "\n".join(out)


def unbacked_claims(body: str, attachments: list | None = None) -> list[str]:
    """The phrases in an email that promise a file or a signature, when no file goes with it (empty = fine to send)."""
    if attachments:
        return []
    return sorted({m.group(0).lower() for m in CLAIM_RE.finditer(own_text(body))})


STOP_PREFIX = "STOPPED, not sent: the email says"


def stop_message(phrases: list[str]) -> str:
    said = ", ".join(f'"{p}"' for p in phrases[:3])
    return (f"{STOP_PREFIX} {said} but nothing is attached, and Charles never attaches or signs anything. "
            "Comment what to say instead and it is rewritten, or send the file yourself from Gmail.")
