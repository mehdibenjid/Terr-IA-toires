# Territorial PDF Mining & Semantic Analysis

Tools to:

* crawl French local authority websites,
* download relevant PDFs (PCAET, PLU, SCOT, SRADET),
* extract text,
* run **semantic stance analysis** on decarbonation/biogas (no external LLM required),
* export results (CSV + JSONL) and archive PDFs.

Works on Windows/macOS/Linux. Tested with Python 3.10+.

---

## Project structure

```
.
├─ scraper_territoires.py      # async crawler + PDF downloader
├─ analyze_semantic.py         # semantic stance (medium model, multilingual)
├─ analyze_semantic_small.py   # semantic stance (small model: e5-small)
├─ results/
│  ├─ pdfs/                    # downloaded PDFs
│  ├─ semantic.csv             # analysis summary (CSV)
│  └─ semantic.jsonl           # analysis evidence (JSONL)
├─ models/                     # optional local HF models (e.g., models/e5-small/)
├─ targets.csv                 # seed sites (optional)
├─ requirements.txt
└─ README.md
```

---

## Quick start (TL;DR)

```powershell
# 1) Create venv (Windows PowerShell)
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip

# 2) Install deps
pip install -r requirements.txt
# or minimal:
# pip install aiohttp async-timeout beautifulsoup4 pandas pdfminer.six sentence-transformers

# 3) Crawl & download PDFs (demo targets for Toulouse)
python scraper_territoires.py --demo --year 2025 --collect-unknown --out .\results\2025.csv

# 4) Analyze PDFs semantically (small model; CPU)
python analyze_semantic_small.py --pdf-dir .\results\pdfs --city "Toulouse Métropole" --out-csv .\results\semantic.csv --out-jsonl .\results\semantic.jsonl
```

Outputs:

* `results/pdfs/*.pdf`
* `results/semantic.csv` (per-PDF stance scores and labels)
* `results/semantic.jsonl` (per-PDF top evidence snippets)

---

## Installing models locally (offline friendly)

Use the small multilingual model for speed: **`intfloat/multilingual-e5-small`**.

```powershell
pip install -U "huggingface_hub[hf_transfer]"
$env:HF_HUB_ENABLE_HF_TRANSFER = "1"

# Download once to a local folder
hf download intfloat/multilingual-e5-small --local-dir .\models\e5-small --resume-download

# (Optional) fully offline during analysis
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"

# Run analysis from local folder
python analyze_semantic_small.py --pdf-dir .\results\pdfs --city "Toulouse Métropole" --model .\models\e5-small
```

---

## CLI usage

### 1) Crawler: `scraper_territoires.py`

Crawls same-domain links (BFS) up to a given depth, finds `.pdf` links, classifies them by doc type, downloads, and writes a results CSV.

```
python scraper_territoires.py [--demo] [--input targets.csv]
                              [--region REGION] [--departement DEP]
                              [--year 2025]
                              [--collect-unknown]
                              [--max-depth 2] [--max-pages 80]
                              [--insecure-ssl]
                              [--out results/results.csv]
```

Key flags:

* `--demo` use built-in Toulouse entry points (no CSV needed).
* `--input` CSV with columns: `name,type,base_url,departement,region`.
* `--year` only keep PDFs whose **URL path contains that year** (e.g., `2025`).
* `--collect-unknown` store PDFs even if they don’t match PCAET/PLU/SCOT/SRADET patterns.
* `--max-depth` crawl depth (default 2).
* `--max-pages` max pages to fetch per target (default 80).
* `--out` CSV summary of found PDFs.

**Example:**

```powershell
# Demo, collect only 2025 PDFs, keep unknowns
python scraper_territoires.py --demo --year 2025 --collect-unknown --out .\results\found_2025.csv

# From your CSV, filter region
python scraper_territoires.py --input .\targets.csv --region Occitanie --max-depth 2 --out .\results\occitanie.csv
```

