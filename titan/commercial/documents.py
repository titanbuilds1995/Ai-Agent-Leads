"""Documents behind a listing: find OM / brochure / flyer / rent roll / T-12 links, download the public ones to
data/listings/<listing_id>/, never fill a form or NDA (gated ones are recorded as such), and read NOI / gross / expenses /
occupancy / tenants / lease terms out of the PDFs. Feeds the EVIDENCE GRADE (scoring.evidence_grade)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import requests

from ..util import log
from . import extract as x

DOC_KINDS = [   # (kind, pattern on the link text / file name)
    ("t12", r"t[-_ ]?12|trailing[-_ ]?(?:12|twelve)|ttm|operating[_ ]statement|income[_ ]statement"),
    ("pnl", r"p\s*&\s*l|(?<![a-z])p[_ ]?l(?![a-z])|(?<![a-z])pnl(?![a-z])|profit[_ ](?:and|&)[_ ]loss|financial(?:s|[_ ]summary|[_ ]statement)|actuals"),
    ("rent_roll", r"rent[-_ ]?roll"),
    ("lease_abstract", r"lease[_ ](?:abstract|summary)|abstract[_ ]of[_ ]lease"),
    ("om", r"(?<![a-z])om(?![a-z])|offering[_ ]memorandum|offering[_ ]memo|investment[_ ](?:memorandum|package|summary)|(?<![a-z])cim(?![a-z])"),
    ("brochure", r"brochure|flyer|marketing[_ ](?:package|flyer)|sale[_ ]flyer|(?<![a-z])package(?![a-z])|property[_ ](?:summary|overview)"),
]
_KIND_RE = [(k, re.compile(p, re.I)) for k, p in DOC_KINDS]
PDF_URL_RE = re.compile(r"https?://[^\s\"'<>)]+?\.pdf(?:\?[^\s\"'<>)]*)?", re.I)
GATE_RE = re.compile(r"(?:sign|execute|complete|submit)[^.\n]{0,40}(?:nda|confidentiality|\bca\b|form|register)|register to (?:view|download)|"
                     r"login required|request (?:the )?(?:om|offering memorandum|package)", re.I)
REQUIRED_DOCS = ("T-12", "rent roll", "OM", "lease abstract")
KIND_LABEL = {"t12": "T-12", "pnl": "T-12", "rent_roll": "rent roll", "om": "OM", "brochure": "OM", "lease_abstract": "lease abstract"}

TENANT_LINE_RE = re.compile(r"^\s*([A-Z][A-Za-z0-9&'.,\- ]{2,40}?)\s{2,}\$?\d", re.M)
LEASE_EXP_RE = re.compile(r"(?:expir(?:es|ation)|lease end|end date)[^0-9\n]{0,20}(\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}/\d{4}|20\d\d)", re.I)


def classify(name: str) -> str:
    for kind, rx in _KIND_RE:
        if rx.search(name or ""):
            return kind
    return "other"


def find_links(L: dict[str, Any]) -> list[dict[str, Any]]:
    """Document candidates from whatever the scraper gave us: explicit attachment lists, plus any .pdf URL in the raw text."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw = L.get("raw") or {}
    for key in ("attachments", "documents", "brochures", "brochure", "files", "pdfs", "marketingMaterials", "flyers"):
        v = raw.get(key)
        items = v if isinstance(v, list) else ([v] if v else [])
        for it in items:
            if isinstance(it, str):
                url, name = it, it.rsplit("/", 1)[-1]
            elif isinstance(it, dict):
                url = it.get("url") or it.get("href") or it.get("link") or it.get("src") or ""
                name = it.get("name") or it.get("title") or it.get("label") or url.rsplit("/", 1)[-1]
            else:
                continue
            if url and url not in seen:
                seen.add(url)
                out.append({"name": name, "url": url, "kind": classify(f"{name} {url}")})
    blob = " ".join(str(v) for v in raw.values() if isinstance(v, (str, list, dict)))
    for m in PDF_URL_RE.finditer(blob):
        url = m.group(0)
        if url not in seen:
            seen.add(url)
            out.append({"name": url.rsplit("/", 1)[-1], "url": url, "kind": classify(url)})
    return out


