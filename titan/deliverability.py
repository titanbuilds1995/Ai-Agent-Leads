"""Why our emails land in spam, and the guards that keep them out (Rodrigo, 2026-11-16: "a lot of emails that I've sent out to
multiple people have gone to spam … figure out why and build a solution").

What the headers of his own mailbox showed on 2026-11-16: ~200 identical HTML flyers ("Off Market Deal Hialeah", 140 KB each, an
inline picture, "Reply STOP to be removed", a List-Unsubscribe header) went out from rodrigo@ on Nov 4 and Nov 6 through a script on
the Mac (smtp.gmail.com), one every two or three minutes, to a scraped owner list full of dead aol / bellsouth / cox / worldnet
addresses: at least 10 hard bounces ("554 mailbox disabled", "552", "domain couldn't be found"). On Nov 9 the 430 KB flyer went out
again by hand, five times in two hours. Gmail, Yahoo and Outlook read exactly that as bulk marketing from a domain with no bulk
history: identical content, a template, a picture, an unsubscribe line, a high bounce rate, a burst from one mailbox. Charles's mail
is plain text, one agent at a time, personal, inside the send window, so it keeps landing; but both mailboxes share ONE domain
reputation, so the blasts can drag Charles down too.

Four guards live here:
  1. `bulk_markers()`      — what makes a message read as bulk (flyer HTML, inline image, STOP / unsubscribe lines, list headers,
                             a price + "opportunity" subject…). `GmailClient.send` refuses such a message to an outside address
                             (`BulkContent`), nothing overrides it: Charles never sends a flyer.
  2. `blast_report()`      — reads the mailbox: sends per day, identical subjects to many people, bounces and the bounce rate,
                             with a verdict in plain words. `titan.cli deliverability` (Mac, Charles's mailbox).
  3. `dns_report()`        — SPF / DKIM / DMARC of the domain, with the fix for each (Mac; the cloud cannot resolve DNS).
  4. `bounce_breaker()`    — the engine's circuit breaker: when the mailbox bounced more than `max_bounce_pct` of its last 7 days
                             of sends (and sent at least `min_sends`), approvals STOP sending and post ONE Needs Rodrigo card a day.
`verify_list()` cleans a list before Rodrigo's own sends (syntax, duplicates, role mailboxes, the domain's MX, optional SMTP check
through enrich's free primitives): the dead addresses are what killed the reputation.
"""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .util import log, now_utc

# --- 1. bulk markers ---------------------------------------------------------------------------------------------------------

BULK_BODY_RE = re.compile(
    r"\breply\s+stop\b|\bto\s+be\s+removed\b|\bunsubscribe\b|\bopt[\s-]?out\b|\byou\s+(are\s+)?receiv(ed|ing)\s+this\s+(email\s+|message\s+)?because\b|"
    r"\bthis\s+is\s+not\s+spam\b|\bview\s+(this\s+email\s+)?in\s+(your\s+)?browser\b|\bclick\s+here\b|\blimited\s+time\b|\bact\s+now\b",
    re.I)
BULK_SUBJECT_RE = re.compile(r"\b(off[\s-]?market|investment|wholesale)\s+(deal|opportunity|opportunities)\b|\$\s?\d[\d,]{2,}|\b\d+\s+rental\s+units\b|"
                             r"\bfree\b|!{2,}|\bURGENT\b", re.I)
FLYER_HTML_RE = re.compile(r"<table[^>]*(role=.presentation|width=.?6\d\d)|background(-color)?:\s*#|<img\b|font-family:", re.I)
LIST_HEADERS = ("list-unsubscribe", "list-id", "precedence", "x-mailer", "x-campaign", "x-mailgun", "x-sg-eid")
MAX_HTML_BYTES = 40_000          # a designed flyer is 100 KB+; a plain reply with a little markup is a few KB


