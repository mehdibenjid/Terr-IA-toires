#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scraper + PDF analyzer for French territorial documents (PCAET, PLU, SCOT, SRADET)
- BFS crawl (same-domain) up to a configurable depth
- PDF detection and type classification
- Async downloads
- Text extraction via PyMuPDF (if available) else pdfminer.six
- Stakeholder keyword scoring
- CSV output + local PDF archive

Quick start (demo Toulouse):
    python scraper_territoires.py --demo --year 2025 --collect-unknown --out results/occitanie.csv

With your own CSV:
    python scraper_territoires.py --input targets.csv --region Occitanie --year 2025 --collect-unknown --out results/occitanie.csv
"""

import asyncio
import aiohttp
import async_timeout
import csv
import hashlib
import os
import re
import sys
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Tuple
from urllib.parse import urljoin, urlparse, unquote
from bs4 import BeautifulSoup
from datetime import datetime
import pandas as pd

# ---------- Text extraction backends ----------
HAS_PYMUPDF = False
try:
    import pymupdf as fitz  # noqa
    HAS_PYMUPDF = True
except Exception:
    HAS_PYMUPDF = False

HAS_PDFMINER = False
try:
    from pdfminer.high_level import extract_text as pdfminer_extract_text  # noqa
    HAS_PDFMINER = True
except Exception:
    HAS_PDFMINER = False

if not (HAS_PYMUPDF or HAS_PDFMINER):
    print("[FATAL] Need at least one PDF backend: install 'pymupdf' or 'pdfminer.six'.")
    sys.exit(1)

# ---------------------------
# CONFIG
# ---------------------------

USER_AGENT = "Mozilla/5.0 (compatible; territorial-scraper/2.2; +https://example.org/oss)"
PDF_EXT = (".pdf",)

DOC_TYPES: Dict[str, List[str]] = {
    "PCAET": [r"\bpcaet\b", r"plan\s*climat"],
    "PLU": [r"\bplu\b", r"plan\s*local\s*d['’]?urbanisme"],
    "SCOT": [r"\bscot\b", r"sch[ée]ma\s*de\s*coh[ée]rence"],
    "SRADET": [r"\bsradet\b", r"sch[ée]ma\s*r[ée]gional"],
}

PARTIES_PRENANTES = {
    "collectivites": ["mairie", "commune", "métropole", "région", "département"],
    "autorites": ["préfet", "dreal", "etat", "ministère"],
    "bureaux_etudes": ["bureau d’études", "cabinet", "consultant"],
    "associations": ["association", "ong", "fédération", "collectif"],
}

# Precompile regex for doc types (case-insensitive)
DOC_PATTERNS: Dict[str, List[re.Pattern]] = {
    k: [re.compile(p, re.I) for p in pats] for k, pats in DOC_TYPES.items()
}

# ---------------------------
# DATA MODELS
# ---------------------------

@dataclass
class Target:
    name: str
    type: str
    base_url: str
    insee: Optional[str] = None
    departement: Optional[str] = None
    region: Optional[str] = None

@dataclass
class FoundDoc:
    target_name: str
    target_type: str
    target_domain: str
    doc_type: str
    title: str
    url: str
    discovered_on: str
    parties_score: Dict[str, int]

# ---------------------------
# PDF ANALYZER
# ---------------------------

class PDFAnalyzer:
    @staticmethod
    def extract_text_from_path(pdf_path: str, max_pages: int = 12) -> str:
        """
        Extract text using PyMuPDF if available, else pdfminer.six.
        max_pages only applies to PyMuPDF (for speed).
        """
        if HAS_PYMUPDF:
            text_parts: List[str] = []
            doc = fitz.open(pdf_path)
            try:
                for i, page in enumerate(doc, start=1):
                    if i > max_pages:
                        break
                    text_parts.append(page.get_text())
            finally:
                doc.close()
            return "\n".join(text_parts)
        else:
            return pdfminer_extract_text(pdf_path)

    @staticmethod
    def analyze(pdf_path: str) -> Dict[str, int]:
        scores = {k: 0 for k in PARTIES_PRENANTES}
        try:
            text = PDFAnalyzer.extract_text_from_path(pdf_path).lower()
            for cat, mots in PARTIES_PRENANTES.items():
                for mot in mots:
                    scores[cat] += len(re.findall(re.escape(mot.lower()), text))
        except Exception as e:
            print(f"[ERREUR] extraction {pdf_path}: {e}")
        return scores

# ---------------------------
# SCRAPER
# ---------------------------

class Scraper:
    def __init__(
        self,
        targets: List[Target],
        out_dir: str = "results/pdfs",
        collect_unknown: bool = False,
        max_depth: int = 2,
        max_pages: int = 80,
        insecure_ssl: bool = False,
        only_year: Optional[int] = None,
    ):
        self.targets = targets
        self.results: List[FoundDoc] = []
        self.out_dir = out_dir
        self.collect_unknown = bool(collect_unknown)
        self.max_depth = int(max_depth)
        self.max_pages = int(max_pages)
        self.insecure_ssl = bool(insecure_ssl)
        self.only_year = only_year
        os.makedirs(self.out_dir, exist_ok=True)

    @staticmethod
    def _is_pdf_url(url: str) -> bool:
        path = urlparse(url).path.lower()
        return path.endswith(".pdf")

    @staticmethod
    def _classify_pdf(text_for_match: str) -> Optional[str]:
        for label, patterns in DOC_PATTERNS.items():
            for pat in patterns:
                if pat.search(text_for_match):
                    return label
        return None

    @staticmethod
    def _url_has_year(url: str, year: int) -> bool:
        path = urlparse(url).path
        return str(year) in path

    @staticmethod
    def _target_filename_from_url(url: str) -> str:
        """
        Use the last path segment as filename; decode %xx; sanitize for Windows.
        Fallback to hashed name if empty; ensure .pdf.
        """
        path = urlparse(url).path
        name = unquote(os.path.basename(path)) or ""
        name = re.sub(r'[\\/:*?"<>|]+', "_", name)
        if not name.lower().endswith(".pdf"):
            base = name or hashlib.md5(url.encode()).hexdigest()
            name = f"{base}.pdf"
        return name

    async def crawl_target(self, session: aiohttp.ClientSession, t: Target):
        base = t.base_url.rstrip("/") + "/"
        parsed_base = urlparse(base)
        domain = f"{parsed_base.scheme}://{parsed_base.netloc}"

        seen = set()
        queue: List[Tuple[str, int]] = [(base, 0)]
        pages_scanned = 0

        while queue:
            url, depth = queue.pop(0)
            if url in seen or depth > self.max_depth or pages_scanned >= self.max_pages:
                continue
            seen.add(url)

            html = await self.fetch_html(session, url)
            pages_scanned += 1
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")

            # 1) Extract direct PDF links on this page
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                full = urljoin(url, href)
                if not self._is_pdf_url(full):
                    continue

                # Year filter (keep only URLs whose PATH contains that year)
                if self.only_year is not None and not self._url_has_year(full, self.only_year):
                    continue

                anchor_text = a.get_text(" ") or ""
                text_for_match = f"{anchor_text} {full}"
                doc_type = self._classify_pdf(text_for_match)
                if not doc_type and not self.collect_unknown:
                    continue
                if not doc_type and self.collect_unknown:
                    doc_type = "UNKNOWN"

                path = await self.download_pdf(session, full)
                if path:
                    parties_score = PDFAnalyzer.analyze(path)
                    self.results.append(
                        FoundDoc(
                            target_name=t.name,
                            target_type=t.type,
                            target_domain=parsed_base.netloc,
                            doc_type=doc_type,
                            title=os.path.basename(path),
                            url=full,
                            discovered_on=datetime.utcnow().isoformat(),
                            parties_score=parties_score,
                        )
                    )

            # 2) Enqueue internal links for deeper crawl
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                nxt = urljoin(url, href)
                if not nxt.startswith(domain):
                    continue
                if self._is_pdf_url(nxt):
                    continue
                if nxt.startswith(("mailto:", "tel:")):
                    continue
                if nxt not in seen:
                    queue.append((nxt, depth + 1))

    async def fetch_html(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        try:
            async with async_timeout.timeout(20):
                async with session.get(
                    url,
                    headers={"User-Agent": USER_AGENT},
                    allow_redirects=True,
                ) as resp:
                    if resp.status == 200:
                        return await resp.text(errors="ignore")
                    if resp.status in (403, 404):
                        print(f"[{resp.status}] {url}")
        except Exception as e:
            print(f"[ERREUR HTML] {url}: {e}")
        return None

    async def download_pdf(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        try:
            filename = self._target_filename_from_url(url)   # ORIGINAL NAME
            path = os.path.join(self.out_dir, filename)

            # If same name already exists, avoid accidental overwrite with different file
            if os.path.exists(path):
                return path

            async with async_timeout.timeout(40):
                async with session.get(
                    url,
                    headers={"User-Agent": USER_AGENT},
                    allow_redirects=True,
                ) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        with open(path, "wb") as f:
                            f.write(content)
                        print(f"[OK] PDF: {url}  ->  {filename}")
                        return path
                    else:
                        print(f"[{resp.status}] PDF fail: {url}")
        except Exception as e:
            print(f"[ERREUR DL] {url}: {e}")
        return None

    async def run(self):
        connector = aiohttp.TCPConnector(
            limit=6,
            ssl=False if self.insecure_ssl else None  # None = default verify
        )
        async with aiohttp.ClientSession(connector=connector) as session:
            await asyncio.gather(*[self.crawl_target(session, t) for t in self.targets])
        return self.results

# ---------------------------
# UTILS
# ---------------------------

def read_targets(csv_path: str, departement=None, region=None) -> List[Target]:
    targets: List[Target] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("base_url"):
                continue
            if departement and (row.get("departement") or "").strip() != str(departement):
                continue
            if region and (row.get("region") or "").strip() != str(region):
                continue
            targets.append(
                Target(
                    name=(row.get("name") or "").strip(),
                    type=(row.get("type") or "").strip().lower(),
                    base_url=(row.get("base_url") or "").strip(),
                    insee=(row.get("insee") or "").strip() or None,
                    departement=(row.get("departement") or "").strip() or None,
                    region=(row.get("region") or "").strip() or None,
                )
            )
    return targets

def save_results_csv(out_path: str, docs: List[FoundDoc]):
    df = pd.DataFrame([asdict(d) for d in docs])
    os.makedirs(os.path.dirname(out_path), exist_ok=True
    )
    df.to_csv(out_path, index=False, encoding="utf-8")
    print(f"[DONE] {len(df)} documents saved -> {out_path}")

def demo_targets() -> List[Target]:
    # Pages that actually lead to PDFs around Toulouse
    return [
        Target("Toulouse Métropole – Accueil", "metropole", "https://metropole.toulouse.fr/"),
        Target("Toulouse Métropole – PLUi-H", "metropole", "https://metropole.toulouse.fr/mon-environnement/logement-et-urbanisme/urbanisme/le-plui-h"),
        Target("Toulouse Métropole – PLU", "metropole", "https://metropole.toulouse.fr/mon-environnement/logement-et-urbanisme/urbanisme/plan-local-durbanisme"),
        Target("Toulouse Métropole – PCAET (kiosque)", "metropole", "https://metropole.toulouse.fr/kiosque/pcaet-livre-1"),
        Target("Ville de Toulouse – Délibérations", "ville", "https://deliberations.toulouse.fr/"),
        Target("Toulouse Métropole – Délibérations", "metropole", "https://deliberations.toulouse-metropole.fr/?nomDuSite=2&p=presentation"),
    ]

# ---------------------------
# MAIN
# ---------------------------

def main():
    import argparse

    p = argparse.ArgumentParser(description="Scraper PCAET/PLU/SCOT/SRADET + PDF analysis")
    p.add_argument("--input", help="CSV of targets (columns: name,type,base_url,departement,region)")
    p.add_argument("--out", default="results/results.csv", help="Output CSV path")
    p.add_argument("--departement", help="Filter by departement value")
    p.add_argument("--region", help="Filter by region value")
    p.add_argument("--max-depth", type=int, default=2, help="Crawl depth (default 2)")
    p.add_argument("--max-pages", type=int, default=80, help="Max pages per target (default 80)")
    p.add_argument("--collect-unknown", action="store_true", help="Also collect PDFs with unknown type")
    p.add_argument("--insecure-ssl", action="store_true", help="Disable SSL verification in connector")
    p.add_argument("--demo", action="store_true", help="Use built-in Toulouse targets (no CSV needed)")
    p.add_argument("--year", type=int, help="Only collect PDFs whose URL path contains this year (e.g., 2025)")
    args = p.parse_args()

    if args.demo and args.input:
        print("[WARN] --demo and --input provided; using demo, ignoring input.")

    if args.demo:
        targets = demo_targets()
    else:
        if not args.input:
            print("You must provide --input or use --demo.")
            sys.exit(1)
        targets = read_targets(args.input, departement=args.departement, region=args.region)

    if not targets:
        print("No targets found after filtering.")
        sys.exit(1)

    scraper = Scraper(
        targets,
        out_dir="results/pdfs",
        collect_unknown=args.collect_unknown,
        max_depth=args.max_depth,
        max_pages=args.max_pages,
        insecure_ssl=args.insecure_ssl,
        only_year=args.year,
    )
    docs = asyncio.run(scraper.run())
    save_results_csv(args.out, docs)

if __name__ == "__main__":
    main()