def gated_mentions(L: dict[str, Any]) -> list[dict[str, Any]]:
    """Documents the text says exist but only behind a form / NDA / request. Recorded, never requested by the engine."""
    text = L.get("text") or ""
    out = []
    for kind, rx in _KIND_RE:
        if kind in ("brochure",):
            continue
        m = rx.search(text)
        if m and GATE_RE.search(text[max(0, m.start() - 160): m.end() + 160]):
            out.append({"name": m.group(0), "url": None, "kind": kind, "gated": True})
    return out


def download(doc: dict[str, Any], folder: Path, max_mb: float = 25, session: requests.Session | None = None, timeout: int = 30) -> dict[str, Any]:
    """Fetch one public document. A login/form page (HTML back, 401/403, redirect to a form) is recorded as gated."""
    s = session or requests.Session()
    doc = dict(doc)
    try:
        r = s.get(doc["url"], timeout=timeout, stream=True, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
    except requests.RequestException as e:
        doc.update(downloaded=False, error=f"{type(e).__name__}")
        return doc
    ctype = (r.headers.get("Content-Type") or "").lower()
    if r.status_code in (401, 403) or "text/html" in ctype:
        doc.update(downloaded=False, gated=True, error=f"HTTP {r.status_code} / {ctype.split(';')[0]}")
        return doc
    if r.status_code >= 400:
        doc.update(downloaded=False, error=f"HTTP {r.status_code}")
        return doc
    folder.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", doc.get("name") or "document.pdf")[:120]
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    path = folder / name
    size = 0
    with open(path, "wb") as fh:
        for chunk in r.iter_content(65536):
            size += len(chunk)
            if size > max_mb * 1_000_000:
                fh.close()
                path.unlink(missing_ok=True)
                doc.update(downloaded=False, error=f"over {max_mb} MB")
                return doc
            fh.write(chunk)
    doc.update(downloaded=True, path=str(path), bytes=size)
    return doc


SHEET_EXT = (".xlsx", ".xlsm", ".xls", ".csv")
READABLE_EXT = (".pdf", ".txt", ".md", ".docx") + SHEET_EXT


def _cell(c: Any) -> str:
    """A spreadsheet cell as text. Whole numbers of 1,000+ are written as dollars ($630,000) so the NOI / gross / expense readers,
    which look for a dollar sign, read a bare financial statement the way they read an OM."""
    if isinstance(c, bool):
        return str(c)
    if isinstance(c, (int, float)):
        if abs(c) >= 1000 and float(c).is_integer():
            return f"${int(c):,}"
        if float(c).is_integer():
            return str(int(c))
        return f"{c:.4g}" if abs(c) < 1 else f"{c:,.2f}"
    return str(c).strip()


def sheet_text(path: Path, max_chars: int = 40_000) -> str:
    """A spreadsheet as plain text (one line per row, cells joined by ' | '). Brokers send income statements and rent rolls as
    Excel files (Curt Ware, 2026-11-09); before this they were filed and never read."""
    try:
        if path.suffix.lower() == ".csv":
            return path.read_text(errors="ignore")[:max_chars]
        import openpyxl
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        lines = []
        for ws in wb.worksheets[:8]:
            lines.append(f"## {ws.title}")
            for row in ws.iter_rows(values_only=True):
                cells = [_cell(c) for c in row if c not in (None, "")]
                if cells:
                    lines.append(" | ".join(cells))
            if sum(len(l) for l in lines) > max_chars:
                break
        return "\n".join(lines)[:max_chars]
    except Exception as e:  # noqa: BLE001
        log.warning("sheet text failed for %s: %s", path, e)
        return ""


def doc_text(path: Path) -> str:
    """Whatever text a filed document holds: PDF, spreadsheet, Word, or plain text."""
    suf = path.suffix.lower()
    if suf in (".txt", ".md"):
        return path.read_text(errors="ignore")
    if suf in SHEET_EXT:
        return sheet_text(path)
    if suf == ".docx":
        try:
            import docx  # python-docx
            return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
        except Exception as e:  # noqa: BLE001
            log.warning("docx text failed for %s: %s", path, e)
            return ""
    return pdf_text(path)


def pdf_text(path: Path, max_pages: int = 60) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages[:max_pages])
    except Exception:  # noqa: BLE001 — fall back to pypdf
        try:
            from pypdf import PdfReader
            rd = PdfReader(str(path))
            return "\n".join((pg.extract_text() or "") for pg in rd.pages[:max_pages])
        except Exception as e:  # noqa: BLE001
            log.warning("pdf text failed for %s: %s", path, e)
            return ""


