"""Step 3: join the area pull with what the Chrome step found into three ranked lists for Rodrigo.

In reports/buyers/<slug>/:
  area_sales.csv      step 1 + the owner / buyer / seller / buyer's agent the Chrome step filled in
  llcs.csv            Chrome: one row per LLC (Sunbiz + web)            columns: LLC_COLS
  people.csv          Chrome: the people behind each LLC and their contact columns: PEOPLE_COLS
  ../fb_investors.csv (optional) the Facebook groups list; matched on company name
Out:
  cash_buyers.csv     one row per buying entity, ranked (buys near the deal, recency, distance, cash, flips sold)
  llc_people.csv      every person tied to those entities, in the entities' rank order, with any phone / email found
  investor_agents.csv every agent tied to investor deals around the deal, ranked, with which entities
  summary.md          the top of each list + what is still blank"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .area import read_csv, write_csv

LLC_COLS = ["llc_name", "doc_number", "status", "filed", "principal_address", "mailing_address", "registered_agent",
            "officers", "related_entities", "website", "phone", "email", "social", "notes", "sources"]
PEOPLE_COLS = ["name", "llc_name", "role", "address", "phone", "email", "website_or_profile", "source", "confidence"]

ENTITY_RE = re.compile(r"\b(L\.?\s?L\.?\s?C|INC|CORP(ORATION)?|CO|L\.?P|LTD|LLLP|PLLC|PA|HOLDINGS?|PROPERTIES|PROPERTY|INVESTMENTS?|"
                       r"GROUP|CAPITAL|VENTURES?|REALTY|HOMES|ENTERPRISES?|PARTNERS|DEVELOPMENT|ASSETS?|FUND|EQUITY|REI)\b\.?", re.I)
TRUST_RE = re.compile(r"\b(TRUST|TRUSTEE|TR|LAND TRUST)\b", re.I)
SKIP_RE = re.compile(r"\b(BANK|MORTGAGE|FEDERAL NATIONAL|FANNIE|FREDDIE|HUD|SECRETARY OF|HOUSING AUTHORITY|COUNTY|CITY OF|VILLAGE OF|"
                     r"ASSOCIATION|HOA|CONDOMINIUM|CHURCH|SCHOOL BOARD|STATE OF)\b", re.I)
INSTITUTIONAL_RE = re.compile(r"\b(OPENDOOR|OFFERPAD|INVITATION HOMES|PROGRESS RESIDENTIAL|AMERICAN HOMES 4 RENT|AMH|TRICON|"
                              r"FIRSTKEY|MAIN STREET RENEWAL|HOME PARTNERS|VINEBROOK|PRETIUM|CERBERUS|BLACKSTONE|ZILLOW|REDFIN|"
                              r"KNOCK|SUNDAE|HOMEVESTORS|WE BUY UGLY|CSH PROPERTY|SFR JV|STAR 2022|VM MASTER)\b", re.I)


def norm(name: str) -> str:
    s = (name or "").upper().replace("&", " AND ")
    s = re.sub(r"\bL\.?\s?L\.?\s?C\.?", " LLC", s)
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def kind(name: str, given: str = "") -> str:
    g = (given or "").upper()
    if g in {"LLC", "CORP", "TRUST", "PERSON", "INSTITUTIONAL", "SKIP"}:
        return g
    if not name:
        return ""
    if SKIP_RE.search(name):
        return "SKIP"
    if INSTITUTIONAL_RE.search(name):
        return "INSTITUTIONAL"
    if ENTITY_RE.search(name):
        return "CORP" if re.search(r"\b(INC|CORP)", name, re.I) else "LLC"
    if TRUST_RE.search(name):
        return "TRUST"
    return "PERSON"


BUYERS = {"LLC", "CORP", "TRUST", "INSTITUTIONAL"}


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace("$", "").replace(",", "")) if str(v).strip() else None
    except ValueError:
        return None


def _date(v: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y-%m"):
        try:
            return datetime.strptime((v or "").strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


def _yes(v: str) -> bool:
    return (v or "").strip().lower() in {"yes", "y", "cash", "true", "1"}


def rank_buyers(rows: list[dict[str, str]], llcs: dict[str, dict[str, str]], fb: dict[str, dict[str, str]],
                price: float | None, today: date) -> list[dict[str, Any]]:
    ent: dict[str, dict[str, Any]] = {}

    def slot(name: str, k: str) -> dict[str, Any]:
        key = norm(name)
        return ent.setdefault(key, {"entity": name, "type": k, "buys_near": 0, "cash_buys": 0, "flips_sold_near": 0,
                                    "last_buy": None, "nearest_miles": None, "buy_prices": [], "addresses": [], "_key": key})

    for r in rows:
        bk = kind(r.get("buyer_name", ""), r.get("buyer_type", ""))
        if bk in BUYERS:
            e = slot(r["buyer_name"], bk)
            e["buys_near"] += 1
            e["cash_buys"] += _yes(r.get("cash", ""))
            d = _date(r.get("deed_date") or r.get("sold_date", ""))
            if d and (not e["last_buy"] or d > e["last_buy"]):
                e["last_buy"] = d
            m = _num(r.get("miles"))
            if m is not None and (e["nearest_miles"] is None or m < e["nearest_miles"]):
                e["nearest_miles"] = m
            p = _num(r.get("deed_price")) or _num(r.get("sold_price"))
            if p:
                e["buy_prices"].append(p)
            e["addresses"].append(r["address"])
        sk = kind(r.get("seller_name", ""), r.get("seller_type", ""))
        if sk in BUYERS and r.get("status") in {"sold", "pending", "for_sale"}:
            e = slot(r["seller_name"], sk)
            e["flips_sold_near"] += 1
            e["addresses"].append(f"{r['address']} (sold/listed by them)")
    out = []
    for e in ent.values():
        info = llcs.get(e["_key"], {})
        f = fb.get(e["_key"], {})
        days = (today - e["last_buy"]).days if e["last_buy"] else None
        prices = e["buy_prices"]
        fit = bool(price and any(0.6 * price <= p <= 1.4 * price for p in prices))
        phone = info.get("phone") or f.get("phone", "")
        email = info.get("email") or f.get("email", "")
        score = (10 * e["buys_near"] + 5 * e["flips_sold_near"] + 3 * e["cash_buys"]
                 + (10 if days is not None and days <= 180 else 5 if days is not None and days <= 365 else 0)
                 + (5 if e["nearest_miles"] is not None and e["nearest_miles"] <= 0.5 else 0) + (3 if fit else 0)
                 + (2 if phone or email else 0) - (8 if e["type"] == "INSTITUTIONAL" else 0) - (3 if e["type"] == "TRUST" else 0))
        out.append({"rank": 0, "score": score, "entity": e["entity"], "type": e["type"], "buys_near": e["buys_near"],
                    "cash_buys": e["cash_buys"], "flips_sold_near": e["flips_sold_near"],
                    "last_buy": e["last_buy"].isoformat() if e["last_buy"] else "", "nearest_miles": e["nearest_miles"] or "",
                    "buy_range": f"${min(prices):,.0f}–${max(prices):,.0f}" if prices else "", "fits_deal_price": "yes" if fit else "",
                    "website": info.get("website") or f.get("website", ""), "phone": phone, "email": email,
                    "registered_agent": info.get("registered_agent", ""), "officers": info.get("officers", ""),
                    "related_entities": info.get("related_entities", ""), "sunbiz_status": info.get("status", ""),
                    "doc_number": info.get("doc_number", ""), "mailing_address": info.get("mailing_address", ""),
                    "on_fb_list": "yes" if f else "", "addresses": "; ".join(e["addresses"][:8]),
                    "looked_up": "yes" if info else "NO, still to look up"})
    out.sort(key=lambda r: (-r["score"], r["entity"]))
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


def people_list(buyers: list[dict[str, Any]], people: list[dict[str, str]], llcs: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    rank = {norm(b["entity"]): b["rank"] for b in buyers}
    seen, out = set(), []
    for p in people:
        k = (norm(p.get("name", "")), norm(p.get("llc_name", "")))
        if not k[0] or k in seen:
            continue
        seen.add(k)
        out.append({**{c: p.get(c, "") for c in PEOPLE_COLS}, "llc_rank": rank.get(k[1], "")})
    for key, info in llcs.items():  # officers / registered agent from Sunbiz that the people sheet does not have yet
        named = [(n.strip(), t.strip()) for n, t in re.findall(r"([^;()]+)\(([^)]*)\)", info.get("officers", ""))]
        if info.get("registered_agent"):
            named.append((info["registered_agent"].split(",")[0], "registered agent"))
        for n, t in named:
            k = (norm(n), key)
            if k[0] and k not in seen:
                seen.add(k)
                out.append({"name": n, "llc_name": info.get("llc_name", ""), "role": t, "address": "", "phone": "", "email": "",
                            "website_or_profile": "", "source": "sunbiz", "confidence": "", "llc_rank": rank.get(key, "")})
    out.sort(key=lambda r: (r["llc_rank"] == "", r["llc_rank"] if r["llc_rank"] != "" else 0, not (r["phone"] or r["email"])))
    return out


def rank_agents(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    ag: dict[str, dict[str, Any]] = {}

    def slot(name: str, phone: str, email: str, office: str) -> dict[str, Any] | None:
        if not (name or email):
            return None
        key = (email or "").lower().strip() or norm(name)
        a = ag.setdefault(key, {"agent": name, "phone": "", "email": "", "office": "", "represented_investor_buyer": 0,
                                "listed_investor_flip": 0, "sold_to_investor": 0, "listed_renovated_flip": 0,
                                "entities": set(), "addresses": []})
        a["phone"] = a["phone"] or phone
        a["email"] = a["email"] or email
        a["office"] = a["office"] or office
        return a

    for r in rows:
        bk = kind(r.get("buyer_name", ""), r.get("buyer_type", ""))
        sk = kind(r.get("seller_name", ""), r.get("seller_type", ""))
        lst = slot(r.get("listing_agent", ""), r.get("listing_agent_phone", ""), r.get("listing_agent_email", ""), r.get("listing_office", ""))
        if lst:
            hit = False
            if bk in BUYERS:
                lst["sold_to_investor"] += 1
                lst["entities"].add(r["buyer_name"])
                hit = True
            if sk in BUYERS:
                lst["listed_investor_flip"] += 1
                lst["entities"].add(r["seller_name"])
                hit = True
            elif r.get("signal") == "FLIP_RESALE":
                lst["listed_renovated_flip"] += 1
                hit = True
            if hit:
                lst["addresses"].append(r["address"])
        if bk in BUYERS:
            b = slot(r.get("buyer_agent", ""), r.get("buyer_agent_phone", ""), r.get("buyer_agent_email", ""), r.get("buyer_agent_office", ""))
            if b:
                b["represented_investor_buyer"] += 1
                b["entities"].add(r["buyer_name"])
                b["addresses"].append(r["address"])
    out = []
    for a in ag.values():
        score = 4 * a["represented_investor_buyer"] + 4 * a["listed_investor_flip"] + 2 * a["sold_to_investor"] + a["listed_renovated_flip"]
        if score:
            out.append({"rank": 0, "score": score, **{k: v for k, v in a.items() if k not in {"entities", "addresses"}},
                        "investors": "; ".join(sorted(a["entities"])), "addresses": "; ".join(dict.fromkeys(a["addresses"]))})
    out.sort(key=lambda r: (-r["score"], r["agent"]))
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


def build(folder: Path | str, price: float | None = None, today: date | None = None) -> dict[str, Any]:
    folder = Path(folder)
    rows = read_csv(folder / "area_sales.csv")
    if not rows:
        raise SystemExit(f"{folder}/area_sales.csv is missing or empty: run `make buyers-area` first")
    llcs = {norm(r.get("llc_name", "")): r for r in read_csv(folder / "llcs.csv") if r.get("llc_name")}
    fb = {norm(r.get("company", "")): r for r in read_csv(folder.parent / "fb_investors.csv") if r.get("company")}
    buyers = rank_buyers(rows, llcs, fb, price, today or date.today())
    people = people_list(buyers, read_csv(folder / "people.csv"), llcs)
    agents = rank_agents(rows)
    bcols = list(buyers[0]) if buyers else ["rank", "entity"]
    acols = list(agents[0]) if agents else ["rank", "agent"]
    write_csv(folder / "cash_buyers.csv", buyers, bcols)
    write_csv(folder / "llc_people.csv", people, [*PEOPLE_COLS, "llc_rank"])
    write_csv(folder / "investor_agents.csv", agents, acols)
    filled = sum(1 for r in rows if r.get("buyer_name") or r.get("seller_name"))
    stats = {"houses": len(rows), "owners_filled": filled, "entities": len(buyers),
             "entities_looked_up": sum(1 for b in buyers if b["looked_up"] == "yes"),
             "people": len(people), "people_with_contact": sum(1 for p in people if p["phone"] or p["email"]), "agents": len(agents)}
    (folder / "summary.md").write_text(_summary(folder.name, stats, buyers, agents, people))
    return stats


def _summary(name: str, s: dict[str, Any], buyers: list[dict[str, Any]], agents: list[dict[str, Any]], people: list[dict[str, Any]]) -> str:
    L = [f"# Cash buyers around {name.replace('_', ' ').title()}", "",
         f"{s['houses']} houses in the area, owner filled in on {s['owners_filled']}. {s['entities']} buying entities "
         f"({s['entities_looked_up']} looked up on Sunbiz / the web). {s['people']} people behind them, "
         f"{s['people_with_contact']} with a phone or email. {s['agents']} agents tied to investor deals.", "",
         "## Top buyers", "", "| # | Entity | Buys near | Last buy | Cash | Flips sold | Contact |", "|---|---|---|---|---|---|---|"]
    for b in buyers[:15]:
        L.append(f"| {b['rank']} | {b['entity']} | {b['buys_near']} | {b['last_buy']} | {b['cash_buys']} | {b['flips_sold_near']} | "
                 f"{b['phone'] or b['email'] or b['website'] or '—'} |")
    L += ["", "## Agents who work with them", "", "| # | Agent | Office | Repped buyer | Listed their flip | Sold to them | Phone / email |",
          "|---|---|---|---|---|---|---|"]
    for a in agents[:15]:
        L.append(f"| {a['rank']} | {a['agent']} | {a['office']} | {a['represented_investor_buyer']} | {a['listed_investor_flip']} | "
                 f"{a['sold_to_investor']} | {a['phone'] or ''} {a['email'] or ''} |")
    L += ["", "## People to call first", ""]
    for p in [p for p in people if p["phone"] or p["email"]][:15]:
        L.append(f"- {p['name']} ({p['role']}, {p['llc_name']}): {p['phone']} {p['email']}")
    todo = [b["entity"] for b in buyers if b["looked_up"] != "yes"][:20]
    if todo:
        L += ["", "## Still to look up on Sunbiz", "", *[f"- {t}" for t in todo]]
    return "\n".join(L) + "\n"
