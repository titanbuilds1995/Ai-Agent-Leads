"""Numbers snapshot for one lead, in the layout Rodrigo approved on Lise's card (2026-10-26) plus the comps behind it:
address, Status, The house, The comps (compact table + averages), The numbers (ARV → exit → where we need to be → gap),
Engine screen, Still open, Conversation so far. Compact by rule (Rodrigo, 2026-09-14: "just the comparables in the area
and the basic info we need"): 6–10 sales, last 12 months, one table.

Built when Rodrigo asks for comps / ARV / numbers in a comment on an approval card (titan/comment_router.py). Internal
only: nothing here ever reaches an agent."""
from __future__ import annotations

import html
import json
import re
from statistics import mean, median
from typing import Any, Callable

from . import db
from .util import log, today_et
from .valuation import haversine_miles, select_comps

RENOVATED_RE = re.compile(r"\b(renovated|remodel(?:ed)?|fully updated|completely updated|new kitchen|brand new|turnkey|turn[- ]key|"
                          r"move[- ]in ready|rehabbed|newly built|new construction)\b", re.I)
ASIS_RE = re.compile(r"\b(as[- ]is|tlc|handyman|fixer|needs (?:work|updating|repairs|renovation)|investor special|investors? "
                     r"(?:special|opportunity|delight)|cash only|original condition|bring your contractor)\b", re.I)
RENT_RE = re.compile(r"(?:rent(?:ed|s|ing)?(?: out)?(?: for| at)?|bringing in|brings in|leased (?:for|at))\s*\$\s?([\d,]{3,6})", re.I)


def money(n: Any) -> str:
    return f"${int(n):,}" if n not in (None, "") else "?"


def condition_of(remarks: str | None) -> str:
    r = remarks or ""
    if RENOVATED_RE.search(r):
        return "renovated"
    if ASIS_RE.search(r):
        return "as-is"
    return "not stated"


def _ppsf(c: dict[str, Any]) -> float | None:
    return c["sold_price"] / c["sqft"] if c.get("sold_price") and c.get("sqft") else None


def pick_comps(p: dict[str, Any], candidates: list[dict[str, Any]], cfg: Any, want: int = 10) -> tuple[list[dict[str, Any]], str]:
    """6–10 closest sales of the same product: the normal radius, widened step by step only when too few."""
    for radius in (cfg.get("valuation.comp_radius_miles", 0.5), cfg.get("valuation.fallback_radius_miles", 1.0), 2.0):
        comps = select_comps(p, candidates, cfg, radius=radius)
        if len(comps) >= 6:
            break
    comps = sorted(comps, key=lambda c: (c.get("distance_mi") if c.get("distance_mi") is not None else 9, -(_date_key(c))))[:want]
    comps = sorted(comps, key=lambda c: -_date_key(c))
    return comps, f"within {radius:g} mi" if p.get("lat") else f"ZIP {p.get('zip')}"


def _date_key(c: dict[str, Any]) -> int:
    d = re.sub(r"\D", "", str(c.get("sold_date") or ""))[:8]
    return int(d) if d.isdigit() and len(d) == 8 else 0


def rent_from(texts: list[str]) -> int | None:
    """Monthly gross rent when the remarks or the agent's messages state it (sums units: '$2,500 ... $2,000')."""
    total = 0
    for t in texts:
        found = [int(x.replace(",", "")) for x in RENT_RE.findall(t or "")]
        found += [int(x.replace(",", "")) for x in re.findall(r"unit \w+ \$?([\d,]{4,5})\b", t or "", re.I)]
        found = [x for x in found if 500 <= x <= 20000]
        if found:
            total = max(total, sum(dict.fromkeys(found)))
    return total or None


