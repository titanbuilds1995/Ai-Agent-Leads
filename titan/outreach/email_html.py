"""The HTML version of an agent email (Rodrigo, 2026-10-27: Lise's offer "does not look right, make it look like a regular email").

A text-only message is shown by Gmail in a narrow column with hard-looking line breaks and no formatting. Every email we
send now carries, next to the plain text, the same HTML that Gmail's own composer would produce: one <div> per line,
<div><br></div> for a blank line, and a bulleted list when a block is a list of terms ("Price: ...", "Closing: ..."). No
fonts or colours are set, so the agent sees it in their mail client's normal font, exactly like a hand-typed email.
"""
from __future__ import annotations

import html
import re

TERM_RE = re.compile(r"^[A-Z][^:]{0,30}:\s\S")   # "Price: $209,000, cash, as is"
SIGN_OFF_RE = re.compile(r"^(Best regards|Best|Regards|Thanks|Thank you|Cheers|Talk soon|Kind regards|Sincerely),?$", re.I)


def _esc(line: str) -> str:
    return html.escape(line, quote=False)


def _lines(block: str) -> str:
    return "".join(f"<div>{_esc(x)}</div>" if x.strip() else "<div><br></div>" for x in block.split("\n"))


def _block_html(block: str, signature: bool) -> str:
    lines = block.split("\n")
    if not signature and len(lines) >= 2 and all(TERM_RE.match(x) for x in lines):
        items = "".join(f"<li>{_esc(x)}</li>" for x in lines)
        return f'<ul style="margin:0;padding-left:1.5em">{items}</ul>'
    return _lines(block)


def body_html(body: str) -> str:
    """Gmail-composer style HTML for a plain-text email body. Paragraphs are split on blank lines; a block of several
    "Label: value" lines becomes a bulleted list; the sign-off and signature keep their lines."""
    text = body.replace("\r\n", "\n").strip("\n")
    blocks = [b for b in re.split(r"\n\s*\n", text)]
    out: list[str] = []
    signature = False
    for b in blocks:
        b = b.strip("\n")
        if SIGN_OFF_RE.match(b.strip()):
            signature = True
        out.append(_block_html(b, signature))
    return '<div dir="ltr">' + "<div><br></div>".join(out) + "</div>"
