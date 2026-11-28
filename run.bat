@echo off
title DocuRecon AI v4.0 - Customs Manifest & Trade Compliance Workbench
cd /d "%~dp0"
where python >nul 2>&1
if %errorlevel%==0 (
    set PY=python
) else (
    set "PY=C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\platform\bundledpython\python.exe"
    set "PYTHONPATH=%LOCALAPPDATA%\Programs\Python\Python313\Lib\site-packages"
)
if not exist "data\docurecon.db" (
    echo [SETUP] Downloading official WCO HS Tariff Codes & UN/LOCODE Seaports...
    "%PY%" scripts\download_reference_data.py
)
if not exist "sample_docs\INV_RUNVS_AEJEA_6042.pdf" (
    "%PY%" scripts\generate_sample_docs.py
)
echo [START] Launching DocuRecon AI on http://127.0.0.1:8001
start "" http://127.0.0.1:8001
"%PY%" app.py
pause