class BulkContent(Exception):
    """Raised by GmailClient.send: this email reads as bulk marketing and would hurt the domain. Nothing overrides it."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def bulk_markers(subject: str, body: str, html: str | None = None, headers: dict[str, str] | None = None,
                 attachments: list[dict[str, Any]] | None = None) -> list[str]:
    """Everything in this message that makes a mailbox provider file it as bulk / marketing. Empty list = reads as a personal email."""
    out: list[str] = []
    subj = subject or ""
    text = body or ""
    if BULK_SUBJECT_RE.search(subj):
        out.append(f"the subject reads as an ad: {subj.strip()[:80]!r}")
    m = BULK_BODY_RE.search(text) or (BULK_BODY_RE.search(re.sub(r"<[^>]+>", " ", html)) if html else None)
    if m:
        out.append(f"marketing language a person never writes one-to-one: {m.group(0).strip()!r}")
    if html:
        if len(html.encode("utf-8", "ignore")) > MAX_HTML_BYTES:
            out.append(f"a designed HTML flyer ({len(html) // 1000} KB of markup)")
        elif FLYER_HTML_RE.search(html):
            out.append("a designed HTML layout (tables, colours, pictures)")
    for a in attachments or []:
        mime = (a.get("mime") or "").lower()
        if mime.startswith("image/") and (a.get("size") or len(a.get("bytes") or b"")) > 60_000:
            out.append(f"a big picture attached ({a.get('filename') or 'image'})")
    for h in headers or {}:
        if h.lower() in LIST_HEADERS:
            out.append(f"a mailing-list header ({h}) that tells the receiver 'this is a campaign'")
    caps = re.findall(r"\b[A-Z]{5,}\b", text)
    if len(caps) >= 4:
        out.append("shouting in capitals")
    return out


# --- 2. the mailbox report -------------------------------------------------------------------------------------------------

BOUNCE_ADDR_RE = re.compile(r"(?:wasn'?t|was not|couldn'?t be|could not be) delivered to\s+<?([\w.+-]+@[\w.-]+)|Final-Recipient:\s*rfc822;\s*([\w.+-]+@[\w.-]+)|"
                            r"X-Failed-Recipients:\s*([\w.+-]+@[\w.-]+)", re.I)
BOUNCE_CODE_RE = re.compile(r"\b(5\d\d)[\s-]+(\d[\d.]*\s+)?([^\n]{0,120})")
BLAST_MIN = 5          # the same subject to this many different people in one day = a blast


def _addr(s: str) -> str:
    m = re.search(r"[\w.+-]+@[\w.-]+", s or "")
    return m.group(0).lower() if m else (s or "").strip().lower()


def blast_report(gmail, days: int = 30, now: datetime | None = None, max_messages: int = 1500) -> dict[str, Any]:
    """Sends, blasts and bounces of the connected mailbox over the last `days`, read through GmailClient (search_refs + message)."""
    now = now or now_utc()
    since = (now - timedelta(days=days)).strftime("%Y/%m/%d")
    own = (getattr(gmail, "sender", "") or "").rpartition("@")[2].lower()
    sent_refs = gmail.search_refs(f"in:sent after:{since}", max_results=max_messages)
    sent: list[dict[str, Any]] = []
    for r in sent_refs:
        try:
            m = gmail.message(r["id"])
        except Exception as e:  # noqa: BLE001
            log.warning("deliverability: could not read sent %s: %s", r["id"], e)
            continue
        to = [_addr(x) for x in re.split(r"[;,]", m.get("to") or "") if "@" in x]
        if not to or all(t.endswith("@" + own) for t in to):
            continue                                        # reports and forwards to ourselves are not outreach
        day = datetime.fromtimestamp(int(m.get("internal_ts") or 0), tz=timezone.utc).strftime("%Y-%m-%d")
        subj = re.sub(r"^\s*((re|fw|fwd)\s*:\s*)+", "", m.get("subject") or "", flags=re.I).strip().lower()
        sent.append({"day": day, "subject": subj, "to": to, "size": len(m.get("body") or ""), "ts": int(m.get("internal_ts") or 0)})
    per_day = Counter(s["day"] for s in sent)
    clusters: dict[tuple[str, str], set[str]] = defaultdict(set)
    for s in sent:
        for t in s["to"]:
            clusters[(s["day"], s["subject"])].add(t)
    blasts = [{"day": d, "subject": subj, "recipients": len(rcpts)} for (d, subj), rcpts in clusters.items() if len(rcpts) >= BLAST_MIN]
    blasts.sort(key=lambda b: (-b["recipients"], b["day"]))
    bounce_refs = gmail.search_refs(f"(from:mailer-daemon OR from:postmaster OR subject:\"Delivery Status Notification\" OR subject:undeliverable) after:{since}",
                                    max_results=max_messages)
    bounces: list[dict[str, str]] = []
    for r in bounce_refs:
        try:
            m = gmail.message(r["id"])
        except Exception as e:  # noqa: BLE001
            log.warning("deliverability: could not read bounce %s: %s", r["id"], e)
            continue
        body = m.get("body") or ""
        am = BOUNCE_ADDR_RE.search(body)
        dead = next((g for g in (am.groups() if am else ()) if g), "")
        cm = BOUNCE_CODE_RE.search(body)
        bounces.append({"address": dead.lower(), "code": cm.group(1) if cm else "", "reason": (cm.group(3) if cm else "")[:100].strip(),
                        "day": datetime.fromtimestamp(int(m.get("internal_ts") or 0), tz=timezone.utc).strftime("%Y-%m-%d")})
    n_sent, n_bounced = len(sent), len(bounces)
    rate = (100.0 * n_bounced / n_sent) if n_sent else 0.0
    rep = {"days": days, "sent": n_sent, "sent_per_day": dict(sorted(per_day.items())), "busiest_day": max(per_day.items(), key=lambda kv: kv[1]) if per_day else None,
           "blasts": blasts, "bounces": bounces, "bounce_pct": round(rate, 1),
           "dead_addresses": sorted({b["address"] for b in bounces if b["address"]})}
    rep["verdict"] = _verdict(rep)
    return rep


def _verdict(rep: dict[str, Any]) -> list[str]:
    out = []
    if rep["bounce_pct"] >= 5 and rep["sent"] >= 20:
        out.append(f"{rep['bounce_pct']}% of what this mailbox sent bounced ({len(rep['bounces'])} of {rep['sent']}). Above 2% mailbox providers start "
                   "filing the domain's mail as spam; above 5% they throttle it. The list has dead addresses: clean it before the next send.")
    elif rep["bounce_pct"] >= 2 and rep["sent"] >= 20:
        out.append(f"{rep['bounce_pct']}% bounced: on the edge. Verify addresses before sending (make verify-list).")
    for b in rep["blasts"][:5]:
        out.append(f"{b['day']}: the same email ({b['subject'][:60]!r}) went to {b['recipients']} different people. Identical content to many "
                   "strangers from one mailbox is the definition of bulk mail; Gmail scores the whole domain on it.")
    if rep["busiest_day"] and rep["busiest_day"][1] > 150:
        out.append(f"{rep['busiest_day'][1]} emails on {rep['busiest_day'][0]} from one mailbox: Google Workspace flags a user above ~100 new "
                   "recipients a day as bulk and can suspend sending for 24 h.")
    if not out:
        out.append("Nothing in the mailbox's own history explains spam placement: volume, bounces and repetition look like a person's mail.")
    return out


def format_report(rep: dict[str, Any], dns: dict[str, Any] | None = None) -> str:
    lines = [f"DELIVERABILITY · last {rep['days']} days", f"  sent to outside addresses: {rep['sent']}  ·  bounced: {len(rep['bounces'])} ({rep['bounce_pct']}%)"]
    if rep["busiest_day"]:
        lines.append(f"  busiest day: {rep['busiest_day'][0]} with {rep['busiest_day'][1]} emails")
    if rep["blasts"]:
        lines.append("  blasts (same subject, many people, one day):")
        lines += [f"    {b['day']}  {b['recipients']:>4} people  {b['subject'][:70]}" for b in rep["blasts"][:10]]
    if rep["dead_addresses"]:
        lines.append(f"  dead addresses ({len(rep['dead_addresses'])}): " + ", ".join(rep["dead_addresses"][:12]) + (" …" if len(rep["dead_addresses"]) > 12 else ""))
    lines.append("  verdict:")
    lines += [f"    - {v}" for v in rep["verdict"]]
    if dns:
        lines.append(f"DNS · {dns['domain']}")
        for k in ("spf", "dkim", "dmarc"):
            r = dns[k]
            lines.append(f"  {k.upper():5} {'OK  ' if r['ok'] else 'FIX '} {r['note']}")
            if not r["ok"] and r.get("fix"):
                lines.append(f"        fix: {r['fix']}")
    return "\n".join(lines)


# --- 3. DNS: SPF / DKIM / DMARC ------------------------------------------------------------------------------------------------

def _txt(name: str, resolver: Callable[[str], list[str]] | None = None) -> list[str]:
    if resolver:
        return resolver(name)
    try:
        import dns.resolver
        return ["".join(s.decode() if isinstance(s, bytes) else str(s) for s in r.strings) for r in dns.resolver.resolve(name, "TXT", lifetime=8)]
    except Exception:  # noqa: BLE001 — NXDOMAIN, timeout, no dnspython: "no record"
        return []


def dns_report(domain: str, resolver: Callable[[str], list[str]] | None = None, dkim_selectors: tuple[str, ...] = ("google", "selector1", "selector2", "default")) -> dict[str, Any]:
    """SPF / DKIM / DMARC of our domain in plain words. `resolver(name) -> [txt strings]` is injectable for tests and for the cloud."""
    spf = [t for t in _txt(domain, resolver) if t.lower().startswith("v=spf1")]
    spf_r: dict[str, Any] = {"ok": False, "record": spf[0] if spf else None}
    if not spf:
        spf_r.update(note="no SPF record: receivers cannot tell Google is allowed to send for us",
                     fix=f'add a TXT record on {domain}: "v=spf1 include:_spf.google.com ~all"')
    elif len(spf) > 1:
        spf_r.update(note="more than one SPF record: that is an SPF failure (permerror) at every receiver", fix="merge them into ONE v=spf1 record")
    elif "_spf.google.com" not in spf[0]:
        spf_r.update(note="SPF does not include Google's servers (_spf.google.com)", fix=f"add include:_spf.google.com to the record: {spf[0]}")
    elif "+all" in spf[0]:
        spf_r.update(note="SPF ends in +all (anyone may send as us): receivers ignore it", fix="end the record with ~all or -all")
    else:
        spf_r.update(ok=True, note=spf[0])
    dkim_r: dict[str, Any] = {"ok": False, "selectors": {}}
    for sel in dkim_selectors:
        rec = _txt(f"{sel}._domainkey.{domain}", resolver)
        if any("p=" in r for r in rec):
            dkim_r["selectors"][sel] = True
    if dkim_r["selectors"]:
        dkim_r.update(ok=True, note=f"DKIM key published (selector {', '.join(dkim_r['selectors'])})")
    else:
        dkim_r.update(note="no DKIM key found under the usual selectors (google, selector1, selector2, default)",
                      fix="Google Admin → Apps → Google Workspace → Gmail → Authenticate email → Generate new record (2048-bit) → add the TXT → Start authentication")
    dm = [t for t in _txt(f"_dmarc.{domain}", resolver) if t.lower().startswith("v=dmarc1")]
    dmarc_r: dict[str, Any] = {"ok": False, "record": dm[0] if dm else None}
    if not dm:
        dmarc_r.update(note="no DMARC record: since Feb 2024 Gmail and Yahoo file mail from bulk senders without DMARC as spam, and every receiver scores it down",
                       fix=f'add a TXT record on _dmarc.{domain}: "v=DMARC1; p=quarantine; rua=mailto:dmarc@{domain}; adkim=r; aspf=r; pct=100"')
    else:
        p = re.search(r"\bp=(\w+)", dm[0], re.I)
        pol = (p.group(1).lower() if p else "none")
        if pol == "none":
            dmarc_r.update(ok=True, note=f"{dm[0]} (p=none only monitors; move to p=quarantine once SPF and DKIM pass on everything we send)")
        else:
            dmarc_r.update(ok=True, note=dm[0])
    return {"domain": domain, "spf": spf_r, "dkim": dkim_r, "dmarc": dmarc_r}


# --- 4. the circuit breaker --------------------------------------------------------------------------------------------------

def bounce_breaker(gmail, cfg: Any, now: datetime | None = None) -> str | None:
    """Reason to stop sending (bounce rate over the last 7 days above the limit), or None. Cheap: two Gmail searches, no bodies."""
    d = (cfg.get("outreach.deliverability", {}) or {}) if hasattr(cfg, "get") else {}
    if not d.get("breaker", True):
        return None
    days = int(d.get("window_days", 7))
    min_sends = int(d.get("min_sends", 20))
    limit = float(d.get("max_bounce_pct", 5))
    since = ((now or now_utc()) - timedelta(days=days)).strftime("%Y/%m/%d")
    try:
        sent = len(gmail.search_refs(f"in:sent after:{since}", max_results=2000))
        bounced = len(gmail.search_refs(f"(from:mailer-daemon OR from:postmaster OR subject:\"Delivery Status Notification\") after:{since}", max_results=2000))
    except Exception as e:  # noqa: BLE001 — Gmail trouble is not a reason to stop
        log.warning("deliverability breaker: could not count: %s", e)
        return None
    if sent < min_sends or not sent:
        return None
    pct = 100.0 * bounced / sent
    if pct > limit:
        return (f"**STOPPED sending: {bounced} of the last {sent} emails from this mailbox bounced ({pct:.0f}%, limit {limit:.0f}%).** A bounce rate "
                f"this high is what gets a domain filed as spam, so nothing goes out until it falls back under the limit (or you raise "
                f"outreach.deliverability.max_bounce_pct). Run `make deliverability` on the Mac to see which addresses are dead.")
    return None


# --- the list cleaner ----------------------------------------------------------------------------------------------------------

ROLE_RE = re.compile(r"^(info|sales|admin|office|support|contact|hello|noreply|no-reply|postmaster|abuse|billing|webmaster|marketing|team|help|service|careers|jobs|hr)@", re.I)
DEAD_DOMAINS = {"worldnet.att.net", "peoplepc.com", "netzero.net", "juno.com", "prodigy.net", "mindspring.com", "earthlink.net", "compuserve.com", "toast.net"}
SYNTAX_RE = re.compile(r"^[\w.+-]+@([\w-]+\.)+[a-z]{2,}$", re.I)


def verify_list(addresses: list[str], mx: Callable[[str], list[str]] | None = None, rcpt: Callable[[str, str], bool | None] | None = None,
                dead_known: set[str] | None = None) -> dict[str, list[dict[str, str]]]:
    """Split a list into clean / dead / unknown before anyone sends to it. mx(domain) and rcpt(mx_host, email) are enrich's free
    primitives (DNS MX lookup, SMTP RCPT probe); without them only syntax, duplicates, role mailboxes and known-dead domains are checked."""
    seen: set[str] = set()
    out: dict[str, list[dict[str, str]]] = {"clean": [], "dead": [], "unknown": []}
    mx_cache: dict[str, list[str]] = {}
    for raw in addresses:
        a = _addr(raw)
        if not a or a in seen:
            continue
        seen.add(a)
        dom = a.rpartition("@")[2]
        if not SYNTAX_RE.match(a):
            out["dead"].append({"email": a, "why": "not a valid address"})
            continue
        if a in (dead_known or set()):
            out["dead"].append({"email": a, "why": "bounced before"})
            continue
        if dom in DEAD_DOMAINS:
            out["dead"].append({"email": a, "why": f"{dom} no longer delivers mail"})
            continue
        if ROLE_RE.match(a):
            out["unknown"].append({"email": a, "why": "a role mailbox (info@, office@…): a person may never read it"})
            continue
        if mx is not None:
            if dom not in mx_cache:
                mx_cache[dom] = mx(dom) or []
            if not mx_cache[dom]:
                out["dead"].append({"email": a, "why": "the domain has no mail server"})
                continue
            if rcpt is not None:
                ok = rcpt(mx_cache[dom][0], a)
                if ok is False:
                    out["dead"].append({"email": a, "why": "the mail server says the mailbox does not exist"})
                    continue
                if ok is None:
                    out["unknown"].append({"email": a, "why": "the mail server would not say (catch-all or greylisting)"})
                    continue
        out["clean"].append({"email": a, "why": ""})
    return out


def verify_file(path: str | Path, out_dir: str | Path | None = None, mx=None, rcpt=None, dead_known: set[str] | None = None) -> dict[str, Any]:
    """Read every address out of a CSV / text file, verify, write <name>.clean.csv and <name>.dead.csv next to it."""
    p = Path(path)
    text = p.read_text(errors="replace")
    addrs = re.findall(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", text, re.I)
    res = verify_list(addrs, mx=mx, rcpt=rcpt, dead_known=dead_known)
    od = Path(out_dir) if out_dir else p.parent
    od.mkdir(parents=True, exist_ok=True)
    files = {}
    for k in ("clean", "dead", "unknown"):
        fp = od / f"{p.stem}.{k}.csv"
        with fp.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["email", "why"])
            w.writerows([[r["email"], r["why"]] for r in res[k]])
        files[k] = str(fp)
    return {"counts": {k: len(v) for k, v in res.items()}, "files": files, "total": len(addrs)}