**CSV schema (`targets.csv`):**

```csv
name,type,base_url,insee,departement,region
Toulouse Métropole,metropole,https://metropole.toulouse.fr,,31,Occitanie
Ville de Toulouse,ville,https://www.toulouse.fr,,31,Occitanie
```

### 2) Semantic analysis (no external LLM)

#### Small model (recommended for quick tests): `analyze_semantic_small.py`

```
python analyze_semantic_small.py --pdf-dir results/pdfs
                                 --city "Toulouse Métropole"
                                 [--model intfloat/multilingual-e5-small|<local-folder>]
                                 [--device cpu|cuda]
                                 [--max-pages 12]
                                 [--topic-threshold 0.20]
                                 [--chunk-chars 1000]
                                 [--chunk-overlap 150]
                                 [--out-csv results/semantic.csv]
                                 [--out-jsonl results/semantic.jsonl]
```

* Uses prototype-based semantic scoring with a multilingual embedder (E5).
* Gates out off-topic PDFs via similarity to “decarbonation” prototypes.
* Produces a **favorability score** and label: `favorable`, `unclear`, or `unfavorable`.
* Writes: CSV (scores) and JSONL (top evidence snippets).

**Example:**

```powershell
# From local model folder
python analyze_semantic_small.py --pdf-dir .\results\pdfs --city "Toulouse Métropole" --model .\models\e5-small
```

#### Medium model (optional): `analyze_semantic.py`

Same interface, uses a larger multilingual model (`paraphrase-multilingual-MiniLM-L12-v2`). More accurate, heavier.

---

## Outputs

### `results/semantic.csv` (example columns)

* `city`, `file`
* `topic_score` (similarity to decarbonation topic)
* `pro_max`, `contra_max`, `neutral_max` (prototype similarities)
* `favorability` (0..1)
* `label` (`favorable` | `unclear` | `unfavorable`)

### `results/semantic.jsonl`

One JSON object per kept PDF with:

* same scores as CSV
* `evidence.pro` and `evidence.contra`: top semantic chunks (strings)

---

## Troubleshooting

* **PowerShell blocks venv activation**
  Use:

  ```powershell
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
  .\.venv\Scripts\Activate.ps1
  ```

  or run via `.\.venv\Scripts\python ...` without activating.

* **PyMuPDF import/DLL issues**
  Use the fallback: `pdfminer.six` works out of the box. You can leave PyMuPDF uninstalled.

* **Hugging Face downloads are slow**
  Install transfer accel: `pip install "huggingface_hub[hf_transfer]"` and set `$env:HF_HUB_ENABLE_HF_TRANSFER="1"`.
  Or download once with `hf download ... --local-dir ./models/...` and run offline.

* **No PDFs found**
  Increase `--max-depth` to 3, add more specific landing pages to `targets.csv`, or use `--collect-unknown`.

* **Ethics & robots**
  Respect site ToS and robots. Keep concurrency modest (`limit=6` default). Add delays if needed.

---

## Roadmap

* **Crawling**

  * Sitemap discovery, robust pagination, dedup canonicalization.
  * JS-rendered pages support via headless fetch (optional module).

* **Document understanding**

  * Table and figure extraction.
  * OCR fallback for scanned PDFs (tesseract) with language models.

* **NLP**

  * Domain-adaptive prototypes per doc type (PCAET vs PLU).
  * Ensemble: combine embedding score with optional LLM judgment.
  * Per-entity extraction (projects, budgets, locations) with spaCy.

* **Ops**

  * Caching of page fetches and embeddings.
  * Dockerfile + CI smoke tests.
  * Config file for per-city crawling policies.

---

## License

MIT (see `LICENSE`).

---

## Acknowledgements

* Sentence-Transformers and Hugging Face Hub
* pdfminer.six / PyMuPDF
* Everyone who publishes municipal documents without hiding PDFs behind 12 iframes.
