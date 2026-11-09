"""The deals email (Rodrigo, 2026-11-08: "I'm a visual guy"): Titan house style, one photo card per listing with the facts and
a "why this fits" line, each card linking to the property's page on the site. Same tables-and-inline-styles rules as the
morning report so Gmail and the iPhone draw it. The same email is what he forwards to the investor, so internal numbers
(score parts, offer anchor, cooked-books flags) stay out of it; they live on the private site view and the Asana card."""
from __future__ import annotations

from typing import Any

from ..report_html import FAINT, GOLD, GOLD_INK, GOLD_SOFT, INK, LINE, MONO, MUTED, NAVY, PAPER, SANS, SERIF, SUNK, SURFACE, e, label, pill
from ..util import money
from .why import photo_url, why_fits

IN_BOX, CLEAN, NEEDS_OM = "in_box", "clean", "needs_om"
COL_COLORS = {"Good fit": ("#16834A", "#E7F1EA"), "In touch with the agent": (GOLD_INK, GOLD_SOFT), "Books received": (GOLD_INK, GOLD_SOFT),
              "Offer out": ("#16834A", "#E7F1EA"), "New": (MUTED, SUNK)}


def clock_text(L: dict[str, Any]) -> str:
    c = L.get("om_request") or {}
    if not c:
        return ""
    if c.get("bounced"):
        return " · email bounced"
    if c.get("hours_to_reply") is not None:
        return f" · answered in {c['hours_to_reply']:g} h" + (" · books in" if c.get("state") == "BOOKS" else "")
    return " · books requested, no answer yet"


def deal_link(L: dict[str, Any], cfg: Any) -> str:
    base = (cfg.get("commercial.site.url") or "").rstrip("/")
    key = cfg.secret("DEALS_SHARE_KEY") or ""
    if base:
        return f"{base}/1031.html?k={key}#{L['listing_id']}" if key else f"{base}/1031.html#{L['listing_id']}"
    return L.get("listing_url") or "#"


def card(L: dict[str, Any], cfg: Any, size: str) -> str:
    photo = photo_url(L, size)
    link = deal_link(L, cfg)
    facts = [money(L.get("price")) if L.get("price") else "price not disclosed",
             f"{L['stated_cap']:.2f}% cap" if L.get("stated_cap") else "cap on request",
             f"{L['building_sf']:,} SF" if L.get("building_sf") else "", f"{L['units']} units" if (L.get("units") or 0) > 1 else "",
             f"built {L['year_built']}" if L.get("year_built") else "", f"class {str(L['building_class'])[:1].upper()}" if L.get("building_class") else "",
             f"{L['airport_minutes']} min to {L['airport_code']}" if L.get("airport_code") else ""]
    col = L.get("board_column") or "New"
    color, bg = COL_COLORS.get(col, (MUTED, SUNK))
    img = (f'<a href="{e(link)}" style="text-decoration:none"><img src="{e(photo)}" width="100%" alt="" style="display:block;width:100%;max-height:260px;'
           f'object-fit:cover;border-radius:10px 10px 0 0;border:0"></a>' if photo else
           f'<div style="height:120px;background:{SUNK};border-radius:10px 10px 0 0;font-family:{MONO};font-size:10px;letter-spacing:.14em;'
           f'text-transform:uppercase;color:{FAINT};text-align:center;line-height:120px">no photo on LoopNet</div>')
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{SURFACE};border:1px solid {LINE};'
            f'border-radius:10px;margin:0 0 14px"><tr><td style="padding:0">{img}</td></tr><tr><td style="padding:14px 18px 16px">'
            f'<div style="margin-bottom:6px">{pill(col, color, bg)} &nbsp;{pill((L.get("asset_class") or L.get("property_type") or "commercial").upper(), MUTED, SUNK)}</div>'
            f'<a href="{e(link)}" style="font-family:{SERIF};font-size:20px;line-height:1.2;color:{INK};text-decoration:none">{e(L.get("street"))}</a>'
            f'<div style="font-family:{SANS};font-size:13px;color:{MUTED};margin-top:2px">{e(L.get("city"))} · {e(L.get("county") or "?")} County · {e(L.get("region") or "")}</div>'
            f'<div style="font-family:{SANS};font-size:14px;color:{INK};margin-top:10px">{e(" · ".join(x for x in facts if x))}</div>'
            f'<div style="margin-top:12px">{label("Why this fits", GOLD)}</div>'
            f'<div style="font-family:{SANS};font-size:13.5px;line-height:1.5;color:{INK}">{e(why_fits(L, cfg))}</div>'
            f'<div style="font-family:{SANS};font-size:12px;color:{MUTED};margin-top:10px">{e(L.get("broker_name") or "")}'
            f'{" · " + e(L["brokerage"]) if L.get("brokerage") else ""}{clock_text(L)}</div>'
            f'<div style="margin-top:12px"><a href="{e(link)}" style="font-family:{MONO};font-size:11px;letter-spacing:.12em;text-transform:uppercase;'
            f'color:{GOLD_INK};text-decoration:none">See the property →</a></div>'
            f'</td></tr></table>')


