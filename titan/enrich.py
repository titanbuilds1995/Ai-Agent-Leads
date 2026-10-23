"""AGENT CONTACT ENRICHMENT: find the listing agent's email. Chain stops at the first verified hit.

(a) email in listing payload            → confidence high
(b) agent page on the brokerage website → high (name match on page) / medium (only one email on the page)
(c) realtor.com / zillow / homes.com agent profiles → high
(d) Florida DBPR licensee search        → medium (DBPR shows emails only for some licensees)
(e) pattern inference from other known emails at the brokerage, verified by MX + SMTP RCPT (no mail sent) → medium / low

Everything network-facing is injectable so the chain is unit-testable offline.
"""
from __future__ import annotations

import re
import smtplib
import socket
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote_plus, urlparse

import requests

from . import db
from .util import log, today_et

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
GENERIC_LOCAL = {"info", "contact", "office", "admin", "sales", "support", "hello", "frontdesk", "listings", "noreply", "no-reply",
                 "careers", "webmaster", "marketing", "team", "help", "service", "customerservice", "privacy", "legal", "press"}
FREE_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "aol.com", "outlook.com", "icloud.com", "me.com", "live.com", "msn.com", "comcast.net", "bellsouth.net"}
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

Fetch = Callable[[str], str | None]           # url -> html (None on failure)
MX = Callable[[str], list[str]]                # domain -> mx hosts
Rcpt = Callable[[str, str], bool | None]       # (mx_host, email) -> True/False/None(unknown)


@dataclass
class Hit:
    email: str
    confidence: str      # high | medium | low
    source: str


CONF_RANK = {"high": 3, "medium": 2, "low": 1}


def _name_parts(name: str | None) -> tuple[str, str]:
    toks = [t for t in re.split(r"[\s,]+", (name or "").lower()) if t and t not in {"jr", "sr", "ii", "iii", "pa", "p.a.", "llc", "realtor", "broker"}]
    toks = [re.sub(r"[^a-z]", "", t) for t in toks]
    toks = [t for t in toks if t]
    if not toks:
        return "", ""
    return toks[0], (toks[-1] if len(toks) > 1 else "")


def name_matches(email: str, name: str | None) -> bool:
    first, last = _name_parts(name)
    local = email.split("@")[0].lower()
    local_clean = re.sub(r"[^a-z]", "", local)
    if not first:
        return False
    if local_clean == first or (last and local_clean == last and len(last) > 4):
        return True
    if last and last in local_clean and (first[0] in local_clean or first in local_clean):
        return True
    if first in local_clean and (not last or last[0] in local_clean):
        return True
    return False


def candidate_patterns(first: str, last: str, domain: str) -> list[str]:
    if not first or not domain:
        return []
    pats = [f"{first}.{last}", f"{first}{last}", f"{first[0]}{last}", f"{first}", f"{first}_{last}", f"{first}{last[0] if last else ''}", f"{last}.{first}", f"{first[0]}.{last}"]
    out: list[str] = []
    for p in pats:
        p = p.strip("._")
        if p and f"{p}@{domain}" not in out:
            out.append(f"{p}@{domain}")
    return out


def detect_pattern(known: list[tuple[str, str]]) -> str | None:
    """known: [(agent name, email)] at one domain → pattern token like 'first.last' when ≥ 2 agree."""
    votes: dict[str, int] = {}
    for name, email in known:
        f, l = _name_parts(name)
        local = email.split("@")[0].lower()
        for label, cand in (("first.last", f"{f}.{l}"), ("firstlast", f"{f}{l}"), ("flast", f"{f[:1]}{l}"), ("first", f),
                            ("first_last", f"{f}_{l}"), ("firstl", f"{f}{l[:1]}"), ("last.first", f"{l}.{f}"), ("f.last", f"{f[:1]}.{l}")):
            if cand and cand == local:
                votes[label] = votes.get(label, 0) + 1
    if not votes:
        return None
    best = max(votes.items(), key=lambda kv: kv[1])
    return best[0] if best[1] >= 2 else None


def apply_pattern(pattern: str, first: str, last: str, domain: str) -> str | None:
    m = {"first.last": f"{first}.{last}", "firstlast": f"{first}{last}", "flast": f"{first[:1]}{last}", "first": first,
         "first_last": f"{first}_{last}", "firstl": f"{first}{last[:1]}", "last.first": f"{last}.{first}", "f.last": f"{first[:1]}.{last}"}
    local = m.get(pattern)
    return f"{local}@{domain}" if local and first and (last or pattern == "first") else None


# --- network primitives (default implementations) ------------------------------------------

def default_fetch(timeout: int = 15, session: requests.Session | None = None) -> Fetch:
    s = session or requests.Session()

    def fetch(url: str) -> str | None:
        try:
            r = s.get(url, headers=UA, timeout=timeout, allow_redirects=True)
            if r.status_code == 200 and "text/html" in r.headers.get("content-type", "text/html"):
                return r.text[:2_000_000]
        except requests.RequestException as e:
            log.debug("fetch %s failed: %s", url, e)
        return None
    return fetch


