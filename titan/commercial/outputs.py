"""Files for Rodrigo: master CSV + workbook (Master / Top 25 / Broker contacts / Needs OM / Diff), the Titan-branded Top-25 PDF,
the broker contact CSV, and the morning diff (markdown + the same text for the email). All under reports/commercial/<date>/."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ..util import money, pretty_phone
from .om_request import om_request

MASTER_COLS = [
    "rank", "score", "tag", "evidence_grade", "tier", "in_box", "box_note", "asset_class", "property_type", "property_subtype", "name", "street", "city",
    "county", "region", "zip", "price", "price_text", "stated_cap", "stated_noi", "back_solved_cap", "cap_mismatch", "offer_anchor", "offer_anchor_basis",
    "building_sf", "ppsf", "lot_sf", "year_built", "units", "occupancy", "tenants", "tenancy", "lease_type", "remaining_term_years", "credit_tenants",
    "days_on_market", "listed_at", "price_cuts", "previous_price", "motivation", "tag_reasons", "evidence_note", "docs_missing", "docs_gated",
    "detail_level", "broker_name", "brokerage", "brokerage_size", "broker_phone", "broker_email", "listing_url", "listing_id", "first_seen", "last_seen",
    "status", "description",
]
GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


def _cell(v: Any) -> Any:
    if isinstance(v, (list, dict)):
        return "; ".join(str(x) for x in v) if isinstance(v, list) else json.dumps(v)
    if isinstance(v, bool):
        return "yes" if v else "no"
    return "" if v is None else v


def rank(listings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hot to cold: in-box first, then score, then the better evidence grade."""
    def key(L: dict[str, Any]):
        box = 0 if L.get("in_box") else 1 if L.get("in_box") is None else 2
        return (box, -(L.get("score") or 0), GRADE_ORDER.get(L.get("evidence_grade") or "D", 3))
    out = sorted(listings, key=key)
    for i, L in enumerate(out, 1):
        L["rank"] = i
    return out


def top_clean(ranked: list[dict[str, Any]], n: int = 25) -> list[dict[str, Any]]:
    """The one-pager list: CLEAN listings in the box, grade first, then score."""
    pool = [L for L in ranked if L.get("in_box") and L.get("tag") == "CLEAN"]
    return sorted(pool, key=lambda L: (GRADE_ORDER.get(L.get("evidence_grade") or "D", 3), -(L.get("score") or 0)))[:n]


def one_line(L: dict[str, Any]) -> str:
    bits = []
    if L.get("tenancy") == "single" and L.get("credit_tenants"):
        bits.append(f"single tenant ({L['credit_tenants'][0].title()})")
    elif L.get("tenancy"):
        bits.append(f"{L['tenancy']} tenant")
    if L.get("occupancy"):
        bits.append(f"{L['occupancy']:.0f}% occupied")
    if L.get("remaining_term_years"):
        bits.append(f"{L['remaining_term_years']:g} yrs left")
    if L.get("days_on_market") is not None:
        bits.append(f"{L['days_on_market']} days on market")
    if L.get("price_cuts"):
        bits.append(f"{L['price_cuts']} cut(s)")
    sig = [s for s in (L.get("motivation") or []) if s not in ("value-add", "1031")]
    if sig:
        bits.append(", ".join(sig))
    return "; ".join(bits) or (L.get("description") or "")[:120]


