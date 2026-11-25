"""
DocuRecon AI — Production FastAPI Server
Autonomous Customs Manifest, WCO HS Tariff, UN/LOCODE Seaport & SAP PO Reconciliation.
"""

import csv
import io
import os
from typing import List, Optional
import uvicorn
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine.database import (
    delete_purchase_order,
    get_audit_by_ref,
    get_reference_metadata,
    list_audits,
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

app = FastAPI(
    title="DocuRecon AI — Customs Manifest & ERP Reconciliation Engine",
    description="Multilingual shipping document parser & ERP discrepancy auditor backed by official WCO HS Tariff & UN/LOCODE Seaport tables.",
    version="3.0.0",
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


def _ensure_initial_audits():
    """Populate initial audits from real sample_docs/ PDFs on first run if history is empty."""
    if list_audits(limit=1):
        return
    if not os.path.exists(SAMPLE_DIR):
        return
    for fn in sorted(os.listdir(SAMPLE_DIR)):
        fp = os.path.join(SAMPLE_DIR, fn)
        if os.path.isfile(fp) and fn.lower().endswith((".pdf", ".txt", ".csv", ".json")):
            with open(fp, "rb") as f:
                reconcile_document_bytes(fn, f.read(), persist=True)


@app.get("/api/health")
def health_check():
    meta = get_reference_metadata()
    return {
        "status": "healthy",
        "service": "DocuRecon AI",
        "version": "3.0.0",
        "wco_hs_codes": int(meta.get("hs_code_count", 0)),
        "un_seaports": int(meta.get("seaport_count", 0)),
        "purchase_orders": len(list_purchase_orders()),
    }


@app.get("/api/overview")
def get_overview():
    _ensure_initial_audits()
    audits = list_audits(limit=50)
    pos = list_purchase_orders()
    meta = get_reference_metadata()

    total = len(audits)
    green = sum(1 for a in audits if a["action_code"] == "APPROVE_GREEN_CHANNEL")
    holds = sum(1 for a in audits if a["action_code"] == "HOLD_FOR_AMENDMENT")
    quarantine = sum(1 for a in audits if a["action_code"] == "QUARANTINE_VET_INSPECTION")
    overcharges_caught = round(
        sum(max(0.0, float(a["freight_variance_usd"] or 0.0)) for a in audits), 2
    )
    total_discrepancies = sum(int(a["discrepancies_count"] or 0) for a in audits)
    green_rate = round((green / max(total, 1)) * 100.0, 1)

    return {
        "metadata": meta,
        "summary": {
            "total_audited": total,
            "green_channel_count": green,
            "hold_amendment_count": holds,
            "quarantine_count": quarantine,
            "green_channel_rate_pct": green_rate,
            "overcharges_prevented_usd": overcharges_caught,
            "total_discrepancies_caught": total_discrepancies,
            "active_purchase_orders": len(pos),
            "wco_hs_codes": int(meta.get("hs_code_count", 0)),
            "un_seaports": int(meta.get("seaport_count", 0)),
        },
        "recent_audits": audits[:12],
    }


@app.get("/api/samples")
def list_sample_documents():
    files = []
    if os.path.exists(SAMPLE_DIR):
        for fn in sorted(os.listdir(SAMPLE_DIR)):
            fp = os.path.join(SAMPLE_DIR, fn)
            if os.path.isfile(fp):
                files.append(
                    {
                        "filename": fn,
                        "size_bytes": os.path.getsize(fp),
                        "ext": fn.rsplit(".", 1)[-1].upper() if "." in fn else "FILE",
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
        "model": "WCO Harmonized System TF-IDF Semantic Classifier (6,940 Official Codes)",
        "predictions": preds,
    }


@app.post("/api/copilot")
def ask_trade_copilot(req: TradeCopilotRequest):
    return trade_copilot.ask(req.query)


@app.get("/api/purchase-orders")
def get_pos():
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
def get_all_audits(limit: int = Query(50, ge=1, le=200)):
    _ensure_initial_audits()
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
            "ACTION_CODE": item["action_code"],
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
    audits = list_audits(limit=200)
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
            "Discrepancies",
            "Freight Variance (USD)",
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
                a["discrepancies_count"],
                a["freight_variance_usd"],
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
    print("=" * 64)
    print("  DocuRecon AI — Customs Manifest & ERP Reconciliation Engine")
    print("  Dashboard: http://127.0.0.1:8001")
    print("  API Docs : http://127.0.0.1:8001/docs")
    print("=" * 64)
    uvicorn.run("app:app", host="127.0.0.1", port=8001, reload=True)