def build(conn, cfg: Any, property_id: int, comps_fn: Callable[[dict[str, Any]], list[dict[str, Any]]] | None,
          asked: str = "", outreach_id: int | None = None) -> dict[str, Any]:
    """Everything the snapshot shows, as data (rendered by to_html / to_comment / to_text)."""
    from .valuation import buy_ratio, repair_cost, repair_tier
    from .market_ratios import lookup
    p = db.row_to_dict(conn.execute("SELECT * FROM properties WHERE id=?", (property_id,)).fetchone())
    v = db.row_to_dict(conn.execute("SELECT * FROM valuations WHERE property_id=?", (property_id,)).fetchone()) or {}
    agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (p.get("agent_id"),)).fetchone()) or {} if p.get("agent_id") else {}
    candidates: list[dict[str, Any]] = []
    source = "sold homes pulled today (HomeHarvest, last 12 months)"
    if comps_fn:
        try:
            candidates = comps_fn(p) or []
        except Exception as e:  # noqa: BLE001
            log.warning("comps for %s failed: %s", p.get("street"), e)
    if not candidates and v.get("comps"):
        candidates = [{**c, "property_type": p.get("property_type")} for c in json.loads(v["comps"] or "[]")]
        source = f"the comps the engine stored on {str(v.get('valued_at') or '')[:10]} (fresh pull not available here)"
    comps, area = pick_comps(p, candidates, cfg)
    for c in comps:
        c["ppsf"] = _ppsf(c)
        c["condition"] = condition_of(c.get("remarks"))
        if c.get("distance_mi") is None and p.get("lat") and c.get("lat"):
            c["distance_mi"] = round(haversine_miles(p["lat"], p["lng"], c["lat"], c["lng"]), 2)
    priced = [c for c in comps if c.get("ppsf")]
    reno = [c for c in priced if c["condition"] == "renovated"]
    sqft = p.get("sqft") or 0
    land = p.get("property_type") == "land"
    if len(reno) >= 3:
        arv_psf, arv_basis = median(c["ppsf"] for c in reno), f"median $/sf of the {len(reno)} renovated sales"
    elif len(priced) >= 4:
        top = sorted(c["ppsf"] for c in priced)[len(priced) // 2:]
        arv_psf, arv_basis = median(top), f"median $/sf of the top half of {len(priced)} sales (few say renovated)"
    elif priced:
        arv_psf, arv_basis = median(c["ppsf"] for c in priced), f"median $/sf of only {len(priced)} sales (thin)"
    else:
        arv_psf, arv_basis = None, ""
    arv = int(round(arv_psf * sqft, -3)) if arv_psf and sqft and not land else None
    if arv is None and v.get("arv"):
        arv, arv_basis = int(v["arv"]), f"engine value ({v.get('arv_method')}); not enough comparable sales to recompute"
    tier = v.get("repair_tier") or repair_tier(p.get("remarks") or "", cfg["repairs"].get("default_tier", "moderate"))
    repairs = int(v.get("repair_cost") or repair_cost(p, tier, cfg) or 0)
    fee = int(cfg["formula"]["assignment_fee"])
    ratios = {}
    path = cfg.root / cfg["formula"].get("market_ratios_file", "data/market_ratios.csv")
    for pct in ("p25", "median", "p75"):
        hit = lookup(path, p.get("county"), p.get("zip"), p.get("property_type"), arv or 0, pct, p.get("city")) if arv else None
        ratios[pct] = hit
    mid = ratios["median"] or (buy_ratio(p, arv, cfg) if arv else None)
    numbers: dict[str, Any] = {"arv": arv, "arv_basis": arv_basis, "repairs": repairs, "repair_tier": tier, "fee": fee}
    if arv and mid:
        exit_ = int(round(arv * mid["ratio"] - repairs, -3))           # what a flipper pays for it as is (deed-record ratio)
        ours = max(exit_ - fee, 0)
        lp = int(p.get("list_price") or 0)
        numbers.update(exit=exit_, ratio=mid["ratio"], ratio_source=mid.get("source"), ratio_n=mid.get("sample_size"),
                       ours=ours, list_price=lp, gap=lp - ours if lp else None, gap_pct=round((lp - ours) / lp * 100) if lp else None)
    screen = []
    for label, pct in (("Conservative", "p25"), ("Typical", "median"), ("Aggressive", "p75")):
        r = ratios.get(pct)
        if arv and r:
            screen.append((f"{label} ({r['ratio']:.0%})", max(int(round(arv * r["ratio"] - repairs - fee, -3)), 0)))
    msgs = [db.row_to_dict(r) for r in conn.execute(
        "SELECT m.direction, m.body, m.created_at, m.classification FROM messages m JOIN threads t ON t.id=m.thread_id "
        "WHERE t.agent_id=? ORDER BY m.created_at", (p.get("agent_id"),)).fetchall()] if p.get("agent_id") and _has_threads(conn) else []
    rent = rent_from([p.get("remarks") or ""] + [m["body"] for m in msgs if m.get("direction") == "in"])
    if rent and numbers.get("ours"):
        numbers["rent"] = rent
        numbers["gross_yield"] = round(rent * 12 / numbers["ours"] * 100, 1)
    extra = _land_and_new(p, candidates, cfg) if re.search(r"land|lot|new construction|new build|tear ?down|develop", asked, re.I) or land else []
    active = _live_competition(conn, p)
    still_open = ["Comps are not checked on the county site; condition is read from listing remarks only, interiors unseen."]
    if not reno:
        still_open.append("None of the sales say renovated in the remarks, so the ARV may be low; check the top sales on the MLS.")
    if (v.get("arv_method") or "").endswith("_suspect"):
        still_open.append("The engine flagged its own value as suspect (resale far above the ask): verify size and unit count.")
    return {"p": p, "agent": agent, "asked": asked.strip(), "comps": comps, "area": area, "source": source, "numbers": numbers,
            "screen": screen, "extra": extra, "active": active, "still_open": still_open, "messages": msgs,
            "averages": _averages(priced, reno), "date": today_et(), "outreach_id": outreach_id}


def _has_threads(conn) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='threads'").fetchone())


def _averages(priced: list[dict[str, Any]], reno: list[dict[str, Any]]) -> list[tuple[str, str]]:
    if not priced:
        return []
    rows = [("Sales used", str(len(priced))),
            ("Median price", money(median(c["sold_price"] for c in priced))),
            ("Average price", money(mean(c["sold_price"] for c in priced))),
            ("Median $/sf (all)", f"${median(c['ppsf'] for c in priced):,.0f}"),
            ("Range $/sf", f"${min(c['ppsf'] for c in priced):,.0f} to ${max(c['ppsf'] for c in priced):,.0f}")]
    if reno:
        rows.append(("Median $/sf (renovated)", f"${median(c['ppsf'] for c in reno):,.0f} ({len(reno)} sales)"))
    return rows


def _land_and_new(p: dict[str, Any], candidates: list[dict[str, Any]], cfg: Any) -> list[tuple[str, str]]:
    """Land value and new-construction resale near the subject, for the 'what would it sell for as land / new build' asks."""
    near = [c for c in candidates if c.get("sold_price") and (not (p.get("lat") and c.get("lat"))
                                                             or haversine_miles(p["lat"], p["lng"], c["lat"], c["lng"]) <= 1.5)]
    out = []
    lots = [c for c in near if c.get("property_type") == "land" and c.get("lot_sqft")]
    if lots:
        per = median(c["sold_price"] / c["lot_sqft"] for c in lots)
        out.append(("Lot sales nearby", f"{len(lots)} sales, median {money(median(c['sold_price'] for c in lots))} (${per:,.0f} per lot sf)"))
        if p.get("lot_sqft"):
            out.append(("This lot as land", f"about {money(round(per * p['lot_sqft'], -3))} ({p['lot_sqft']:,} sf lot)"))
    else:
        out.append(("Lot sales nearby", "none in the last 12 months within 1.5 mi"))
    new = [c for c in near if (c.get("year_built") or 0) >= int(today_et()[:4]) - 6 and c.get("sqft") and c.get("property_type") != "land"]
    if new:
        out.append(("New builds sold nearby", f"{len(new)} sales, median {money(median(c['sold_price'] for c in new))} "
                                              f"(${median(c['sold_price'] / c['sqft'] for c in new):,.0f}/sf)"))
    else:
        out.append(("New builds sold nearby", "none built in the last 6 years sold within 1.5 mi"))
    return out


def _live_competition(conn, p: dict[str, Any]) -> str:
    sq = p.get("sqft") or 0
    rows = conn.execute("SELECT list_price, days_on_market FROM properties WHERE zip=? AND id!=? AND property_type IS ? AND lead_status != 'EXCLUDED' "
                        "AND list_price > 0 AND (? = 0 OR sqft BETWEEN ? AND ?)",
                        (p.get("zip"), p["id"], p.get("property_type"), sq, sq * 0.75, sq * 1.25)).fetchall()
    if not rows:
        return "No other active listings of this kind in the ZIP in our data."
    asks = sorted(r[0] for r in rows)
    return (f"{len(rows)} similar active listings in {p.get('zip')}: median ask {money(median(asks))}, "
            f"cheapest {money(asks[0])}.")


# ---------- rendering ----------

def _e(x: Any) -> str:
    return html.escape(str(x), quote=False)


def _table(rows: list[list[Any]], head: list[str]) -> str:
    w = ' width="120" data-cell-widths="120"'
    out = "<table><tr>" + "".join(f"<td{w}><strong>{_e(h)}</strong></td>" for h in head) + "</tr>"
    for r in rows:
        out += "<tr>" + "".join(f"<td{w}>{_e(c)}</td>" for c in r) + "</tr>"
    return out + "</table>"


def comp_rows(s: dict[str, Any]) -> list[list[str]]:
    rows = []
    for c in s["comps"]:
        feat = " ".join(x for x, ok in (("pool", c.get("pool")), ("water", c.get("water"))) if ok) or "-"
        rows.append([c.get("address") or "?", str(c.get("sold_date") or "")[:10], money(c["sold_price"]), f"{c['sqft']:,}" if c.get("sqft") else "?",
                     f"${c['ppsf']:,.0f}" if c.get("ppsf") else "?", feat, c.get("condition") or "?",
                     f"{c['distance_mi']} mi" if c.get("distance_mi") is not None else "-", c.get("mls") or "-"])
    return rows


COMP_HEAD = ["Address", "Sold", "Price", "sf", "$/sf", "Pool/Water", "Condition", "Distance", "MLS"]


def headline(s: dict[str, Any]) -> str:
    n = s["numbers"]
    if n.get("ours") is None:
        return "Not enough sales to set a number: see Still open."
    gap = f", {money(n['gap'])} ({n['gap_pct']}%) under the ask" if n.get("gap") and n["gap"] > 0 else (", at or above the ask" if n.get("gap") is not None else "")
    return f"Where we need to be: about {money(n['ours'])}{gap}. ARV {money(n['arv'])}, exit to a flipper about {money(n['exit'])}."


def house_rows(s: dict[str, Any]) -> list[list[str]]:
    p = s["p"]
    size = " · ".join(x for x in (f"{p['sqft']:,} sq ft" if p.get("sqft") else "", f"built {p['year_built']}" if p.get("year_built") else "",
                                  f"{p.get('beds') or '?'} bd / {p.get('baths') or '?'} ba" if p.get("beds") else "",
                                  f"{p['units']} units" if (p.get("units") or 0) > 1 else "") if x)
    rows = [["Asking", money(p.get("list_price"))], ["Size / year", size or "?"],
            ["On market", f"{p.get('days_on_market') or '?'} days" + (f", {p['price_cuts']} price cut(s)" if p.get("price_cuts") else "")],
            ["Condition (remarks)", f"{condition_of(p.get('remarks'))}; engine repair tier: {s['numbers']['repair_tier']}"]]
    if s["numbers"].get("rent"):
        rows.append(["Rent (stated)", f"{money(s['numbers']['rent'])}/mo"])
    return rows


def number_rows(s: dict[str, Any]) -> list[list[str]]:
    n = s["numbers"]
    rows = [["ARV (resale after rehab)", f"{money(n['arv'])} ({n['arv_basis']})" if n.get("arv") else "not enough sales"],
            ["Repairs (estimate)", f"{money(n['repairs'])} ({n['repair_tier']})"]]
    if n.get("exit") is not None:
        rows += [["Exit to end buyer", f"{money(n['exit'])} (ARV × {n['ratio']:.0%} what investors paid here, minus repairs)"],
                 ["Our spread", money(n["fee"])],
                 ["Where we need to be", money(n["ours"])],
                 ["Asking", money(n["list_price"])],
                 ["Gap to the ask", f"{money(n['gap'])} ({n['gap_pct']}%)" if n.get("gap") and n["gap"] > 0 else "none: the ask is at or under our number"]]
    if n.get("gross_yield"):
        rows.append(["Gross rent yield at our number", f"{n['gross_yield']}% ({money(n['rent'] * 12)}/yr)"])
    return rows


def convo(s: dict[str, Any]) -> list[str]:
    out = []
    for m in s["messages"][-6:]:
        who = "Agent" if m.get("direction") == "in" else "Charles"
        body = " ".join((m.get("body") or "").split())[:140]
        out.append(f"{str(m.get('created_at') or '')[:10]} · {who}: {body}")
    return out or ["No replies yet."]


def to_html(s: dict[str, Any]) -> str:
    """Card description (html_notes) in Lise's layout. Only Asana-allowed tags: no <p>, <br>, <th>."""
    p, a = s["p"], s["agent"]
    who = " · ".join(x for x in (a.get("name"), a.get("phone"), a.get("email")) if x)
    parts = [f"<body><h1>{_e(p['street'])}, {_e(p['city'])}</h1>{_e(who)}\n<hr/>",
             f"<h2>Status</h2><strong>{_e(headline(s))}</strong>\nYou asked: <em>{_e(s['asked'][:300])}</em>\n"
             "The email is held in Needs Revision. Comment what to send, or drag the card to Approved to send it as written.\n<hr/>",
             "<h2>The house</h2>" + _table(house_rows(s), ["Item", "Detail"]),
             f"<h2>The comps</h2>{_e(len(s['comps']))} sales, same type, {_e(s['area'])}, last 12 months. Source: {_e(s['source'])}.\n"
             + (_table(comp_rows(s), COMP_HEAD) if s["comps"] else "<em>No comparable sales found.</em>\n")
             + (_table([list(r) for r in s["averages"]], ["Averages", "Value"]) if s["averages"] else ""),
             "<h2>The numbers</h2>" + _table(number_rows(s), ["Line", "Amount"])]
    if s["extra"]:
        parts.append("<h2>Land and new construction</h2>" + _table([list(r) for r in s["extra"]], ["Line", "Value"]))
    if s["screen"]:
        parts.append("<h2>Engine screen (for reference)</h2>" + _table([[k, money(v)] for k, v in s["screen"]], ["Scenario", "Our price"]))
    parts.append(f"<h2>Live competition</h2>{_e(s['active'])}\n<hr/>")
    parts.append("<h2>Still open</h2><ul>" + "".join(f"<li>{_e(x)}</li>" for x in s["still_open"]) + "</ul><hr/>")
    parts.append("<h2>Conversation so far</h2><ul>" + "".join(f"<li>{_e(x)}</li>" for x in convo(s)) + "</ul></body>")
    return "".join(parts)


def to_comment(s: dict[str, Any], card_url: str | None = None) -> str:
    """The same snapshot as a comment on the lead's own card (comments allow no h1/h2/table/hr: <strong> + lists + <pre>)."""
    p = s["p"]
    lines = [f"<body>🤖 <strong>NUMBERS · {_e(p['street'])}, {_e(p['city'])}</strong>\n<strong>{_e(headline(s))}</strong>\n"]
    lines.append("<strong>The comps</strong> (" + _e(f"{len(s['comps'])} sales, {s['area']}, last 12 months") + ")\n<pre>" + _e(text_table(s)) + "</pre>\n")
    if s["averages"]:
        lines.append("<ul>" + "".join(f"<li>{_e(k)}: {_e(v)}</li>" for k, v in s["averages"]) + "</ul>\n")
    lines.append("<strong>The numbers</strong>\n<ul>" + "".join(f"<li>{_e(k)}: {_e(v)}</li>" for k, v in number_rows(s)) + "</ul>\n")
    if s["extra"]:
        lines.append("<strong>Land and new construction</strong>\n<ul>" + "".join(f"<li>{_e(k)}: {_e(v)}</li>" for k, v in s["extra"]) + "</ul>\n")
    if s["screen"]:
        lines.append("<strong>Engine screen</strong>\n<ul>" + "".join(f"<li>{_e(k)}: {_e(money(v))}</li>" for k, v in s["screen"]) + "</ul>\n")
    lines.append(f"<strong>Live competition</strong>: {_e(s['active'])}\n")
    lines.append("<strong>Still open</strong>\n<ul>" + "".join(f"<li>{_e(x)}</li>" for x in s["still_open"]) + "</ul>\n")
    lines.append("Email held in Needs Revision until you decide: comment what to send, or drag to Approved to send it as written."
                 + (f' Full snapshot: <a href="{_e(card_url)}">{_e(card_url)}</a>' if card_url else "") + "</body>")
    return "".join(lines)


def text_table(s: dict[str, Any]) -> str:
    rows = [[r[0][:26], r[1], r[2], r[3], r[4], r[6][:10]] for r in comp_rows(s)]
    head = ["Address", "Sold", "Price", "sf", "$/sf", "Cond"]
    if not rows:
        return "No comparable sales found."
    w = [max(len(str(x[i])) for x in rows + [head]) for i in range(len(head))]
    fmt = lambda r: "  ".join(str(c).ljust(w[i]) for i, c in enumerate(r))  # noqa: E731
    return "\n".join([fmt(head)] + [fmt(r) for r in rows])


def to_text(s: dict[str, Any]) -> str:
    """Plain-text version (reports/comps/<slug>.md and logs)."""
    p = s["p"]
    out = [f"# {p['street']}, {p['city']}  ({s['date']})", "", f"**{headline(s)}**", "", "## The house"]
    out += [f"- {k}: {v}" for k, v in house_rows(s)]
    out += ["", f"## The comps ({len(s['comps'])} sales, {s['area']}, last 12 months; {s['source']})", "", text_table(s), ""]
    out += [f"- {k}: {v}" for k, v in s["averages"]]
    out += ["", "## The numbers"] + [f"- {k}: {v}" for k, v in number_rows(s)]
    if s["extra"]:
        out += ["", "## Land and new construction"] + [f"- {k}: {v}" for k, v in s["extra"]]
    if s["screen"]:
        out += ["", "## Engine screen"] + [f"- {k}: {money(v)}" for k, v in s["screen"]]
    out += ["", f"Live competition: {s['active']}", "", "## Still open"] + [f"- {x}" for x in s["still_open"]]
    return "\n".join(out) + "\n"
