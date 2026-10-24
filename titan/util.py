"""Shared helpers: logging, retry/backoff, address + phone normalization, ET time windows."""
from __future__ import annotations

import logging
import logging.handlers
import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, TypeVar
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
T = TypeVar("T")

_LOG_READY = False


def setup_logging(log_dir: Path | str = "logs", name: str = "titan", level: int = logging.INFO) -> logging.Logger:
    global _LOG_READY
    logger = logging.getLogger(name)
    if _LOG_READY:
        return logger
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(Path(log_dir) / f"{name}.log", maxBytes=5_000_000, backupCount=5)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.setLevel(level)
    logger.propagate = False
    _LOG_READY = True
    return logger


log = logging.getLogger("titan")


class RetryError(Exception):
    pass


def retry(fn: Callable[[], T], *, attempts: int = 4, base: float = 1.5, max_wait: float = 60.0,
          retry_on: tuple[type[BaseException], ...] = (Exception,), what: str = "call",
          sleep: Callable[[float], None] = time.sleep) -> T:
    """Call fn with exponential backoff + jitter. Re-raises the last error wrapped in RetryError."""
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except retry_on as e:  # noqa: PERF203
            last = e
            if i == attempts - 1:
                break
            wait = min(max_wait, base * (2 ** i)) + random.uniform(0, 0.5)
            log.warning("%s failed (%s: %s); retry %d/%d in %.1fs", what, type(e).__name__, e, i + 1, attempts - 1, wait)
            sleep(wait)
    raise RetryError(f"{what} failed after {attempts} attempts: {last}") from last


# --- time -------------------------------------------------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_et() -> datetime:
    return datetime.now(ET)