def facts_from_text(text: str) -> dict[str, Any]:
    """NOI, gross, expenses, occupancy, tenant names, lease type, term and expirations from a document's text."""
    tenants = []
    for m in TENANT_LINE_RE.finditer(text):
        name = m.group(1).strip()
        if name.lower() not in ("total", "totals", "vacant", "tenant", "suite", "unit", "address") and name not in tenants:
            tenants.append(name)
    return {
        "noi": x.noi(text), "gross": x.gross_income(text), "expenses": x.expenses(text), "occupancy": x.occupancy(text),
        "lease_type": x.lease_type(text), "remaining_term_years": x.remaining_term_years(text),
        "tenants": tenants[:40], "expirations": sorted({m.group(1) for m in LEASE_EXP_RE.finditer(text)})[:40],
        "proforma": x.is_proforma(text), "trailing": x.mentions_trailing(text), "credit_tenants": x.credit_tenants(text),
    }


def local_files(folder: Path) -> list[dict[str, Any]]:
    """Documents someone dropped into data/listings/<id>/ by hand: OM PDFs the broker sent, or the LoopNet property sheet
    saved as text from Rodrigo's own browser (Claude in Chrome on the Mac). Read like downloaded ones."""
    if not folder.exists():
        return []
    out = []
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() in READABLE_EXT and f.is_file():
            kind = classify(f.name)
            if kind == "other" and f.suffix.lower() in (".txt", ".md"):
                kind = "sheet"
            elif kind == "other" and f.suffix.lower() in SHEET_EXT:
                kind = "pnl"        # an unnamed spreadsheet from a broker is the financials (income statement / rent roll)
            out.append({"name": f.name, "url": None, "kind": kind, "downloaded": True, "path": str(f), "local": True})
    return out


def doc_facts(L: dict[str, Any], base_dir: Path, enabled: bool = True, max_mb: float = 25, session=None) -> dict[str, Any]:
    """Find, download and read every document behind one listing. Returns the fields scoring.evidence_grade reads."""
    folder = base_dir / str(L.get("listing_id") or "unknown")
    local = local_files(folder)
    docs = local + [d for d in find_links(L) + gated_mentions(L) if not any(x["name"] == d.get("name") for x in local)]
    merged: dict[str, Any] = {}
    for d in docs:
        if not d.get("local"):
            if d.get("gated") or not enabled or not d.get("url"):
                d.setdefault("downloaded", False)
                d.setdefault("gated", not d.get("url"))
                continue
            d.update(download(d, folder, max_mb=max_mb, session=session))
        if d.get("downloaded"):
            path = Path(d["path"])
            facts = facts_from_text(doc_text(path))
            d["facts"] = {k: v for k, v in facts.items() if v not in (None, [], False)}
            if d.get("kind") == "sheet":
                d["facts"]["_text"] = (path.read_text(errors="ignore") if path.suffix.lower() in (".txt", ".md") else "")[:20000]
            for k, v in facts.items():
                if v not in (None, [], False) and merged.get(k) in (None, [], False):
                    merged[k] = v
    missing = [lab for lab in REQUIRED_DOCS if not any(KIND_LABEL.get(d.get("kind")) == lab and d.get("downloaded") for d in docs)]
    return {
        "documents": docs, "doc_noi": merged.get("noi"), "doc_gross": merged.get("gross"), "doc_expenses": merged.get("expenses"),
        "doc_occupancy": merged.get("occupancy"), "doc_tenants": merged.get("tenants") or [], "doc_expirations": merged.get("expirations") or [],
        "doc_lease_type": merged.get("lease_type"), "doc_term_years": merged.get("remaining_term_years"),
        "docs_missing": missing, "docs_gated": [d.get("name") for d in docs if d.get("gated")],
    }
