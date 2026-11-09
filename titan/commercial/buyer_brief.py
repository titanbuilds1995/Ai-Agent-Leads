"""The buyer brief (Rodrigo, 2026-11-09): a one-page PDF for the 1031 buyer and their agent. What we found (live numbers from the
screen), what happens this week (book requests Monday, analysis as the books come in, review call Wednesday), how we filter, and
what we need from them (proof of funds, confidentiality agreements). Titan house style, same engine as the Top-25 PDF."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..util import money
from .site import load_listings


def week_dates(today: date | None = None) -> dict[str, date]:
    """Monday = the next Monday (today if today is Monday); Wednesday = two days after."""
    t = today or date.today()
    monday = t + timedelta(days=(0 - t.weekday()) % 7)
    return {"today": t, "monday": monday, "wednesday": monday + timedelta(days=2), "friday": monday + timedelta(days=4)}


def numbers(conn) -> dict[str, Any]:
    rows = load_listings(conn)
    total = conn.execute("SELECT COUNT(*) FROM commercial_listings").fetchone()[0]
    in_box = [L for L in rows if L.get("in_box")]
    by_type = Counter((L.get("asset_class") or "other") for L in in_box)
    by_region = Counter((L.get("region") or "Unknown") for L in in_box)
    counties = {L.get("county") for L in in_box if L.get("county")}
    brokers = {(L.get("broker_email") or L.get("broker_name") or "").lower() for L in in_box}
    with_cap = sum(1 for L in in_box if L.get("stated_cap"))
    caps = sorted(L["stated_cap"] for L in in_box if L.get("stated_cap"))
    prices = [L["price"] for L in in_box if L.get("price")]
    contacted = conn.execute("SELECT COUNT(DISTINCT listing_id) FROM commercial_threads").fetchone()[0]
    answered = conn.execute("SELECT COUNT(*) FROM commercial_threads WHERE first_reply_at IS NOT NULL").fetchone()[0]
    books = conn.execute("SELECT COUNT(*) FROM commercial_threads WHERE state='BOOKS'").fetchone()[0]
    clean = sum(1 for L in in_box if L.get("tag") == "CLEAN")
    return {"screened": total, "in_box": len(in_box), "by_type": by_type.most_common(), "by_region": by_region.most_common(),
            "counties": len(counties), "brokers": len(b for b in [brokers] for b in b if b) if False else len([b for b in brokers if b]),
            "with_cap": with_cap, "cap_median": caps[len(caps) // 2] if caps else None, "cap_max": caps[-1] if caps else None,
            "price_low": min(prices) if prices else None, "price_high": max(prices) if prices else None,
            "contacted": contacted, "answered": answered, "books": books, "clean": clean}


TYPE_LABEL = {"nnn": "Single-tenant net lease", "retail": "Retail centers", "medical": "Medical", "hotel": "Hotels", "industrial": "Industrial / flex",
              "office": "Office", "multifamily": "Multifamily (5+ units)"}


def build(conn, cfg: Any, path: Path, today: date | None = None) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    NAVY, GOLD, INK, MUTED, PAPER, LINE = colors.HexColor("#0C1C35"), colors.HexColor("#B99455"), colors.HexColor("#16203A"), colors.HexColor("#5B6472"), colors.HexColor("#F4F2EC"), colors.HexColor("#E1DCD0")
    n = numbers(conn)
    d = week_dates(today)
    box = cfg.get("commercial.buyer_box", {})
    buyer = cfg.get("commercial.buyer_name") or "1031 Exchange Buyer"
    op = cfg["operator"]
    h1 = ParagraphStyle("h1", fontName="Times-Bold", fontSize=22, leading=26, textColor=INK, spaceAfter=2)
    sub = ParagraphStyle("sub", fontName="Helvetica", fontSize=10, leading=13, textColor=MUTED, spaceAfter=8)
    h2 = ParagraphStyle("h2", fontName="Times-Bold", fontSize=14, leading=17, textColor=INK, spaceBefore=12, spaceAfter=5)
    eye = ParagraphStyle("eye", fontName="Courier", fontSize=7.5, leading=9, textColor=GOLD, spaceBefore=10)
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=14, textColor=INK)
    small = ParagraphStyle("small", fontName="Helvetica", fontSize=8.5, leading=11.5, textColor=MUTED)
    big = ParagraphStyle("big", fontName="Times-Bold", fontSize=26, leading=28, textColor=INK)
    lab = ParagraphStyle("lab", fontName="Courier", fontSize=7, leading=9, textColor=MUTED)
    cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK)
    cellb = ParagraphStyle("cellb", parent=cell, fontName="Helvetica-Bold")

    def header(canvas, doc):
        canvas.saveState()
        w, h = letter
        canvas.setFillColor(NAVY); canvas.rect(0, h - 0.6 * inch, w, 0.6 * inch, fill=1, stroke=0)
        canvas.setFillColor(GOLD); canvas.rect(0, h - 0.63 * inch, w, 0.03 * inch, fill=1, stroke=0)
        canvas.setFillColor(colors.white); canvas.setFont("Times-Bold", 13); canvas.drawString(0.6 * inch, h - 0.39 * inch, "TITAN REALTY INVESTMENTS")
        canvas.setFillColor(GOLD); canvas.setFont("Courier", 7.5); canvas.drawRightString(w - 0.6 * inch, h - 0.39 * inch, f"1031 ACQUISITION SEARCH · STATUS & PLAN · {d['today'].strftime('%B %-d, %Y').upper()}")
        canvas.setFillColor(MUTED); canvas.setFont("Helvetica", 7.5)
        canvas.drawString(0.6 * inch, 0.45 * inch, f"{op.get('company', 'Titan Realty Investments')} · {op.get('mailing_address', '')} · Office Line +1 (561) 405 7379 · prepared for {buyer}")
        canvas.drawRightString(w - 0.6 * inch, 0.45 * inch, f"page {doc.page}")
        canvas.restoreState()

    def tile(label, value, note=""):
        return Table([[Paragraph(label.upper(), lab)], [Paragraph(str(value), big)], [Paragraph(note, small)]], colWidths=[1.7 * inch],
                     style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.white), ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                                       ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, 0), 8), ("BOTTOMPADDING", (0, -1), (-1, -1), 8)]))

    doc = SimpleDocTemplate(str(path), pagesize=letter, leftMargin=0.6 * inch, rightMargin=0.6 * inch, topMargin=0.9 * inch, bottomMargin=0.7 * inch,
                            title="1031 Acquisition Search: Status & Plan", author="Titan Realty Investments")
    story: list[Any] = []
    story.append(Paragraph("Where your 1031 search stands, and what happens this week", h1))
    story.append(Paragraph(f"Prepared for {buyer} · {d['today'].strftime('%A, %B %-d, %Y')}. We screen every active for-sale listing on LoopNet across Florida "
                           f"against your criteria, request the books from every broker whose deal fits, verify the numbers as they come in, and bring you "
                           f"only what holds up.", sub))
    story.append(Paragraph("THE CRITERIA WE SCREEN AGAINST", eye))
    crit = [[Paragraph("<b>Price</b>", cell), Paragraph(f"{money(box.get('price_min'))} to {money(box.get('price_max'))} (sweet spot to {money(box.get('sweet_spot_max'))})", cell)],
            [Paragraph("<b>Return</b>", cell), Paragraph(f"Stated cap rate of {box.get('cap_min')}% or better. Anything under is out before it reaches you.", cell)],
            [Paragraph("<b>Property</b>", cell), Paragraph("Existing, cash-flowing: single-tenant net lease, retail centers, hotels, industrial / flex, office, "
                                                           "multifamily (5+ units), medical. No land, no development, no business-only sales.", cell)],
            [Paragraph("<b>Where</b>", cell), Paragraph("All of Florida. South Florida rarely trades at 8%, so the rest of the state is where most of the fits are.", cell)]]
    story.append(Table(crit, colWidths=[1.1 * inch, 6.2 * inch], style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
                                                                                   ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)])))
    story.append(Paragraph("WHAT WE HAVE IDENTIFIED SO FAR", eye))
    tiles = [tile("Listings screened", n["screened"], "active for-sale listings on LoopNet read by the engine"),
             tile("Fit your criteria", n["in_box"], f"across {n['counties']} Florida counties"),
             tile("Brokers to contact", n["brokers"], "one book request each, Monday"),
             tile("Show a cap rate", n["with_cap"], f"median {n['cap_median']:.2f}%, top {n['cap_max']:.2f}%" if n["cap_median"] else "the rest come with the books")]
    story.append(Table([tiles], colWidths=[1.8 * inch] * 4, style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6)])))
    story.append(Spacer(1, 6))
    t_rows = [[Paragraph("<b>By property type</b>", cellb), Paragraph("", cell), Paragraph("<b>By region</b>", cellb), Paragraph("", cell)]]
    types = [(TYPE_LABEL.get(k, k.title()), v) for k, v in n["by_type"]]
    regions = n["by_region"]
    for i in range(max(len(types), len(regions))):
        tl, tv = types[i] if i < len(types) else ("", "")
        rl, rv = regions[i] if i < len(regions) else ("", "")
        t_rows.append([Paragraph(tl, cell), Paragraph(str(tv), cell), Paragraph(rl, cell), Paragraph(str(rv), cell)])
    story.append(Table(t_rows, colWidths=[2.6 * inch, 0.9 * inch, 2.6 * inch, 0.9 * inch],
                       style=TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.8, GOLD), ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE), ("TOPPADDING", (0, 0), (-1, -1), 3),
                                         ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("ALIGN", (3, 0), (3, -1), "RIGHT")])))
    story.append(Paragraph("THIS WEEK, STEP BY STEP", eye))
    plan = [
        (d["monday"].strftime("%a %b %-d"), "Book requests go out", f"Every broker with a fitting listing gets a short email from our acquisitions desk asking for the offering "
                                                                     f"memorandum, the trailing twelve months and the rent roll. We sign confidentiality agreements where a broker asks."),
        (f"{d['monday'].strftime('%a')}–{d['wednesday'].strftime('%a')}", "Analysis as the books arrive",
         "Each package is read the day it lands: real NOI against the stated cap, occupancy, who the tenants are and how long they are committed, expenses that make "
         "sense for the lease type. Every deal gets a verdict (clean, suspect, or still waiting on the books) and an evidence grade from A to D."),
        ("Ongoing", "Cutting the fat", "Numbers that do not hold up, pro-forma stories, brokers who do not answer or do not move: out. We track how fast every broker responds; "
                                       "speed is a signal about the seller."),
        (d["wednesday"].strftime("%a %b %-d"), "Review call", "We walk you and your agent through the opportunities that survived: the facts, the books, our read, and the price "
                                                               f"at which each one hits {box.get('target_cap', 8)}% on the real income (the number we would open with)."),
        ("After the call", "Offers on the ones you choose", "Written offers go out at the agreed number, with the inspection period and timeline your exchange needs."),
    ]
    rows = [[Paragraph(f"<b>{w}</b>", cell), Paragraph(f"<b>{t}</b><br/>{x}", cell)] for w, t, x in plan]
    story.append(Table(rows, colWidths=[1.2 * inch, 6.1 * inch], style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -2), 0.3, LINE),
                                                                                   ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)])))
    story.append(KeepTogether([
        Paragraph("WHAT WE NEED FROM YOU TO MOVE AT FULL SPEED", eye),
        Paragraph("Brokers release books faster to buyers who look real. Three things make that true from day one:", body),
        Table([[Paragraph("1.", cellb), Paragraph("<b>Proof of funds</b> (a bank or exchange-accommodator letter covering the equity). Many brokers ask for it before they send the trailing twelve.", cell)],
               [Paragraph("2.", cellb), Paragraph("<b>Confidentiality agreements.</b> Your OK for us to sign them on the buyer's behalf, or a signer on your side who can turn them around the same day.", cell)],
               [Paragraph("3.", cellb), Paragraph(f"<b>A decision on {d['wednesday'].strftime('%A')}.</b> The deals that survive will have other buyers. Come to the call ready to say yes to an offer or no to the deal.", cell)]],
              colWidths=[0.3 * inch, 7.0 * inch], style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)])),
    ]))
    if n["contacted"]:
        story.append(Paragraph("PROGRESS", eye))
        story.append(Paragraph(f"{n['contacted']} brokers contacted · {n['answered']} answered · {n['books']} sent their books · {n['clean']} deals verified clean so far.", body))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Figures as stated on LoopNet until the books are in; nothing is presented to you as verified until the trailing twelve and the rent roll support it.", small))
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.build(story, onFirstPage=header, onLaterPages=header)
    return path