def broker_contacts(listings: list[dict[str, Any]], include_undisclosed: bool = False) -> list[dict[str, Any]]:
    """Every broker for every in-box listing (grades A–D), deduplicated by email (name+brokerage when no email), with the
    listing count, addresses and the documents missing per listing. This is Monday's book-request list."""
    rows: dict[str, dict[str, Any]] = {}
    for L in listings:
        if not (L.get("in_box") or (include_undisclosed and L.get("in_box") is None)):
            continue
        key = (L.get("broker_email") or "").lower() or f"{(L.get('broker_name') or '').lower()}|{(L.get('brokerage') or '').lower()}"
        r = rows.setdefault(key, {"broker_name": L.get("broker_name"), "brokerage": L.get("brokerage"), "broker_phone": pretty_phone(L.get("broker_phone")) if L.get("broker_phone") else (L.get("broker_phone_raw") or ""),
                                 "broker_email": (L.get("broker_email") or "").lower(), "email_missing": not L.get("broker_email"), "brokerage_size": L.get("brokerage_size"),
                                 "listing_count": 0, "listings": [], "best_score": 0, "grades": set(), "tags": set(), "missing_docs": []})
        r["listing_count"] += 1
        addr = f"{L.get('street')}, {L.get('city')}"
        r["listings"].append(f"{addr} ({money(L.get('price'))}, {L.get('tag')}, grade {L.get('evidence_grade')})")
        r["best_score"] = max(r["best_score"], L.get("score") or 0)
        r["grades"].add(L.get("evidence_grade") or "D")
        r["tags"].add(L.get("tag") or "")
        miss = L.get("docs_missing") or ["T-12", "rent roll", "OM", "lease abstract"]
        r["missing_docs"].append(f"{addr}: {', '.join(miss)}" + (f" (gated: {', '.join(L['docs_gated'])})" if L.get("docs_gated") else ""))
    out = []
    for r in rows.values():
        r["grades"], r["tags"] = "".join(sorted(r["grades"])), ", ".join(sorted(t for t in r["tags"] if t))
        out.append(r)
    return sorted(out, key=lambda r: (-r["listing_count"], -r["best_score"]))


def write_csv(path: Path, rows: list[dict[str, Any]], cols: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: _cell(r.get(c)) for c in cols})
    return path


