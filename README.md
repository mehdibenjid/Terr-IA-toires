Bon, tu veux ton README en français et un petit speech d’ascenseur d’une minute. Voilà :

---

# 🗂️ Extraction et analyse sémantique de PDFs territoriaux

Outils pour :

* explorer les sites web des collectivités françaises,
* télécharger les PDFs pertinents (PCAET, PLU, SCOT, SRADET),
* extraire le texte,
* exécuter une **analyse sémantique d’orientation** (décarbonation/biogaz, sans LLM externe),
* exporter les résultats (CSV + JSONL) et archiver les PDFs.

Compatible Windows/macOS/Linux. Testé avec Python 3.10+.

---

## Structure du projet

```
.
├─ scraper_territoires.py      # crawler async + téléchargement PDF
├─ analyze_semantic.py         # analyse sémantique (modèle moyen, multilingue)
├─ analyze_semantic_small.py   # analyse sémantique (modèle léger : e5-small)
├─ results/
│  ├─ pdfs/                    # PDFs téléchargés
│  ├─ semantic.csv             # synthèse des analyses (CSV)
│  └─ semantic.jsonl           # extraits de preuves (JSONL)
├─ models/                     # modèles HF locaux (ex : models/e5-small/)
├─ targets.csv                 # sites de départ (optionnel)
├─ requirements.txt
└─ README.md
```

---

## Démarrage rapide (TL;DR)

```powershell
# 1) Créer un venv (Windows PowerShell)
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip

# 2) Installer les dépendances
pip install -r requirements.txt
# ou minimal :
# pip install aiohttp async-timeout beautifulsoup4 pandas pdfminer.six sentence-transformers

# 3) Crawler et télécharger les PDFs (démo Toulouse)
python scraper_territoires.py --demo --year 2025 --collect-unknown --out .\results\2025.csv

# 4) Analyser les PDFs sémantiquement (modèle léger ; CPU)
python analyze_semantic_small.py --pdf-dir .\results\pdfs --city "Toulouse Métropole" --out-csv .\results\semantic.csv --out-jsonl .\results\semantic.jsonl
```

Résultats :

* `results/pdfs/*.pdf`
* `results/semantic.csv` (scores et labels par PDF)
* `results/semantic.jsonl` (extraits de preuves par PDF)

---

## Installer les modèles en local (mode hors-ligne)

Utiliser le modèle léger multilingue pour la rapidité : **`intfloat/multilingual-e5-small`**.

```powershell
pip install -U "huggingface_hub[hf_transfer]"
$env:HF_HUB_ENABLE_HF_TRANSFER = "1"

# Téléchargement unique dans un dossier local
hf download intfloat/multilingual-e5-small --local-dir .\models\e5-small --resume-download

# (Optionnel) exécution totalement hors-ligne
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"

# Analyse depuis le dossier local
python analyze_semantic_small.py --pdf-dir .\results\pdfs --city "Toulouse Métropole" --model .\models\e5-small
```

---

## Utilisation CLI

### 1) Crawler : `scraper_territoires.py`

Explore les liens du même domaine (BFS) jusqu’à une profondeur donnée, trouve les liens `.pdf`, les classe par type de document, télécharge et génère un CSV de résultats.

```
python scraper_territoires.py [--demo] [--input targets.csv]
                              [--region REGION] [--departement DEP]
                              [--year 2025]
                              [--collect-unknown]
                              [--max-depth 2] [--max-pages 80]
                              [--insecure-ssl]
                              [--out results/results.csv]
```

Exemple :

```powershell
python scraper_territoires.py --demo --year 2025 --collect-unknown --out .\results\found_2025.csv
```

---

### 2) Analyse sémantique

#### Modèle léger (recommandé) : `analyze_semantic_small.py`

```
python analyze_semantic_small.py --pdf-dir results/pdfs
                                 --city "Toulouse Métropole"
                                 [--model intfloat/multilingual-e5-small|<dossier-local>]
                                 [--device cpu|cuda]
                                 [--out-csv results/semantic.csv]
                                 [--out-jsonl results/semantic.jsonl]
```

* Analyse par similarité sémantique avec des prototypes thématiques.
* Filtrage hors-sujet via similarité “décarbonation”.
* Produit un **score de favorabilité** et une étiquette : `favorable`, `peu clair`, `défavorable`.

---

## Roadmap

* Crawler : découverte de sitemaps, meilleure gestion de pagination, support JS.
* Analyse : extraction de tableaux/figures, OCR pour scans, prototypes spécifiques par type de doc.
* Ops : cache des fetchs/embeddings, Dockerfile + CI, config par collectivité.

---

## Licence

MIT