def default_mx(domain: str) -> list[str]:
    try:
        import dns.resolver
        answers = dns.resolver.resolve(domain, "MX", lifetime=8)
        return [str(r.exchange).rstrip(".") for r in sorted(answers, key=lambda a: a.preference)]
    except Exception:  # noqa: BLE001
        return []


def default_rcpt(timeout: int = 8, helo_domain: str = "titanrealtyoffers.com", mail_from: str = "verify@titanrealtyoffers.com") -> Rcpt:
    def rcpt(mx_host: str, email: str) -> bool | None:
        try:
            with smtplib.SMTP(mx_host, 25, timeout=timeout) as s:
                s.ehlo(helo_domain)
                s.mail(mail_from)
                code, _ = s.rcpt(email)
                if code == 250:
                    return True
                if code in (550, 551, 553, 554):
                    return False
                return None
        except (smtplib.SMTPException, socket.error, OSError):
            return None
    return rcpt


# --- the chain ---------------------------------------------------------------------------------

class Enricher:
    def __init__(self, cfg: Any, conn, fetch: Fetch | None = None, mx: MX | None = None, rcpt: Rcpt | None = None,
                 search: Callable[[str], list[str]] | None = None):
        self.cfg, self.conn = cfg, conn
        e = cfg["enrichment"]
        self.fetch = fetch or default_fetch(e.get("http_timeout_seconds", 15))
        self.mx = mx or default_mx
        self.rcpt = rcpt or (default_rcpt(e.get("smtp_timeout_seconds", 8), helo_domain=self._sender_domain(), mail_from=f"verify@{self._sender_domain()}") if e.get("smtp_verify", True) else (lambda h, m: None))
        self.search = search or self._duckduckgo
        self.min_known = e.get("pattern_min_known_emails", 2)

    def _sender_domain(self) -> str:
        em = self.cfg.get("operator.reply_to_email", "") or ""
        return em.split("@")[-1] if "@" in em else "example.com"

    # (b)+(c)+(d) all need "find the agent's page"; search engine fallback when the portal/brokerage URL isn't known
    def _duckduckgo(self, query: str) -> list[str]:
        html = self.fetch(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}")
        if not html:
            return []
        urls = re.findall(r'href="(https?://[^"]+)"', html)
        out = []
        for u in urls:
            if "duckduckgo.com" in u:
                m = re.search(r"uddg=([^&]+)", u)
                if m:
                    from urllib.parse import unquote
                    u = unquote(m.group(1))
                else:
                    continue
            if u not in out:
                out.append(u)
        return out[:8]

    def _emails_in(self, html: str) -> list[str]:
        found = []
        for e in EMAIL_RE.findall(html or ""):
            e = e.lower().rstrip(".")
            if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
                continue
            if e.split("@")[0] in GENERIC_LOCAL:
                continue
            if e not in found:
                found.append(e)
        return found

    def _pick(self, emails: list[str], name: str | None, source: str) -> Hit | None:
        for e in emails:
            if name_matches(e, name):
                return Hit(e, "high", source)
        if len(emails) == 1 and emails[0].split("@")[1] not in FREE_DOMAINS:
            return Hit(emails[0], "medium", source)
        return None

    def brokerage_domain(self, brokerage_key: str) -> str | None:
        rows = self.conn.execute("SELECT email FROM agents WHERE brokerage_key=? AND email IS NOT NULL", (brokerage_key,)).fetchall()
        doms: dict[str, int] = {}
        for r in rows:
            d = r["email"].split("@")[1]
            if d not in FREE_DOMAINS:
                doms[d] = doms.get(d, 0) + 1
        return max(doms, key=doms.get) if doms else None

    # rungs -------------------------------------------------------------------------------------
    def rung_payload(self, agent: dict[str, Any]) -> Hit | None:
        if agent.get("email"):
            return Hit(agent["email"], agent.get("email_confidence") or "high", agent.get("email_source") or "listing_payload")
        return None

    def rung_brokerage_site(self, agent: dict[str, Any]) -> Hit | None:
        name, brok = agent.get("name"), agent.get("brokerage")
        if not name or not brok:
            return None
        domain = self.brokerage_domain(agent["brokerage_key"])
        urls: list[str] = []
        if domain:
            urls += [f"https://{domain}", f"https://www.{domain}/agents", f"https://www.{domain}/our-team"]
        urls += [u for u in self.search(f'"{name}" "{brok}" email') if not any(p in u for p in ("realtor.com", "zillow.com", "homes.com", "facebook.com", "linkedin.com"))][:4]
        for u in urls:
            html = self.fetch(u)
            if not html:
                continue
            hit = self._pick(self._emails_in(html), name, f"brokerage_site:{urlparse(u).netloc}")
            if hit:
                return hit
        return None

    def rung_portals(self, agent: dict[str, Any]) -> Hit | None:
        name = agent.get("name")
        if not name:
            return None
        urls: list[str] = []
        pu = db.uj(agent.get("portal_urls"), {}) or {}
        if pu.get("profile"):
            urls.append(pu["profile"])
        for portal in self.cfg.get("enrichment.portals", []):
            urls += [u for u in self.search(f'site:{portal} "{name}" {agent.get("brokerage") or ""}') if portal in u][:2]
        for u in urls:
            html = self.fetch(u)
            if not html:
                continue
            hit = self._pick(self._emails_in(html), name, f"portal:{urlparse(u).netloc}")
            if hit:
                return hit
        return None

    def rung_dbpr(self, agent: dict[str, Any]) -> Hit | None:
        if not self.cfg.get("enrichment.dbpr_enabled", True) or not agent.get("name"):
            return None
        first, last = _name_parts(agent["name"])
        if not last:
            return None
        url = ("https://www.myfloridalicense.com/wl11.asp?mode=2&search=Name&SID=&brd=&typ=N"
               f"&LastName={quote_plus(last)}&FirstName={quote_plus(first)}")
        html = self.fetch(url)
        if not html:
            return None
        hit = self._pick(self._emails_in(html), agent["name"], "dbpr")
        if hit:
            hit.confidence = "medium" if hit.confidence == "high" else "low"
        return hit

    def rung_pattern(self, agent: dict[str, Any]) -> Hit | None:
        name = agent.get("name")
        domain = self.brokerage_domain(agent["brokerage_key"])
        if not name or not domain:
            return None
        known = [(r["name"], r["email"]) for r in self.conn.execute(
            "SELECT name, email FROM agents WHERE brokerage_key=? AND email IS NOT NULL AND email LIKE ? AND id!=?",
            (agent["brokerage_key"], f"%@{domain}", agent["id"])).fetchall()]
        first, last = _name_parts(name)
        if len(known) >= self.min_known:
            pat = detect_pattern(known)
            cands = [apply_pattern(pat, first, last, domain)] if pat else []
            cands = [c for c in cands if c] + [c for c in candidate_patterns(first, last, domain) if c not in cands]
        else:
            cands = candidate_patterns(first, last, domain)[:3]
        mx = self.mx(domain)
        if not mx:
            return None
        for i, cand in enumerate(cands):
            ok = self.rcpt(mx[0], cand)
            if ok is True:
                return Hit(cand, "medium" if (i == 0 and len(known) >= self.min_known) else "low", "pattern+smtp")
            if ok is None and i == 0 and len(known) >= self.min_known:
                # catch-all / greylisted server: pattern agreement is the best evidence we have
                return Hit(cand, "low", "pattern+mx")
        return None

    def enrich_agent(self, agent: dict[str, Any]) -> Hit | None:
        for rung in (self.rung_payload, self.rung_brokerage_site, self.rung_portals, self.rung_dbpr, self.rung_pattern):
            try:
                hit = rung(agent)
            except Exception as e:  # noqa: BLE001
                log.warning("enrich %s rung %s failed: %s", agent.get("name"), rung.__name__, e)
                hit = None
            if hit:
                return hit
        return None

    def run(self, ratings: tuple[str, ...] = ("HOT", "WARM"), max_attempts: int = 3, limit: int | None = None) -> dict[str, int]:
        run_id = db.start_run(self.conn, "enrich", today_et())
        rows = self.conn.execute(
            "SELECT DISTINCT a.* FROM agents a JOIN properties p ON p.agent_id=a.id JOIN valuations v ON v.property_id=p.id "
            f"WHERE v.rating IN ({','.join('?' * len(ratings))}) AND a.email IS NULL AND a.enrich_attempts < ? ORDER BY a.id",
            (*ratings, max_attempts)).fetchall()
        found, tried = 0, 0
        for r in rows:
            agent = db.row_to_dict(r)
            hit = self.enrich_agent(agent)
            tried += 1
            if hit:
                found += 1
                self.conn.execute("UPDATE agents SET email=?, email_confidence=?, email_source=?, enriched_at=?, enrich_attempts=enrich_attempts+1, updated_at=? WHERE id=?",
                                  (hit.email.lower(), hit.confidence, hit.source, db.iso(), db.iso(), agent["id"]))
            else:
                self.conn.execute("UPDATE agents SET enriched_at=?, enrich_attempts=enrich_attempts+1, updated_at=? WHERE id=?",
                                  (db.iso(), db.iso(), agent["id"]))
            if limit and tried >= limit:
                break
        counts = {"agents_tried": tried, "emails_found": found}
        db.finish_run(self.conn, run_id, "ok", counts)
        log.info("enrich: %s", counts)
        return counts
