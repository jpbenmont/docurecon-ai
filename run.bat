@echo off
title DocuRecon AI - Customs Manifest & ERP Reconciler
cd /d "%~dp0"
if not exist "data\docurecon.db" (
    echo [SETUP] Downloading official WCO HS Tariff Codes & UN/LOCODE Seaports...
    python scripts\download_reference_data.py
)
if not exist "sample_docs\BOL_BRSSZ_AOLAD_8841.pdf" (
    python scripts\generate_sample_docs.py
)
echo [START] Launching DocuRecon AI on http://127.0.0.1:8001
start "" http://127.0.0.1:8001
python app.py
pause