def iso(dt: datetime | None = None) -> str:
    return (dt or now_utc()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def today_et() -> str:
    return now_et().strftime("%Y-%m-%d")


def et_day_bounds(run_date: str) -> tuple[str, str]:
    """UTC ISO bounds [start, end) of one Eastern-time calendar day, for comparing against stored UTC timestamps."""
    d = datetime.strptime(run_date, "%Y-%m-%d").replace(tzinfo=ET)
    return iso(d), iso(d + timedelta(days=1))


def in_send_window(dt: datetime | None, start_hour: int, end_hour: int, weekdays_only: bool = True,
                   tz: str = "America/New_York") -> bool:
    """True when dt (default now) is inside [start_hour, end_hour) local time, Mon–Fri if weekdays_only."""
    z = ZoneInfo(tz)
    local = (dt or now_utc()).astimezone(z)
    if weekdays_only and local.weekday() >= 5:
        return False
    return start_hour <= local.hour < end_hour


def add_business_days(dt: datetime, days: int) -> datetime:
    d = dt
    added = 0
    while added < days:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def business_days_since(start: datetime, end: datetime | None = None) -> int:
    end = end or now_utc()
    if end <= start:
        return 0
    n, d = 0, start
    while d.date() < end.date():
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


# --- addresses ----------------------------------------------------------------

_SUFFIX = {
    "street": "st", "str": "st", "avenue": "ave", "av": "ave", "boulevard": "blvd", "road": "rd", "drive": "dr",
    "court": "ct", "circle": "cir", "lane": "ln", "place": "pl", "terrace": "ter", "terr": "ter", "trail": "trl",
    "parkway": "pkwy", "highway": "hwy", "way": "way", "loop": "loop", "cove": "cv", "point": "pt", "square": "sq",
}
_DIR = {"north": "n", "south": "s", "east": "e", "west": "w", "northeast": "ne", "northwest": "nw",
        "southeast": "se", "southwest": "sw"}
_UNIT = re.compile(r"\b(apt|unit|ste|suite|#)\s*\.?\s*([a-z0-9-]+)", re.I)


def normalize_address(street: str | None, city: str | None = None, state: str | None = None,
                      zip_code: str | None = None) -> str:
    """Stable key for one property: '123 nw 19th st|miami|fl|33125' (unit kept when present)."""
    s = (street or "").lower().strip()
    s = s.replace(".", "").replace(",", " ")
    unit = ""
    m = _UNIT.search(s)
    if m:
        unit = f" #{m.group(2)}"
        s = _UNIT.sub(" ", s)
    toks = [t for t in re.split(r"\s+", s) if t]
    out = []
    for t in toks:
        t = _DIR.get(t, t)
        t = _SUFFIX.get(t, t)
        out.append(t)
    street_n = " ".join(out) + unit
    city_n = re.sub(r"\s+", " ", (city or "").lower().strip())
    state_n = (state or "").lower().strip()[:2]
    zip_n = (zip_code or "").strip()[:5]
    return f"{street_n}|{city_n}|{state_n}|{zip_n}"


COUNTY_BY_CITY = {
    # Miami-Dade
    "miami": "Miami-Dade", "hialeah": "Miami-Dade", "homestead": "Miami-Dade", "miami gardens": "Miami-Dade",
    "north miami": "Miami-Dade", "north miami beach": "Miami-Dade", "miami beach": "Miami-Dade", "coral gables": "Miami-Dade",
    "doral": "Miami-Dade", "kendall": "Miami-Dade", "cutler bay": "Miami-Dade", "opa-locka": "Miami-Dade", "opa locka": "Miami-Dade",
    "sweetwater": "Miami-Dade", "miami lakes": "Miami-Dade", "palmetto bay": "Miami-Dade", "pinecrest": "Miami-Dade",
    "aventura": "Miami-Dade", "homestead base": "Miami-Dade", "florida city": "Miami-Dade", "south miami": "Miami-Dade",
    "miami springs": "Miami-Dade", "west miami": "Miami-Dade", "medley": "Miami-Dade", "key biscayne": "Miami-Dade",
    # Broward
    "fort lauderdale": "Broward", "ft lauderdale": "Broward", "hollywood": "Broward", "pembroke pines": "Broward",
    "miramar": "Broward", "coral springs": "Broward", "pompano beach": "Broward", "davie": "Broward", "plantation": "Broward",
    "sunrise": "Broward", "deerfield beach": "Broward", "lauderhill": "Broward", "weston": "Broward", "tamarac": "Broward",
    "margate": "Broward", "coconut creek": "Broward", "north lauderdale": "Broward", "oakland park": "Broward",
    "hallandale beach": "Broward", "hallandale": "Broward", "lauderdale lakes": "Broward", "dania beach": "Broward",
    "cooper city": "Broward", "parkland": "Broward", "wilton manors": "Broward", "lighthouse point": "Broward",
    "west park": "Broward", "pembroke park": "Broward", "lauderdale by the sea": "Broward", "southwest ranches": "Broward",
    # Palm Beach
    "west palm beach": "Palm Beach", "boca raton": "Palm Beach", "boynton beach": "Palm Beach", "delray beach": "Palm Beach",
    "lake worth": "Palm Beach", "lake worth beach": "Palm Beach", "wellington": "Palm Beach", "jupiter": "Palm Beach",
    "palm beach gardens": "Palm Beach", "royal palm beach": "Palm Beach", "greenacres": "Palm Beach", "riviera beach": "Palm Beach",
    "palm springs": "Palm Beach", "lantana": "Palm Beach", "belle glade": "Palm Beach", "north palm beach": "Palm Beach",
    "palm beach": "Palm Beach", "loxahatchee": "Palm Beach", "pahokee": "Palm Beach", "tequesta": "Palm Beach",
    "west palm": "Palm Beach", "lake park": "Palm Beach", "the acreage": "Palm Beach", "south bay": "Palm Beach",
}


def county_for(city: str | None, explicit: str | None = None) -> str | None:
    if explicit:
        e = explicit.replace(" County", "").strip()
        return {"miami dade": "Miami-Dade", "miami-dade": "Miami-Dade", "dade": "Miami-Dade", "broward": "Broward",
                "palm beach": "Palm Beach"}.get(e.lower(), e)
    if not city:
        return None
    return COUNTY_BY_CITY.get(city.lower().strip())


# --- phones -------------------------------------------------------------------

def normalize_phone(raw: str | None) -> str | None:
    """US numbers → E.164 (+1XXXXXXXXXX). Returns None when unparseable."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] in "01":
        return None
    return f"+1{digits}"


def pretty_phone(e164: str | None) -> str:
    """+13055550100 → (305) 555-0100 for human-facing text."""
    if not e164:
        return ""
    d = re.sub(r"\D", "", e164)
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else e164


def display_street(street: str | None) -> str:
    """'107 N.W. 11th STREET' → '107 NW 11th Street' for drafts and reports (the DB key is normalize_address)."""
    s = re.sub(r"\s+", " ", (street or "")).strip()
    s = re.sub(r"\b([NSEW])\.\s?([NSEW])\.", lambda m: m.group(1) + m.group(2), s, flags=re.I)
    s = re.sub(r"\b([NSEW]{1,2})\.", r"\1", s, flags=re.I)
    out = []
    for tok in s.split(" "):
        if re.fullmatch(r"[NSEW]{1,2}", tok, re.I):
            out.append(tok.upper())
        elif re.fullmatch(r"\d+(st|nd|rd|th)", tok, re.I):
            out.append(tok.lower())
        elif tok.isupper() and len(tok) >= 2:
            out.append(tok.title())
        elif tok.islower() and len(tok) > 1:
            out.append(tok.title())
        else:
            out.append(tok)
    return " ".join(out)


_NOT_A_NAME = {"dr", "mr", "mrs", "ms", "team", "the", "realtor", "broker", "pa", "llc", "inc", "group", "associates", "realty"}


def first_name(full: str | None) -> str:
    """'CHERYL A MEDEIROS REALTOR' → 'Cheryl'; 'C Glansen, Broker' / 'Dr. Ana Perez' / 'Team Smith' → the first real first name,
    else 'there' (so the greeting reads 'Hey there,' instead of 'Hey C,')."""
    if not full:
        return "there"
    for part in re.split(r"[\s,]+", full.strip()):
        fn = re.sub(r"[^A-Za-z'\-]", "", part)
        if len(fn) >= 2 and fn.lower() not in _NOT_A_NAME:
            return fn.title()
    return "there"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def chunks(items: Iterable[T], n: int) -> Iterable[list[T]]:
    buf: list[T] = []
    for it in items:
        buf.append(it)
        if len(buf) == n:
            yield buf
            buf = []
    if buf:
        yield buf


def money(x: float | int | None) -> str:
    if x is None:
        return "n/a"
    return f"${x:,.0f}"


def inspection_phrase(terms: dict) -> str:
    """How the inspection term is written everywhere the engine speaks. Driven only by config.outreach.terms."""
    if not terms.get("inspection_contingency"):
        return "no inspection contingency"
    d = terms.get("inspection_days")
    return f"a {int(d)}-day inspection period" if d else "a short inspection period"
