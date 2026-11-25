# DocuRecon AI — Trade Document Intelligence, WCO NLP Tariff & ERP Audit Workbench

A precision customs compliance, fraud risk scoring, and multilingual shipping document reconciliation workbench built with **Python, FastAPI, Scikit-Learn, pypdf, and SQLite**. Parses genuine **PDF, TXT, CSV, and JSON** shipping documents in **English, Portuguese, and French**, classifies commodities against **6,940 official WCO HS Tariff Codes**, verifies ports against **17,647 official UN/LOCODE Seaports**, and cross-matches every field against a live **SAP Purchase Order ledger**.

---

## Official Public Trade Data Provenance

Reproducible via `scripts/download_reference_data.py` into `data/docurecon.db`:

1. **WCO Harmonized System (HS) Tariff Codes (`6,940` official classifications)**
   * Source: `https://raw.githubusercontent.com/datasets/harmonized-system/master/data/harmonized-system.csv`
2. **UN/LOCODE Global Maritime Seaports (`17,647` verified international seaports)**
   * Source: `https://raw.githubusercontent.com/datasets/un-locode/main/data/code-list.csv`

---

## Core Enterprise AI & Trade Compliance Features

| Module | What It Does |
| :--- | :--- |
| **1. Split-Pane Document Workbench** | Upload single or multi-file batch `.pdf`, `.txt`, `.csv`, `.json` manifests. Left pane displays live `pypdf` extracted text with inline green/amber/red audit highlights; right pane shows live AI verification and variance tables. |
| **2. Live WCO NLP Tariff Classifier (`6,940` Codes)** | `scikit-learn` TF-IDF + Cosine Similarity semantic classifier trained across all 6,940 official WCO HS descriptions. Independently predicts the Top-3 HS codes from raw commodity text to catch customs tariff misclassification. |
| **3. Multi-Factor Trade Fraud Risk Scorer (`0–100`)** | Scores every shipment across Carrier Freight Overcharge, WCO Tariff Compliance, Cargo Weight Tampering, Cold-Chain Thermal Integrity, and UN/LOCODE Corridor Deviation. |
| **4. Automated Multilingual Carrier Debit Notes** | Drafts formal, ready-to-send carrier dispute notices in **English, Portuguese, or French** citing container numbers, SAP POs, UN/LOCODE corridors, and exact USD overcharges to recover. |
| **5. Trade Compliance AI Copilot** | Slide-over copilot grounded in live SQLite audits and the WCO NLP classifier (supports optional `GEMINI_API_KEY` via gitignored `.env` with complete local tool-calling fallback). |
| **6. SAP PO CRUD & IDoc Export** | Full SQLite Purchase Order management (`GET/POST/DELETE`), downloadable CSV audit ledger, and structured `INVOIC02_CUSTOMS_RECON` SAP JSON payloads. |

---

## Included Real Sample Documents (`sample_docs/`)

* `BOL_BRSSZ_AOLAD_8841.pdf` *(English)* — Catches **+0.80 MT** weight variance & **+$750** unapproved freight surcharge (`HOLD_FOR_AMENDMENT`).
* `FATURA_COMERCIAL_BRPNG_AOLAD_9104.pdf` *(Portuguese)* — 100% matched across WCO HS `0202.30`, UN seaports, weight, freight, and temp (`APPROVE_GREEN_CHANNEL`).
* `BORDEREAU_CNSGH_AOLAD_7729.pdf` *(French)* — Catches **-15.4°C** cold-chain thermal breach above `-18.0°C` limit (`QUARANTINE_VET_INSPECTION`).
* `INV_INNSA_AEJEA_4410.pdf` *(English, India → UAE)* — Auto-converts `25,000 KG → 25.00 MT` and catches **HS Tariff mismatch (`1006.40` Broken Rice vs `1006.30` Milled Rice)** + **+$380** freight overcharge.
* `MANIFEST_NLRTM_NGLOS_5520.txt` *(English, Rotterdam → Lagos)* — 100% compliant (`APPROVE_GREEN_CHANNEL`).

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Download official WCO HS Codes & UN/LOCODE Seaports into SQLite
python scripts/download_reference_data.py

# 3. Generate real multilingual sample PDFs
python scripts/generate_sample_docs.py

# 4. Run the application
python app.py
# Workbench: http://127.0.0.1:8001 | Swagger Docs: http://127.0.0.1:8001/docs

# 5. Run automated tests (10/10 passing)
python -m unittest discover -s tests -v
```
