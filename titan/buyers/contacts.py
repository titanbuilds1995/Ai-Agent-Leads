"""Every contact both buyer jobs found, in ONE workbook (Rodrigo, 2026-11-16: "I need to see all of the contact info from
everyone we got on the facebook pages and the owners around tortuga"). `make buyers-contacts` / `titan contacts`.

Tabs: Facebook investors, Facebook deals, then per deal folder: People behind the LLCs, Investor agents (agents who brought an
investor buyer, listed an investor's flip or sold to an LLC near the deal: the ones with buyers), LLCs, Owners (buyer / seller of
every house around the deal with mailing address), Agents (listing + buyer's agents with phone / email). Reads only."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .area import read_csv

FB_COLS = ["name", "company", "phone", "email", "website", "role", "counties", "cities", "property_types", "price_range",
           "strategy", "groups", "last_post_link", "last_seen", "posts_seen"]
OWNER_COLS = ["address", "city", "zip", "sold_date", "sold_price", "cash", "buyer_name", "buyer_type", "buyer_mailing",
              "seller_name", "seller_type", "deed_date", "deed_price", "miles", "notes"]


def _cols(rows: list[dict[str, str]], first: list[str]) -> list[str]:
    seen = [c for c in first if any(r.get(c) for r in rows)]
    for r in rows:
        seen += [c for c in r if c not in seen and any(x.get(c) for x in rows)]
    return seen or first


def _agents(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        for who, ph, em, off, side in (("listing_agent", "listing_agent_phone", "listing_agent_email", "listing_office", "listed"),
                                       ("buyer_agent", "buyer_agent_phone", "buyer_agent_email", "buyer_agent_office", "bought")):
            name = r.get(who, "")
            if not name:
                continue
            a = out.setdefault(name.lower(), {"agent": name, "phone": "", "email": "", "office": "", "listed": 0, "bought": 0,
                                              "houses": []})
            a["phone"] = a["phone"] or r.get(ph, "")
            a["email"] = a["email"] or r.get(em, "")
            a["office"] = a["office"] or r.get(off, "")
            a[side] += 1
            a["houses"].append(r.get("address", ""))
    rows_out = sorted(out.values(), key=lambda a: -(a["listed"] + a["bought"] * 2))
    for a in rows_out:
        a["houses"] = "; ".join(dict.fromkeys(h for h in a["houses"] if h))[:300]
    return rows_out


def sheets(root: Path) -> list[tuple[str, list[str], list[dict[str, Any]]]]:
    root = Path(root)
    out: list[tuple[str, list[str], list[dict[str, Any]]]] = []
    fb = read_csv(root / "fb_investors.csv")
    out.append(("Facebook investors", _cols(fb, FB_COLS), fb))
    deals = read_csv(root / "fb_deals.csv")
    out.append(("Facebook deals", _cols(deals, []), deals))
    for d in sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []:
        tag = d.name.replace("_", " ").title()[:14]
        people = read_csv(d / "llc_people.csv") or read_csv(d / "people.csv")
        out.append((f"{tag} people", _cols(people, ["name", "llc_name", "role", "phone", "email"]), people))
        inv = read_csv(d / "investor_agents.csv")    # agents who actually worked with investors near the deal (build.rank_agents)
        out.append((f"{tag} investor agents", _cols(inv, ["rank", "agent", "phone", "email", "office", "represented_investor_buyer",
                                                          "listed_investor_flip", "sold_to_investor", "listed_renovated_flip",
                                                          "investors", "addresses"]), inv))
        llcs = read_csv(d / "cash_buyers.csv") or read_csv(d / "llcs.csv")
        out.append((f"{tag} LLCs", _cols(llcs, []), llcs))
        area = read_csv(d / "area_sales.csv")
        owners = [r for r in area if r.get("buyer_name") or r.get("seller_name")]
        out.append((f"{tag} owners", OWNER_COLS, owners))
        out.append((f"{tag} agents", ["agent", "phone", "email", "office", "listed", "bought", "houses"], _agents(area)))
    return out


def export(root: Path | str = "reports/buyers", path: Path | str | None = None) -> tuple[Path, list[tuple[str, int]]]:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = Path(path or root / "ALL_CONTACTS.xlsx")
    wb = Workbook()
    wb.remove(wb.active)
    counts = []
    for name, cols, rows in sheets(root):
        ws = wb.create_sheet(name[:31])
        ws.append(cols)
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in rows:
            ws.append([r.get(c, "") for c in cols])
        ws.freeze_panes = "A2"
        for i, c in enumerate(cols, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = min(40, max(10, len(c) + 2))
        counts.append((name, len(rows)))
    wb.save(path)
    return path, counts


def email(path: Path, counts: list[tuple[str, int]], gmail: Any, to: str) -> None:
    """Send the workbook to Rodrigo himself (REPORT_TO). Never to anyone else."""
    lines = [f"- {n}: {c}" for n, c in counts]
    body = ("Rodrigo,\n\nAll the buyer contacts found so far are in the attached spreadsheet, one tab per list:\n\n"
            + "\n".join(lines) + "\n\nNothing was sent to any of them.\n\nCharles")
    gmail.send(to, f"Buyer contacts: Facebook groups + around Tortuga ({sum(c for _, c in counts)} rows)", body,
               attachments=[{"filename": path.name, "bytes": Path(path).read_bytes(),
                             "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}])
