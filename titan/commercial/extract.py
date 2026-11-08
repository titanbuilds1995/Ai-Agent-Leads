"""Pull the numbers and the signals out of a LoopNet listing's text and facts: price, cap, NOI, gross income, expenses, occupancy,
tenancy, lease type, remaining term, motivation language, pro-forma language, credit tenants, brokerage size. Pure functions, no I/O."""
from __future__ import annotations

import re
from typing import Any

_MONEY = r"\$\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(million|mm|m|k)?"


def money(text: str | None) -> int | None:
    """'$7.895M' → 7895000; '$512,244' → 512244; '$1.2 million' → 1200000. None for 'Price Not Disclosed' etc."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(text)
    m = re.search(_MONEY, str(text), re.I)
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    if unit in ("million", "mm", "m"):
        n *= 1_000_000
    elif unit == "k":
        n *= 1_000
    return int(round(n))


def money_range(text: str | None) -> tuple[int | None, int | None]:
    """'$689.7K - $3M' → (689700, 3000000); a single price → (p, p)."""
    if not text:
        return None, None
    parts = re.split(r"\s[-–]\s|\s+to\s+", str(text))
    vals = [money(p) for p in parts]
    vals = [v for v in vals if v]
    if not vals:
        return None, None
    return min(vals), max(vals)


def percent(text: Any) -> float | None:
    """'9.35%' → 9.35; 'On Request' → None; 7.5 → 7.5."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = re.search(r"(\d{1,2}(?:\.\d{1,2})?)\s*%", str(text))
    return float(m.group(1)) if m else None


def number(text: Any) -> float | None:
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = re.search(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)", str(text))
    return float(m.group(1).replace(",", "")) if m else None


