"""
DocuRecon AI — Production FastAPI Server (v4.0)
Autonomous Customs Manifest, Hierarchy WCO HS Tariff NLP, UN/LOCODE Seaport,
ISO 6346 Container Check-Digit, Cargo Valuation & SAP PO Reconciliation.
"""

from contextlib import asynccontextmanager
import csv
import io
import os
from typing import List
import uvicorn
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine.database import (
    DB_PATH,
    delete_purchase_order,
    ensure_seed_purchase_orders,
    get_audit_by_ref,
    get_reference_metadata,
    list_audits,
    list_latest_unique_audits,
    list_purchase_orders,
    search_hs_codes,
    search_locodes,
    upsert_purchase_order,
)
from engine.reconciler import reconcile_document_bytes
from engine.tariff_classifier import WCOTariffClassifier
from engine.ai_copilot import TradeComplianceCopilot

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
SAMPLE_DIR = os.path.join(BASE_DIR, "sample_docs")
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(SAMPLE_DIR, exist_ok=True)

EXTRA_SEED_POS = [
    {
        "po_number": "PO-LUA-2026-9318",
        "vendor_name": "Bunge Alimentos S.A. (Brazil)",
        "commodity": "Refined Sunflower Oil 20L Jerrycans",
        "hs_code": "151219",
        "origin_locode": "BRSSZ",
        "destination_locode": "AOLAD",
        "approved_weight_mt": 22.00,
        "approved_freight_usd": 4500.00,
        "approved_cargo_usd": 35500.00,
        "max_temp_celsius": 28.0,
        "incoterms": "CIF",
        "status": "OPEN",
    },
    {
        "po_number": "PO-DXB-2026-6042",
        "vendor_name": "EuroChem Trading DMCC",
        "commodity": "Granular Urea 46% Nitrogen Fertilizer 50kg PP Bags",
        "hs_code": "310210",
        "origin_locode": "RUNVS",
        "destination_locode": "AEJEA",
        "approved_weight_mt": 27.00,
        "approved_freight_usd": 3400.00,
        "approved_cargo_usd": 10800.00,
        "max_temp_celsius": 35.0,
        "incoterms": "CFR",
        "status": "OPEN",
    },
]