def write_workbook(path: Path, ranked: list[dict[str, Any]], top: list[dict[str, Any]], brokers: list[dict[str, Any]],
                   needs_om: list[dict[str, Any]], diff_rows: list[dict[str, Any]], cfg: Any) -> Path | None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        return None
    wb = Workbook()
    navy, gold = PatternFill("solid", fgColor="0C1C35"), Font(color="B99455", bold=True)

    def sheet(title: str, cols: list[str], rows: list[dict[str, Any]], first: bool = False):
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        ws.append(cols)
        for c in ws[1]:
            c.fill, c.font, c.alignment = navy, gold, Alignment(vertical="center")
        for r in rows:
            ws.append([_cell(r.get(c)) for c in cols])
        ws.freeze_panes = "A2"
        for i, c in enumerate(cols, 1):
            width = max([len(str(c))] + [min(60, len(str(_cell(r.get(c))))) for r in rows[:200]]) + 2
            ws.column_dimensions[get_column_letter(i)].width = min(60, max(8, width))
        return ws

    sheet("Master", MASTER_COLS, ranked, first=True)
    top_cols = ["rank", "evidence_grade", "score", "region", "county", "asset_class", "street", "city", "price", "stated_cap", "offer_anchor", "lease_type", "summary", "broker_name", "broker_phone", "broker_email", "listing_url"]
    sheet("Top 25 for 1031 Buyer", top_cols, [{**L, "summary": one_line(L)} for L in top])
    bcols = ["broker_name", "brokerage", "brokerage_size", "broker_phone", "broker_email", "email_missing", "listing_count", "best_score", "grades", "tags", "listings", "missing_docs"]
    sheet("Broker Contacts", bcols, brokers)
    om_cols = ["score", "asset_class", "street", "city", "county", "price", "stated_cap", "broker_name", "broker_phone", "broker_email", "om_subject", "om_body", "listing_url"]
    sheet("Needs OM", om_cols, [{**L, **{"om_subject": om_request(cfg, L)["subject"], "om_body": om_request(cfg, L)["body"]}} for L in needs_om])
    sheet("Diff", ["change", "street", "city", "asset_class", "price", "was", "now", "score", "tag", "listing_url"], diff_rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return path


# --- the Top-25 one-pager (reportlab, house style: navy bar, gold rule, paper, serif headings, mono labels) ----------------------

def write_pdf(path: Path, top: list[dict[str, Any]], cfg: Any, run_date: str, counts: dict[str, Any]) -> Path | None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, KeepTogether
    except ImportError:
        return None
    NAVY, GOLD, INK, MUTED, PAPER, LINE = colors.HexColor("#0C1C35"), colors.HexColor("#B99455"), colors.HexColor("#16203A"), colors.HexColor("#5B6472"), colors.HexColor("#F4F2EC"), colors.HexColor("#E1DCD0")
    box = cfg.get("commercial.buyer_box", {})
    h1 = ParagraphStyle("h1", fontName="Times-Bold", fontSize=20, leading=24, textColor=INK, spaceAfter=2)
    sub = ParagraphStyle("sub", fontName="Helvetica", fontSize=9, leading=12, textColor=MUTED, spaceAfter=10)
    h2 = ParagraphStyle("h2", fontName="Times-Bold", fontSize=13, leading=16, textColor=INK, spaceBefore=10, spaceAfter=4)
    eyebrow = ParagraphStyle("eye", fontName="Courier", fontSize=7, leading=9, textColor=GOLD, spaceAfter=1)
    cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=7.6, leading=9.4, textColor=INK, alignment=TA_LEFT)
    cellm = ParagraphStyle("cellm", parent=cell, textColor=MUTED, fontSize=7)
    foot = ParagraphStyle("foot", fontName="Helvetica", fontSize=7, leading=9, textColor=MUTED)

    def header(canvas, doc):
        canvas.saveState()
        w, h = letter
        canvas.setFillColor(NAVY)
        canvas.rect(0, h - 0.55 * inch, w, 0.55 * inch, fill=1, stroke=0)
        canvas.setFillColor(GOLD)
        canvas.rect(0, h - 0.58 * inch, w, 0.03 * inch, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Times-Bold", 13)
        canvas.drawString(0.5 * inch, h - 0.36 * inch, "TITAN REALTY INVESTMENTS")
        canvas.setFont("Courier", 7.5)
        canvas.setFillColor(GOLD)
        canvas.drawRightString(w - 0.5 * inch, h - 0.36 * inch, f"1031 BUYER · COMMERCIAL SCREEN · {run_date}")
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(0.5 * inch, 0.4 * inch, "Internal screening. Offer anchor = price at which the STATED NOI yields the target cap. Verify every number against the T-12 and rent roll before an offer.")
        canvas.drawRightString(w - 0.5 * inch, 0.4 * inch, f"page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(path), pagesize=letter, leftMargin=0.5 * inch, rightMargin=0.5 * inch, topMargin=0.85 * inch, bottomMargin=0.65 * inch,
                            title="Top 25 for 1031 Buyer", author="Titan Realty Investments")
    story: list[Any] = [Paragraph("Top 25 for the 1031 Buyer", h1),
                        Paragraph(f"Florida · LoopNet for-sale · {money(box.get('price_min'))}–{money(box.get('price_max'))} · stated cap {box.get('cap_min')}%+ · CLEAN listings only "
                                  f"(trailing NOI, cap, occupancy and lease type stated and consistent) · sorted by evidence grade, then score. "
                                  f"{counts.get('in_box', 0)} listings in the box, {counts.get('clean', 0)} CLEAN, {counts.get('suspect', 0)} SUSPECT, {counts.get('needs_om', 0)} NEEDS-OM.", sub)]
    if not top:
        story.append(Paragraph("No CLEAN listing yet: every in-box listing is either missing NOI/cap (NEEDS-OM, broker queued for the books) or SUSPECT. "
                               "The broker contact sheet is the Monday list.", cell))
    regions: dict[str, list[dict[str, Any]]] = {}
    for L in top:
        regions.setdefault(L.get("region") or "Unknown", []).append(L)
    for region, rows in regions.items():
        data = [[Paragraph(f"<b>{h}</b>", cellm) for h in ("#", "Grade", "Deal", "Asking", "Cap", "Offer anchor", "Lease", "Score", "One line")]]
        for L in rows:
            data.append([
                Paragraph(str(L.get("rank", "")), cell), Paragraph(f"<b>{L.get('evidence_grade')}</b>", cell),
                Paragraph(f"<b>{L.get('street')}</b><br/>{L.get('city')} · {L.get('county')} · {(L.get('asset_class') or '').upper()}<br/>"
                          f"<font color='#5B6472'>{L.get('broker_name') or ''} · {pretty_phone(L.get('broker_phone')) if L.get('broker_phone') else ''}</font>", cell),
                Paragraph(money(L.get("price")), cell), Paragraph(f"{L['stated_cap']:.2f}%" if L.get("stated_cap") else "", cell),
                Paragraph(f"<b>{money(L.get('offer_anchor'))}</b>", cell), Paragraph((L.get("lease_type") or "?").upper(), cell),
                Paragraph(str(L.get("score")), cell), Paragraph(one_line(L), cellm),
            ])
        t = Table(data, colWidths=[0.22 * inch, 0.48 * inch, 2.15 * inch, 0.7 * inch, 0.5 * inch, 0.8 * inch, 0.55 * inch, 0.45 * inch, 1.65 * inch], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), PAPER), ("LINEBELOW", (0, 0), (-1, 0), 0.8, GOLD), ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        story.append(KeepTogether([Paragraph("REGION", eyebrow), Paragraph(f"{region} · {len(rows)} deal{'s' if len(rows) != 1 else ''}", h2), t]))
    story += [Spacer(1, 8), Paragraph("Evidence grade: A = T-12/P&L + rent roll on file and matching the stated cap · B = OM with NOI and expenses, no trailing financials · "
                                      "C = cap/NOI in the listing text only · D = nothing or pro forma only. A high score with a D grade is still a D.", foot)]
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.build(story, onFirstPage=header, onLaterPages=header)
    return path