def build_email(listings: list[dict[str, Any]], cfg: Any, run_date: str, counts: dict[str, Any], intro: str | None = None) -> tuple[str, str]:
    """(html, text). Listings come ranked; only in-box ones are shown, up to commercial.email.max_cards."""
    size = str(cfg.get("commercial.email.photo_size", "116"))
    n = int(cfg.get("commercial.email.max_cards", 40))
    rows = [L for L in listings if L.get("in_box") and (L.get("board_column") or "New") != "Not a fit" and L.get("status") != "gone"][:n]
    base = (cfg.get("commercial.site.url") or "").rstrip("/")
    key = cfg.secret("DEALS_SHARE_KEY") or ""
    site = f"{base}/1031.html" + (f"?k={key}" if key else "") if base else ""
    head = (f'<tr><td style="background:{NAVY};padding:18px 24px;border-bottom:3px solid {GOLD}">'
            f'<div style="font-family:{MONO};font-size:11px;letter-spacing:.3em;text-transform:uppercase;color:#fff">Titan <span style="color:{GOLD}">Realty Investments</span></div>'
            f'<div style="font-family:{MONO};font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:rgba(255,255,255,.6);margin-top:4px">1031 buyer · commercial deals · {e(run_date)}</div></td></tr>')
    summary = (f'<tr><td style="padding:22px 24px 6px;font-family:{SANS};color:{INK}">'
               f'<div style="font-family:{SERIF};font-size:24px;line-height:1.2">{len(rows)} deals worth a look</div>'
               f'<div style="font-family:{SANS};font-size:13.5px;color:{MUTED};margin-top:6px">{e(intro or "")}'
               f'{e(f"{counts.get(IN_BOX, 0)} listings in the box on LoopNet · {counts.get(CLEAN, 0)} with clean numbers · {counts.get(NEEDS_OM, 0)} waiting on the books")}'
               + (f' · <a href="{e(site)}" style="color:{GOLD_INK}">open the site</a>' if site else "") + "</div></td></tr>")
    cards = "".join(f'<tr><td style="padding:8px 24px 0">{card(L, cfg, size)}</td></tr>' for L in rows)
    foot = (f'<tr><td style="padding:18px 24px 28px;font-family:{SANS};font-size:12px;color:{FAINT}">Figures as stated on LoopNet; nothing here is verified until '
            f'the trailing twelve and rent roll are in. Titan Realty Investments · Office Line +1 (561) 405 7379</td></tr>')
    html = (f'<!doctype html><html><body style="margin:0;padding:0;background:{PAPER}"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:{PAPER}"><tr><td align="center" style="padding:16px 8px"><table role="presentation" width="640" cellpadding="0" cellspacing="0" '
            f'style="max-width:640px;width:100%;background:{PAPER}">{head}{summary}{cards}{foot}</table></td></tr></table></body></html>')
    text = [f"Titan 1031 · commercial deals · {run_date}", f"{len(rows)} deals worth a look", ""]
    for L in rows:
        text += [f"{L.get('street')}, {L.get('city')} ({L.get('county')}) · {money(L.get('price')) if L.get('price') else 'price n/a'} · "
                 f"{str(L.get('stated_cap')) + '% cap' if L.get('stated_cap') else 'cap on request'} · {L.get('asset_class')}",
                 f"  Why: {why_fits(L, cfg)}", f"  {deal_link(L, cfg)}", ""]
    return html, "\n".join(text)