def _ensure_initial_audits():
    """Populate initial audits for any un-audited sample_docs/ files."""
    if not os.path.exists(SAMPLE_DIR):
        return
    existing_files = {a["filename"] for a in list_latest_unique_audits()}
    for fn in sorted(os.listdir(SAMPLE_DIR)):
        fp = os.path.join(SAMPLE_DIR, fn)
        if os.path.isfile(fp) and fn.lower().endswith((".pdf", ".txt", ".csv", ".json")):
            if fn not in existing_files:
                with open(fp, "rb") as f:
                    reconcile_document_bytes(fn, f.read(), persist=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not os.path.exists(DB_PATH):
        from scripts.download_reference_data import build_database

        build_database()
    ensure_seed_purchase_orders(EXTRA_SEED_POS)
    if not os.path.exists(os.path.join(SAMPLE_DIR, "INV_RUNVS_AEJEA_6042.pdf")):
        from scripts.generate_sample_docs import build_samples

        build_samples()
    WCOTariffClassifier.get()
    _ensure_initial_audits()
    yield


app = FastAPI(
    title="DocuRecon AI — Customs Manifest & ERP Reconciliation Engine",
    description="Multilingual shipping document parser, WCO HS hierarchy NLP classifier, ISO 6346 validator & SAP PO discrepancy auditor.",
    version="4.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PurchaseOrderIn(BaseModel):
    po_number: str = Field(..., min_length=4, max_length=40)
    vendor_name: str = Field(..., min_length=2)
    commodity: str = Field(..., min_length=2)
    hs_code: str = Field(..., min_length=4, max_length=10)
    origin_locode: str = Field(..., min_length=5, max_length=5)
    destination_locode: str = Field(..., min_length=5, max_length=5)
    approved_weight_mt: float = Field(..., gt=0)
    approved_freight_usd: float = Field(..., ge=0)
    approved_cargo_usd: float = Field(0.0, ge=0)
    max_temp_celsius: float = Field(-18.0)
    incoterms: str = Field("CIF")


class ClassifyHSRequest(BaseModel):
    commodity_description: str = Field(..., min_length=2)
    top_k: int = Field(5, ge=1, le=10)


class TradeCopilotRequest(BaseModel):
    query: str = Field(..., min_length=2)


trade_copilot = TradeComplianceCopilot()


@app.get("/api/health")
def health_check():
    meta = get_reference_metadata()
    return {
        "status": "healthy",
        "service": "DocuRecon AI",
        "version": "4.0.0",
        "wco_hs_codes": int(meta.get("hs_code_count", 0)),
        "un_seaports": int(meta.get("seaport_count", 0)),
        "purchase_orders": len(list_purchase_orders()),
    }


@app.get("/api/overview")
def get_overview():
    ensure_seed_purchase_orders(EXTRA_SEED_POS)
    _ensure_initial_audits()
    unique_audits = list_latest_unique_audits()
    pos = list_purchase_orders()
    meta = get_reference_metadata()

    total = len(unique_audits)
    green = sum(1 for a in unique_audits if a["action_code"] == "APPROVE_GREEN_CHANNEL")
    holds = sum(1 for a in unique_audits if a["action_code"] == "HOLD_FOR_AMENDMENT")
    quarantine = sum(1 for a in unique_audits if a["action_code"] == "QUARANTINE_VET_INSPECTION")
    freight_over = sum(max(0.0, float(a["freight_variance_usd"] or 0.0)) for a in unique_audits if (a["freight_variance_usd"] or 0) > 50)
    cargo_over = sum(max(0.0, float(a.get("cargo_variance_usd") or 0.0)) for a in unique_audits if (a.get("cargo_variance_usd") or 0) > 100)
    total_leakage_caught = round(freight_over + cargo_over, 2)
    total_discrepancies = sum(int(a["discrepancies_count"] or 0) for a in unique_audits)
    green_rate = round((green / max(total, 1)) * 100.0, 1)

    return {
        "metadata": meta,
        "summary": {
            "total_audited": total,
            "green_channel_count": green,
            "hold_amendment_count": holds,
            "quarantine_count": quarantine,
            "green_channel_rate_pct": green_rate,
            "freight_overcharges_usd": round(freight_over, 2),
            "cargo_overinvoicing_usd": round(cargo_over, 2),
            "overcharges_prevented_usd": total_leakage_caught,
            "total_discrepancies_caught": total_discrepancies,
            "active_purchase_orders": len(pos),
            "wco_hs_codes": int(meta.get("hs_code_count", 0)),
            "un_seaports": int(meta.get("seaport_count", 0)),
        },
        "recent_audits": unique_audits[:15],
    }


@app.get("/api/samples")
def list_sample_documents():
    latest_map = {a["filename"]: a for a in list_latest_unique_audits()}
    files = []
    if os.path.exists(SAMPLE_DIR):
        for fn in sorted(os.listdir(SAMPLE_DIR)):
            fp = os.path.join(SAMPLE_DIR, fn)
            if os.path.isfile(fp):
                aud = latest_map.get(fn)
                files.append(
                    {
                        "filename": fn,
                        "size_bytes": os.path.getsize(fp),
                        "ext": fn.rsplit(".", 1)[-1].upper() if "." in fn else "FILE",
                        "language": aud["language_detected"] if aud else None,
                        "corridor": f"{aud['origin_locode']}→{aud['destination_locode']}" if aud and aud.get("origin_locode") else None,
                        "severity": aud["severity"] if aud else None,
                        "action_code": aud["action_code"] if aud else None,
                        "risk_score": aud["risk_score"] if aud else None,
                    }
                )
    return {"samples": files}


@app.get("/api/samples/{filename}/download")
def download_sample_document(filename: str):
    safe = os.path.basename(filename)
    fp = os.path.join(SAMPLE_DIR, safe)
    if not os.path.isfile(fp):
        raise HTTPException(status_code=404, detail="Sample file not found.")
    return FileResponse(fp, filename=safe)


@app.post("/api/reconcile/sample/{filename}")
def reconcile_sample(filename: str):
    safe = os.path.basename(filename)
    fp = os.path.join(SAMPLE_DIR, safe)
    if not os.path.isfile(fp):
        raise HTTPException(status_code=404, detail=f"Sample document '{safe}' not found.")
    try:
        with open(fp, "rb") as f:
            return reconcile_document_bytes(safe, f.read(), persist=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/reconcile/upload")
async def reconcile_upload(file: UploadFile = File(...)):
    try:
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        return reconcile_document_bytes(file.filename or "uploaded_manifest.txt", raw, persist=True)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/reconcile/batch")
async def reconcile_batch(files: List[UploadFile] = File(...)):
    results = []
    for f in files:
        raw = await f.read()
        if not raw:
            continue
        results.append(reconcile_document_bytes(f.filename or "batch_doc.txt", raw, persist=True))
    return {"count": len(results), "results": results}


@app.post("/api/classify-hs")
def classify_hs_code(req: ClassifyHSRequest):
    preds = WCOTariffClassifier.get().classify(req.commodity_description, top_k=req.top_k)
    return {
        "commodity": req.commodity_description,
        "model": "WCO Hierarchy Hybrid Word+Char TF-IDF Classifier (6,940 Official Codes)",
        "predictions": preds,
    }


@app.post("/api/copilot")
def ask_trade_copilot(req: TradeCopilotRequest):
    return trade_copilot.ask(req.query)


@app.get("/api/purchase-orders")
def get_pos():
    ensure_seed_purchase_orders(EXTRA_SEED_POS)
    return {"purchase_orders": list_purchase_orders()}


@app.post("/api/purchase-orders")
def create_or_update_po(po: PurchaseOrderIn):
    try:
        saved = upsert_purchase_order(po.model_dump())
        return {"status": "saved", "purchase_order": saved}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/purchase-orders/{po_number}")
def remove_po(po_number: str):
    ok = delete_purchase_order(po_number)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Purchase Order '{po_number}' not found.")
    return {"status": "deleted", "po_number": po_number}


@app.get("/api/audits")
def get_all_audits(limit: int = Query(50, ge=1, le=200), unique_only: bool = Query(True)):
    _ensure_initial_audits()
    if unique_only:
        return {"audits": list_latest_unique_audits()[:limit]}
    return {"audits": list_audits(limit=limit)}


@app.get("/api/audits/{audit_ref}")
def get_audit_detail(audit_ref: str):
    item = get_audit_by_ref(audit_ref)
    if not item:
        raise HTTPException(status_code=404, detail=f"Audit '{audit_ref}' not found.")
    return item


@app.get("/api/audits/{audit_ref}/sap-json")
def export_sap_audit(audit_ref: str):
    item = get_audit_by_ref(audit_ref)
    if not item:
        raise HTTPException(status_code=404, detail=f"Audit '{audit_ref}' not found.")
    hs = item["official_validations"].get("hs_code") or {}
    return {
        "SAP_IDOC_TYPE": "INVOIC02_CUSTOMS_RECON",
        "AUDIT_HEADER": {
            "AUDIT_REF": item["audit_ref"],
            "AUDITED_AT_UTC": item["audited_at_utc"],
            "FILENAME": item["filename"],
            "PO_NUMBER": item["po_matched"],
            "CONTAINER_NO": item["extracted"].get("container_number"),
            "ISO_6346_VALID": item["extracted"].get("container_iso6346", {}).get("valid"),
            "ACTION_CODE": item["action_code"],
            "FRAUD_RISK_SCORE": item.get("fraud_risk", {}).get("score"),
            "DISCREPANCIES_COUNT": item["discrepancies_count"],
        },
        "OFFICIAL_TRADE_VALIDATION": {
            "WCO_HS_CODE": hs.get("hscode", item["extracted"].get("hs_code")),
            "WCO_DESCRIPTION": hs.get("description", "UNVERIFIED"),
            "ORIGIN_UNLOCODE": item["extracted"].get("origin_locode"),
            "DESTINATION_UNLOCODE": item["extracted"].get("destination_locode"),
        },
        "FINANCIAL_VARIANCE": item["metrics"],
        "COMPARISON_ROWS": item["comparison_rows"],
    }


@app.get("/api/audits-export-csv")
def export_audits_csv():
    audits = list_latest_unique_audits()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Audit Ref",
            "Timestamp (UTC)",
            "Filename",
            "Language",
            "PO Number",
            "Container",
            "Route",
            "HS Code",
            "Risk Score",
            "Discrepancies",
            "Freight Variance (USD)",
            "Cargo Valuation Variance (USD)",
            "Weight Variance (MT)",
            "Action Code",
        ]
    )
    for a in audits:
        writer.writerow(
            [
                a["audit_ref"],
                a["audited_at"],
                a["filename"],
                a["language_detected"],
                a["po_number"] or "",
                a["container_number"] or "",
                f"{a['origin_locode'] or ''}->{a['destination_locode'] or ''}",
                a["hs_code"] or "",
                a.get("risk_score", 0),
                a["discrepancies_count"],
                a["freight_variance_usd"],
                a.get("cargo_variance_usd", 0.0),
                a["weight_variance_mt"],
                a["action_code"],
            ]
        )
    return PlainTextResponse(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=docurecon_audit_log.csv"},
    )


@app.get("/api/reference/hs")
def query_hs(q: str = Query(""), limit: int = Query(40, ge=1, le=100)):
    return {"results": search_hs_codes(q, limit=limit)}


@app.get("/api/reference/ports")
def query_ports(q: str = Query(""), limit: int = Query(40, ge=1, le=100)):
    return {"results": search_locodes(q, limit=limit)}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8001"))
    host = os.getenv("HOST", "0.0.0.0" if os.getenv("RENDER") else "127.0.0.1")
    is_render = bool(os.getenv("RENDER"))
    print("=" * 64)
    print("  DocuRecon AI v4.0 — Customs Manifest & ERP Reconciliation Engine")
    print(f"  Dashboard: http://{host}:{port}")
    print(f"  API Docs : http://{host}:{port}/docs")
    print("=" * 64)
    uvicorn.run("app:app", host=host, port=port, reload=not is_render)