def diff_markdown(run_date: str, diff: dict[str, list[dict[str, Any]]], counts: dict[str, Any], cost: dict[str, Any]) -> str:
    lines = [f"# Commercial screen · {run_date}", "",
             f"{counts.get('total', 0)} LoopNet listings seen · {counts.get('in_box', 0)} in the box · {counts.get('clean', 0)} CLEAN · "
             f"{counts.get('suspect', 0)} SUSPECT · {counts.get('needs_om', 0)} NEEDS-OM · {counts.get('undisclosed', 0)} price not disclosed · "
             f"{counts.get('sheets_pending', 0)} detail sheets still to fetch", ""]
    if cost:
        lines += [f"Apify spend this run: about ${cost.get('total_usd', 0):.2f} (search {cost.get('search_usd', 0):.2f}, details {cost.get('details_usd', 0):.2f}).", ""]
    for key, title in (("new", "New in the box"), ("price_drops", "Price drops"), ("price_ups", "Price increases"), ("gone", "Gone (pending / sold / pulled)"),
                       ("back", "Back on the market"), ("dom_changes", "Days on market jumped"), ("tag_changes", "Tag changed")):
        rows = diff.get(key) or []
        lines.append(f"## {title} ({len(rows)})")
        if not rows:
            lines.append("none")
        for r in rows[:60]:
            lines.append(f"- {r.get('street')}, {r.get('city')} · {r.get('asset_class') or ''} · {money(r.get('price'))}"
                         + (f" · {r.get('was')} → {r.get('now')}" if r.get("was") is not None else "")
                         + f" · score {r.get('score')} · {r.get('tag')} · {r.get('listing_url')}")
        lines.append("")
    return "\n".join(lines)