def sqft(text: Any) -> int | None:
    """'55,000 SF' → 55000; '2.5 AC' → acres×43560; '55K' → 55000."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(text)
    s = str(text)
    m = re.search(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k)?\s*(ac|acre|acres|sf|sq\.? ?ft)?", s, re.I)
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    if m.group(2):
        n *= 1000
    unit = (m.group(3) or "").lower()
    if unit.startswith("ac"):
        n *= 43560
    return int(round(n))


# --- signals in the text ----------------------------------------------------------------------------------------------

NOI_RE = re.compile(r"(?:net operating income|\bNOI\b)[^$\d]{0,40}" + _MONEY, re.I)
NOI_PCT_FIRST_RE = re.compile(_MONEY + r"[^.\n]{0,30}(?:net operating income|\bNOI\b)", re.I)
GROSS_RE = re.compile(r"(?:gross (?:scheduled |potential |annual |operating )?(?:income|rent|revenue)|\bGSI\b|\bGPR\b|\bEGI\b|effective gross income|"
                      r"total (?:annual )?(?:income|revenue))[^$\d]{0,40}" + _MONEY, re.I)
EXPENSES_RE = re.compile(r"(?:operating expenses|total expenses|\bopex\b|expenses)[^$\d]{0,30}" + _MONEY, re.I)
OCC_RE = re.compile(r"(\d{1,3})\s?%\s*(?:occupied|occupancy|leased)|(?:occupancy|occupied|leased)[^.\d\n]{0,25}?(\d{1,3})\s?%", re.I)
FULLY_LEASED_RE = re.compile(r"(?<!to )(?<!reach )(?<!achieve )(?<!toward )(?<!towards )(?<!from )\b(100%\s*(?:occupied|leased)|fully (?:occupied|leased)|full occupancy)\b"
                             r"(?!\s*(?:initiative|potential|upside|once|upon|would|could|target|goal|achiev|pro ?forma|scenario))", re.I)
VACANT_RE = re.compile(r"\b(vacant|owner[- ]user|owner/user|delivered vacant|vacant at closing)\b", re.I)
CAP_TEXT_RE = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s?%\s*(?:cap(?: rate)?|capitalization)|cap(?: rate)?[^.\d\n]{0,25}?(\d{1,2}(?:\.\d{1,2})?)\s?%", re.I)
PROFORMA_RE = re.compile(r"\b(pro[- ]?forma|projected|upon stabilization|stabilized|year[- ]?(?:1|one|2|two) (?:noi|cap)|market rent cap|"
                         r"potential (?:noi|cap|income)|at full occupancy|once (?:leased|stabilized)|in[- ]place (?:noi|cap) of)\b", re.I)
TRAILING_RE = re.compile(r"\b(trailing(?: twelve|[- ]12| 12 months?)?|t-?12|ttm|actual(?:s)?|in[- ]place|current (?:noi|cap|income)|"
                         r"202\d (?:noi|actuals?|financials)|year[- ]end)\b", re.I)
MOTIVATION = {
    "priced to sell": r"priced to sell|priced (?:aggressively|below|to move)|aggressive(?:ly)? priced",
    "owner retiring": r"owner (?:is )?retir|retirement sale|retiring owner",
    "bring offers": r"bring (?:all |your )?offers?|all (?:reasonable )?offers (?:considered|welcome)|submit (?:all |your )?offers?|open to offers|make (?:an|us an) offer",
    "motivated": r"\bmotivated\b|must sell|needs? to sell|seller (?:is )?(?:very )?motivated|highly motivated",
    "below replacement cost": r"below replacement cost|fraction of replacement cost|under replacement",
    "estate sale": r"estate sale|\bestate\b.{0,20}(?:sale|liquidat)|probate|trust sale|heirs?",
    "price reduced": r"price (?:reduced|reduction|drop|cut|improvement)|reduced price|new(?:ly)? (?:reduced )?price|just reduced",
    "value-add": r"value[- ]add|upside|below market rents?|under[- ]market",
    "seller financing": r"seller financing|owner financing|owner will carry|seller will carry",
    "distress": r"\b(?:foreclosure|receivership|lender[- ]owned|\breo\b|bank[- ]owned|short sale|bankruptcy|court[- ]ordered|distress)\b",
    "1031": r"1031|exchange buyer",
}
_MOTIVATION_RE = {k: re.compile(v, re.I) for k, v in MOTIVATION.items()}

LEASE_TYPES = [   # most specific first
    ("absolute nnn", r"absolute(?:ly)? (?:nnn|net|triple[- ]net)|zero landlord|no landlord responsibilit|bond(?:able)? lease"),
    ("nnn", r"\bnnn\b|triple[- ]net|\bnet[- ]net[- ]net\b"),
    ("nn", r"\bnn\b|double[- ]net"),
    ("modified gross", r"modified gross|\bmg\b lease|modified net"),
    ("gross", r"\bfull[- ]service\b|\bgross lease|\bgross\b(?! income| rent| revenue| scheduled| potential| sf| sq)"),
    ("ground lease", r"ground lease"),
]
_LEASE_RE = [(k, re.compile(v, re.I)) for k, v in LEASE_TYPES]

TERM_RE = re.compile(r"(\d{1,2}(?:\.\d)?)\+?\s*(?:years?|yrs?)\s*(?:remain(?:ing)?|left|of (?:primary |base )?(?:lease )?term|(?:lease )?term(?: remaining)?)|"
                     r"(?:remaining|left)(?: on the lease| lease term)?[^.\d\n]{0,25}?(\d{1,2}(?:\.\d)?)\s*(?:years?|yrs?)|"
                     r"(?:lease (?:expires?|expiration|through|until|thru)|expires?|through)\s*(?:in\s*)?(20\d\d)", re.I)
TENANTS_RE = re.compile(r"(\d{1,3})\s*(?:-|\s)?(?:tenants?|suites?|units?|bays?)\b", re.I)
SINGLE_TENANT_RE = re.compile(r"single[- ]tenant|sole tenant|one tenant|100% leased to", re.I)
MULTI_TENANT_RE = re.compile(r"multi[- ]tenant|strip center|shopping center|plaza\b|tenant mix|anchored", re.I)
HOTEL_METRICS_RE = re.compile(r"\b(revpar|\badr\b|average daily rate|occupancy rate|\bstr report\b|room nights)\b", re.I)
RENT_ROLL_RE = re.compile(r"rent roll|t-?12|trailing twelve|profit (?:&|and) loss|p&l|operating statement|financial(?:s)? (?:available|statement)", re.I)
OM_GATED_RE = re.compile(r"(?:sign|execute|complete)[^.\n]{0,30}(?:nda|confidentiality agreement|\bca\b)|upon request|available upon|contact (?:broker|agent) for", re.I)

CREDIT_TENANTS = [
    "dollar general", "walgreens", "cvs", "starbucks", "7-eleven", "7 eleven", "wawa", "chick-fil-a", "chick fil a", "dollar tree", "family dollar",
    "autozone", "o'reilly", "oreilly", "advance auto", "tractor supply", "publix", "walmart", "taco bell", "wendy's", "burger king", "mcdonald's",
    "kfc", "popeyes", "dunkin", "chase", "bank of america", "wells fargo", "truist", "pnc", "fifth third", "regions bank", "verizon", "at&t",
    "t-mobile", "sherwin-williams", "sherwin williams", "caliber collision", "take 5", "valvoline", "jiffy lube", "firestone", "goodyear",
    "davita", "fresenius", "aspen dental", "planet fitness", "circle k", "racetrac", "shell", "chevron", "mobil", "marathon", "sunoco",
    "hobby lobby", "ross", "tj maxx", "t.j. maxx", "burlington", "big lots", "harbor freight", "mavis", "pep boys", "petsmart", "petco",
    "panera", "chipotle", "jersey mike's", "dutch bros", "raising cane's", "whataburger", "culver's", "zaxby's", "bojangles", "hardee's",
    "arby's", "sonic", "checkers", "pollo tropical", "fedex", "ups store", "usps", "united states postal", "amazon", "lowe's", "home depot",
    "aldi", "winn-dixie", "winn dixie", "quest diagnostics", "labcorp", "baptist health", "hca", "adventhealth", "orlando health",
    "veterans affairs", "u.s. government", "gsa", "state of florida", "napa auto", "pet supermarket", "tire kingdom", "wingstop",
    "five guys", "jimmy john's", "firehouse subs", "tropical smoothie", "domino's", "pizza hut", "papa john's", "walgreen", "rite aid",
    "kohl's", "target", "best buy", "office depot", "staples", "michaels", "ulta", "five below", "ollie's", "planet fitness", "la fitness",
    "crunch fitness", "bank ozk", "seacoast bank", "bankunited", "td bank", "citibank", "capital one", "synovus", "ameris", "south state",
    "cracker barrel", "ihop", "denny's", "waffle house", "golden corral", "red lobster", "olive garden", "longhorn", "outback", "applebee's",
    "chili's", "texas roadhouse", "buffalo wild wings", "hooters", "tijuana flats", "moe's", "panda express", "el pollo loco", "bb&t",
    "suntrust", "enterprise rent", "hertz", "avis", "u-haul", "public storage", "extra space", "cubesmart", "life storage",
]
_CREDIT_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in sorted(CREDIT_TENANTS, key=len, reverse=True)) + r")\b", re.I)

INSTITUTIONAL = ["cbre", "jll", "jones lang lasalle", "cushman", "marcus & millichap", "marcus and millichap", "colliers", "newmark", "berkadia",
                 "walker & dunlop", "avison young", "lee & associates", "srs real estate", "kidder mathews", "matthews", "northmarq", "franklin street",
                 "transwestern", "savills", "stream realty", "eastdil", "institutional property advisors", "\\bipa\\b", "hff", "nai ", "berkshire hathaway",
                 "keller williams commercial", "kw commercial", "compass", "coldwell banker commercial", "sperry", "svn", "re/max commercial",
                 "jones lang", "colliers international", "costar", "ten-x", "crexi", "elliman", "sotheby", "coldwell banker", "keller williams",
                 "re/max", "century 21", "better homes", "engel", "cbre", "cushman & wakefield"]
REGIONAL_HINTS = ["commercial", "realty", "properties", "group", "partners", "advisors", "capital", "investments", "brokerage"]
_INST_RE = re.compile("|".join(INSTITUTIONAL), re.I)


def noi(text: str) -> int | None:
    m = NOI_RE.search(text or "")
    if m:
        return money(m.group(0)[m.group(0).find("$"):])
    m = NOI_PCT_FIRST_RE.search(text or "")
    return money(m.group(0)) if m else None


def gross_income(text: str) -> int | None:
    m = GROSS_RE.search(text or "")
    return money(m.group(0)[m.group(0).find("$"):]) if m else None


def expenses(text: str) -> int | None:
    m = EXPENSES_RE.search(text or "")
    return money(m.group(0)[m.group(0).find("$"):]) if m else None


def occupancy(text: str) -> float | None:
    if FULLY_LEASED_RE.search(text or ""):
        return 100.0
    m = OCC_RE.search(text or "")
    if m:
        v = float(m.group(1) or m.group(2))
        return v if 0 < v <= 100 else None
    return None


def cap_from_text(text: str) -> float | None:
    m = CAP_TEXT_RE.search(text or "")
    if not m:
        return None
    v = float(m.group(1) or m.group(2))
    return v if 1 <= v <= 25 else None


def lease_type(text: str) -> str | None:
    for name, rx in _LEASE_RE:
        if rx.search(text or ""):
            return name
    return None


def remaining_term_years(text: str, today_year: int = 2026) -> float | None:
    m = TERM_RE.search(text or "")
    if not m:
        return None
    if m.group(1):
        return float(m.group(1))
    if m.group(2):
        return float(m.group(2))
    if m.group(3):
        yrs = int(m.group(3)) - today_year
        return float(yrs) if yrs >= 0 else None
    return None


def tenant_count(text: str) -> int | None:
    if SINGLE_TENANT_RE.search(text or ""):
        return 1
    best = None
    for m in TENANTS_RE.finditer(text or ""):
        n = int(m.group(1))
        if 1 <= n <= 300:
            best = n if best is None else max(best, n)
    return best


def tenancy(text: str, tenants: int | None) -> str | None:
    if SINGLE_TENANT_RE.search(text or "") or tenants == 1:
        return "single"
    if MULTI_TENANT_RE.search(text or "") or (tenants or 0) > 1:
        return "multi"
    return None


def credit_tenants(text: str) -> list[str]:
    seen: list[str] = []
    for m in _CREDIT_RE.finditer(text or ""):
        name = m.group(1).lower()
        if name not in seen:
            seen.append(name)
    return seen


def motivation_signals(text: str) -> list[str]:
    return [k for k, rx in _MOTIVATION_RE.items() if rx.search(text or "")]


def is_proforma(text: str) -> bool:
    return bool(PROFORMA_RE.search(text or ""))


def mentions_trailing(text: str) -> bool:
    return bool(TRAILING_RE.search(text or ""))


def has_hotel_metrics(text: str) -> bool:
    return bool(HOTEL_METRICS_RE.search(text or ""))


def mentions_rent_roll(text: str) -> bool:
    return bool(RENT_ROLL_RE.search(text or ""))


def brokerage_size(name: str | None) -> str:
    """institutional | regional | small. Small/local brokerages are the easier conversation (buyer's scoring rule 6)."""
    n = (name or "").strip()
    if not n:
        return "small"
    if _INST_RE.search(n):
        return "institutional"
    low = n.lower()
    if any(h in low for h in REGIONAL_HINTS) and len(low.split()) >= 3:
        return "regional"
    return "small"


def expense_ratio(noi_v: int | None, gross: int | None) -> float | None:
    if not noi_v or not gross or gross <= 0 or noi_v > gross:
        return None
    return round(1 - noi_v / gross, 3)
