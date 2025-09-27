#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Semantic PDF analysis for decarbonation / biogas stance (no external LLM needed)

What it does
------------
- Extract text from PDFs (PyMuPDF if available, else pdfminer.six)
- Chunk text and embed with a multilingual sentence-transformer
- Gate out off-topic PDFs via a "decarbonation topic" similarity threshold
- Prototype-based semantic scoring:
    pro     = support for decarbonation/biomethane/bioGNV/networks
    contra  = opposition / moratorium / cancellation / negatives
    neutral = administrative / procedural / no stance
- Favorability = sigmoid( (max_sim(pro) - max(max_sim(contra), 0.5*max_sim(neutral)) - bias) * scale )
- Outputs CSV with per-PDF scores + label + confidence, and JSONL with evidence snippets

Usage
-----
python analyze_semantic.py --pdf-dir results/pdfs --city "Toulouse Métropole" \
    --model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
    --out-csv results/semantic.csv --out-jsonl results/semantic.jsonl

Notes
-----
- Default model is small, multilingual, good enough for French: paraphrase-multilingual-MiniLM-L12-v2
- Runs on CPU by default. GPU only if you configured torch/cuda yourself.
"""

import os
import re
import sys
import json
import math
import glob
import unicodedata
from dataclasses import dataclass, asdict
from typing import List, Dict, Tuple, Optional

import pandas as pd
import numpy as np

# -------- PDF backends --------
HAS_PYMUPDF = False
try:
    import pymupdf as fitz  # type: ignore
    HAS_PYMUPDF = True
except Exception:
    HAS_PYMUPDF = False

HAS_PDFMINER = False
try:
    from pdfminer.high_level import extract_text as pdfminer_extract_text  # type: ignore
    HAS_PDFMINER = True
except Exception:
    HAS_PDFMINER = False

if not (HAS_PYMUPDF or HAS_PDFMINER):
    print("[FATAL] Need at least one PDF backend: install 'pymupdf' or 'pdfminer.six'.")
    sys.exit(1)

# -------- Embedding model --------
from sentence_transformers import SentenceTransformer
import torch

# ------------------ prototypes ------------------
TOPIC_PROTOTYPES_FR = [
    "transition énergétique, décarbonation territoriale, plan climat, PCAET, trajectoire bas-carbone",
    "biogaz, biométhane injecté, méthanisation agricole et territoriale, gaz vert",
    "bioGNV, station GNV, mobilité au gaz renouvelable, flotte de bus au bioGNV",
    "réseau de chaleur urbain bas-carbone, récupération énergétique, valorisation de déchets",
    "EnR locales: solaire, éolien, hydro, géothermie, autoconsommation collective",
    "hydrogène décarboné pour la mobilité ou l'industrie locale",
]

PRO_FR = [
    "la collectivité soutient activement la décarbonation: objectifs chiffrés de réduction des émissions",
    "déploiement de la méthanisation et injection de biométhane dans le réseau, projets pilotes",
    "création ou extension de stations bioGNV pour bus ou collecte des déchets",
    "développement de réseaux de chaleur bas-carbone et raccordements de bâtiments publics",
    "financement, subventions ou appels à projets pour le gaz vert et la valorisation énergétique des déchets",
]

CONTRA_FR = [
    "opposition ou moratoire sur la méthanisation, projets annulés ou refusés pour nuisances",
    "abandon des stations GNV ou absence d'engagement envers le biogaz",
    "priorité donnée au gaz fossile sans trajectoire de réduction, refus de raccordement au réseau de chaleur",
    "inquiétudes non résolues sur odeurs, trafic, risques, menant à l'arrêt de projets",
]

NEUTRAL_FR = [
    "document administratif neutre: calendrier, procédure, avis techniques, pas de décision stratégique",
    "information générale sans engagement opérationnel en faveur ou contre la décarbonation",
]

# ------------------ helpers ------------------
def normalize_ws(text: str) -> str:
    text = unicodedata.normalize("NFC", str(text)).replace("\u00A0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def extract_pdf_text(path: str, max_pages: int = 12) -> str:
    if HAS_PYMUPDF:
        doc = fitz.open(path)  # type: ignore
        parts: List[str] = []
        try:
            for i, page in enumerate(doc, start=1):
                if i > max_pages:
                    break
                parts.append(page.get_text())
        finally:
            doc.close()
        return normalize_ws("\n".join(parts))
    else:
        return normalize_ws(pdfminer_extract_text(path))  # type: ignore

def chunk_text(text: str, target_chars: int = 1000, overlap: int = 150) -> List[str]:
    # split by paragraphs then merge to ~target_chars chunks
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: List[str] = []
    buf = ""
    for p in paras:
        if not buf:
            buf = p
        elif len(buf) + 2 + len(p) <= target_chars:
            buf = buf + "\n\n" + p
        else:
            chunks.append(buf)
            if overlap > 0 and len(buf) > overlap:
                buf = buf[-overlap:] + "\n\n" + p
            else:
                buf = p
    if buf:
        chunks.append(buf)
    return chunks

def cos_sim(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    # returns matrix cosine similarities between a [n,d] and b [m,d]
    a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-12)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-12)
    return np.matmul(a_norm, b_norm.T)

def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))

# ------------------ core analysis ------------------
@dataclass
class DocResult:
    city: str
    file: str
    topic_score: float
    pro_max: float
    contra_max: float
    neutral_max: float
    favorability: float
    label: str
    top_evidence_pro: List[str]
    top_evidence_contra: List[str]

class SemanticAnalyzer:
    def __init__(
        self,
        model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        device: str = "cpu",
        topic_threshold: float = 0.20,
        scale: float = 6.0,
        bias: float = 0.10,
        chunk_chars: int = 1000,
        chunk_overlap: int = 150,
    ):
        self.device = device
        self.model = SentenceTransformer(model_name, device=device)
        self.topic_threshold = topic_threshold
        self.scale = scale
        self.bias = bias
        self.chunk_chars = chunk_chars
        self.chunk_overlap = chunk_overlap

        # Pre-embed prototypes
        self.protos = {
            "topic": TOPIC_PROTOTYPES_FR,
            "pro": PRO_FR,
            "contra": CONTRA_FR,
            "neutral": NEUTRAL_FR,
        }
        self.emb_protos = {
            k: self._embed_texts(v) for k, v in self.protos.items()
        }

    def _embed_texts(self, texts: List[str]) -> np.ndarray:
        embs = self.model.encode(texts, batch_size=16, convert_to_numpy=True, normalize_embeddings=False)
        return embs.astype(np.float32)

    def _embed_chunks(self, chunks: List[str]) -> np.ndarray:
        return self._embed_texts(chunks)

    def analyze_pdf(self, path: str, city: str, max_pages: int = 12) -> Optional[DocResult]:
        try:
            raw = extract_pdf_text(path, max_pages=max_pages)
        except Exception as e:
            print(f"[EXTRACT FAIL] {os.path.basename(path)}: {e}")
            return None

        if not raw or len(raw) < 200:
            return None

        chunks = chunk_text(raw, target_chars=self.chunk_chars, overlap=self.chunk_overlap)
        if not chunks:
            return None

        emb_chunks = self._embed_chunks(chunks)

        # Topic gating: if doc not about decarbonation domain, skip
        topic_sim = np.max(cos_sim(emb_chunks, self.emb_protos["topic"]))
        if topic_sim < self.topic_threshold:
            return None

        # Prototype similarities
        pro_sim     = cos_sim(emb_chunks, self.emb_protos["pro"])      # [n_chunks, n_pro]
        contra_sim  = cos_sim(emb_chunks, self.emb_protos["contra"])   # [n_chunks, n_contra]
        neutral_sim = cos_sim(emb_chunks, self.emb_protos["neutral"])  # [n_chunks, n_neutral]

        pro_max_by_chunk     = np.max(pro_sim, axis=1)     if pro_sim.size else np.zeros(len(chunks))
        contra_max_by_chunk  = np.max(contra_sim, axis=1)  if contra_sim.size else np.zeros(len(chunks))
        neutral_max_by_chunk = np.max(neutral_sim, axis=1) if neutral_sim.size else np.zeros(len(chunks))

        pro_max     = float(np.max(pro_max_by_chunk)) if pro_max_by_chunk.size else 0.0
        contra_max  = float(np.max(contra_max_by_chunk)) if contra_max_by_chunk.size else 0.0
        neutral_max = float(np.max(neutral_max_by_chunk)) if neutral_max_by_chunk.size else 0.0

        # Favorability score
        raw_margin = pro_max - max(contra_max, 0.5 * neutral_max)
        favorability = sigmoid((raw_margin - self.bias) * self.scale)

        label = "favorable" if favorability >= 0.6 else ("unclear" if favorability >= 0.45 else "unfavorable")

        # Evidence: top 3 chunks for pro and contra margins
        pro_margin = pro_max_by_chunk - np.maximum(contra_max_by_chunk, 0.5 * neutral_max_by_chunk)
        contra_margin = contra_max_by_chunk - np.maximum(pro_max_by_chunk, 0.5 * neutral_max_by_chunk)

        def top_k_texts(margins: np.ndarray, k: int = 3) -> List[str]:
            if margins.size == 0:
                return []
            idx = np.argsort(-margins)[:k]
            out = []
            for i in idx:
                snippet = re.sub(r"\s+", " ", chunks[i]).strip()
                out.append(snippet[:400])
            return out

        top_evidence_pro = top_k_texts(pro_margin, 3)
        top_evidence_contra = top_k_texts(contra_margin, 3)

        return DocResult(
            city=city,
            file=os.path.basename(path),
            topic_score=float(topic_sim),
            pro_max=pro_max,
            contra_max=contra_max,
            neutral_max=neutral_max,
            favorability=float(favorability),
            label=label,
            top_evidence_pro=top_evidence_pro,
            top_evidence_contra=top_evidence_contra,
        )

# ------------------ I/O ------------------
def save_outputs(results: List[DocResult], out_csv: str, out_jsonl: str):
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    os.makedirs(os.path.dirname(out_jsonl), exist_ok=True)

    rows = []
    with open(out_jsonl, "w", encoding="utf-8") as jf:
        for r in results:
            rows.append({
                "city": r.city,
                "file": r.file,
                "topic_score": r.topic_score,
                "pro_max": r.pro_max,
                "contra_max": r.contra_max,
                "neutral_max": r.neutral_max,
                "favorability": r.favorability,
                "label": r.label,
            })
            jf.write(json.dumps({
                "city": r.city,
                "file": r.file,
                "topic_score": r.topic_score,
                "pro_max": r.pro_max,
                "contra_max": r.contra_max,
                "neutral_max": r.neutral_max,
                "favorability": r.favorability,
                "label": r.label,
                "evidence": {
                    "pro": r.top_evidence_pro,
                    "contra": r.top_evidence_contra,
                }
            }, ensure_ascii=False) + "\n")

    df = pd.DataFrame(rows).sort_values(["label", "favorability"], ascending=[True, False])
    df.to_csv(out_csv, index=False, encoding="utf-8")
    print(f"[DONE] {len(df)} PDFs kept -> {out_csv}")
    return df

# ------------------ main ------------------
def main():
    import argparse
    p = argparse.ArgumentParser(description="Semantic decarbonation stance analysis (no external LLM).")
    p.add_argument("--pdf-dir", required=True, help="Folder with PDFs (e.g., results/pdfs)")
    p.add_argument("--city", required=True, help='City label, e.g. "Toulouse Métropole"')
    p.add_argument("--model", default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                   help="SentenceTransformer model (multilingual recommended)")
    p.add_argument("--device", default="cpu", help='Device: "cpu" or "cuda" if available')
    p.add_argument("--max-pages", type=int, default=12, help="Max pages per PDF to read")
    p.add_argument("--topic-threshold", type=float, default=0.20, help="Min similarity to topic prototypes to keep PDF")
    p.add_argument("--scale", type=float, default=6.0, help="Sigmoid scale for favorability mapping")
    p.add_argument("--bias", type=float, default=0.10, help="Bias before sigmoid")
    p.add_argument("--chunk-chars", type=int, default=1000, help="Approx chars per chunk")
    p.add_argument("--chunk-overlap", type=int, default=150, help="Overlap chars between chunks")
    p.add_argument("--out-csv", default="results/semantic.csv")
    p.add_argument("--out-jsonl", default="results/semantic.jsonl")
    args = p.parse_args()

    pdfs = sorted(glob.glob(os.path.join(args.pdf_dir, "*.pdf")))
    if not pdfs:
        print(f"[INFO] No PDFs found in {args.pdf_dir}")
        sys.exit(0)

    analyzer = SemanticAnalyzer(
        model_name=args.model,
        device=args.device,
        topic_threshold=args.topic_threshold,
        scale=args.scale,
        bias=args.bias,
        chunk_chars=args.chunk_chars,
        chunk_overlap=args.chunk_overlap,
    )

    results: List[DocResult] = []
    for pth in pdfs:
        res = analyzer.analyze_pdf(pth, city=args.city, max_pages=args.max_pages)
        if res is None:
            continue
        results.append(res)

    df = save_outputs(results, args.out_csv, args.out_jsonl)

    # City-level verdict
    if len(df):
        favorable_rate = float((df["label"] == "favorable").mean())
        print(f"[CITY VERDICT] {args.city}: favorable_rate={favorable_rate:.2%} over {len(df)} docs")
    else:
        print(f"[CITY VERDICT] {args.city}: no kept PDFs after topic gate")

if __name__ == "__main__":
    main()
