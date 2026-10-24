"""A picture of an email as Gmail would show it (plain text, From/To/Subject header), for the Asana approval cards."""
from __future__ import annotations

import textwrap
from pathlib import Path


def render(path: Path, sender: str, to: str, subject: str, body: str, width: int = 900) -> Path:
    from PIL import Image, ImageDraw, ImageFont   # optional dependency; only needed when posting approval cards

    def font(size: int, bold: bool = False):
        for name in (("Arial Bold.ttf", "DejaVuSans-Bold.ttf") if bold else ("Arial.ttf", "DejaVuSans.ttf")):
            for d in ("/System/Library/Fonts/Supplemental/", "/Library/Fonts/", "/usr/share/fonts/truetype/dejavu/", ""):
                try:
                    return ImageFont.truetype(d + name, size)
                except OSError:
                    continue
        return ImageFont.load_default()

    f_body, f_meta, f_subj = font(17), font(15), font(22, bold=True)
    pad, line_h = 40, 26
    lines: list[str] = []
    for para in body.split("\n"):
        lines += textwrap.wrap(para, 88) or [""]
    h = pad * 2 + 130 + line_h * len(lines)
    img = Image.new("RGB", (width, h), "#ffffff")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, width, 6], fill="#1a73e8")
    d.text((pad, pad), subject, font=f_subj, fill="#202124")
    d.text((pad, pad + 42), f"From: {sender}", font=f_meta, fill="#5f6368")
    d.text((pad, pad + 64), f"To: {to}", font=f_meta, fill="#5f6368")
    d.line([pad, pad + 100, width - pad, pad + 100], fill="#e0e0e0", width=1)
    y = pad + 120
    for ln in lines:
        d.text((pad, y), ln, font=f_body, fill="#202124" if not ln.startswith("--") else "#80868b")
        y += line_h
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return path
