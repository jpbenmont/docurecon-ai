"""
DocuRecon AI — Real Multilingual Shipping Document Parser
Extracts structured customs, tariff, port, weight, freight, and reefer telemetry
fields from real PDF (via pypdf), TXT, CSV, and JSON shipping documents in
English, Portuguese (Fatura Comercial / Conhecimento de Embarque), and French
(Bordereau d'Expédition / Liste de Colisage).
"""

import csv
import io
import json
import re
from typing import Any, Dict, Optional
from pypdf import PdfReader


def extract_text_from_bytes(filename: str, content: bytes) -> str:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else "txt"
    if ext == "pdf":
        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages).strip()
    return content.decode("utf-8", errors="replace").strip()


def _detect_language(text: str) -> str:
    lower = text.lower()
    pt_hits = sum(k in lower for k in ["fatura comercial", "peso bruto", "porto de", "frete marítimo", "exportador", "conhecimento"])
    fr_hits = sum(k in lower for k in ["bordereau", "poids brut", "port de", "fret maritime", "expéditeur", "température"])
    if pt_hits >= 2:
        return "Portuguese"
    if fr_hits >= 2:
        return "French"
    return "English"


def _detect_doc_type(text: str, filename: str) -> str:
    combo = f"{filename} {text[:400]}".lower()
    if "bill of lading" in combo or "bol" in combo or "conhecimento" in combo:
        return "Maritime Bill of Lading"
    if "fatura comercial" in combo or "commercial invoice" in combo or "inv_" in combo or "invoice" in combo:
        return "Commercial Export Invoice"
    if "bordereau" in combo or "packing list" in combo or "colisage" in combo:
        return "Cold-Chain Packing List & Reefer Log"
    return "Maritime Trade Manifest"


def _first_match(patterns, text: str) -> Optional[str]:
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).strip()
    return None


def _parse_float(val: Optional[str]) -> Optional[float]:
    if not val:
        return None
    cleaned = val.replace(",", "").replace("$", "").replace("USD", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_document(filename: str, content: bytes) -> Dict[str, Any]:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else "txt"

    # Handle structured JSON directly
    if ext == "json":
        obj = json.loads(content.decode("utf-8", errors="replace"))
        hs_clean = "".join(ch for ch in str(obj.get("hs_code", "")) if ch.isdigit())[:6]
        return {
            "filename": filename,
            "doc_type": obj.get("doc_type", "JSON Manifest"),
            "language": obj.get("language", "Structured JSON"),
            "po_reference": str(obj.get("po_reference", "")).strip().upper(),
            "container_number": str(obj.get("container_number", "")).strip().upper(),
            "carrier": str(obj.get("carrier", "Unknown Carrier")).strip(),
            "vessel_name": str(obj.get("vessel_name", "N/A")).strip(),
            "shipper": str(obj.get("shipper", "Unknown Shipper")).strip(),
            "origin_locode": str(obj.get("origin_locode", "")).strip().upper()[:5],
            "destination_locode": str(obj.get("destination_locode", "")).strip().upper()[:5],
            "hs_code": hs_clean,
            "commodity": str(obj.get("commodity", "")).strip(),
            "declared_weight_mt": float(obj.get("declared_weight_mt", 0.0)),
            "invoiced_freight_usd": float(obj.get("invoiced_freight_usd", 0.0)),
            "cargo_value_usd": float(obj.get("cargo_value_usd", 0.0)),
            "reefer_temp_celsius": obj.get("reefer_temp_celsius"),
            "raw_text": json.dumps(obj, indent=2),
        }

    # Handle CSV (first record row)
    if ext == "csv":
        raw_text = content.decode("utf-8", errors="replace")
        rows = list(csv.DictReader(io.StringIO(raw_text)))
        if not rows:
            raise ValueError("Uploaded CSV file contains no data rows.")
        row = {k.strip().lower(): (v or "").strip() for k, v in rows[0].items() if k}
        hs_clean = "".join(ch for ch in row.get("hs_code", "") if ch.isdigit())[:6]
        temp_val = _parse_float(row.get("reefer_temp_celsius") or row.get("temp_celsius"))
        return {
            "filename": filename,
            "doc_type": row.get("doc_type", "CSV Trade Manifest"),
            "language": "Structured CSV",
            "po_reference": (row.get("po_reference") or row.get("po_number") or "").upper(),
            "container_number": (row.get("container_number") or "N/A").upper(),
            "carrier": row.get("carrier") or "Unknown Carrier",
            "vessel_name": row.get("vessel_name") or "N/A",
            "shipper": row.get("shipper") or row.get("vendor_name") or "Unknown Shipper",
            "origin_locode": (row.get("origin_locode") or "").upper()[:5],
            "destination_locode": (row.get("destination_locode") or "").upper()[:5],
            "hs_code": hs_clean,
            "commodity": row.get("commodity") or "",
            "declared_weight_mt": _parse_float(row.get("declared_weight_mt") or row.get("gross_weight_mt")) or 0.0,
            "invoiced_freight_usd": _parse_float(row.get("invoiced_freight_usd") or row.get("ocean_freight_usd")) or 0.0,
            "cargo_value_usd": _parse_float(row.get("cargo_value_usd")) or 0.0,
            "reefer_temp_celsius": temp_val,
            "raw_text": raw_text[:4000],
        }

    # PDF or TXT unstructured extraction
    raw_text = extract_text_from_bytes(filename, content)
    if not raw_text:
        raise ValueError(f"Could not extract readable text from '{filename}'.")

    language = _detect_language(raw_text)
    doc_type = _detect_doc_type(raw_text, filename)

    po_ref = _first_match(
        [
            r"(?:PO\s*(?:Reference|Number|No\.?|Ref)|Pedido\s*de\s*Compra|Bon\s*de\s*Commande)\s*[:#-]?\s*([A-Z0-9-]{6,30})",
            r"\b(PO-[A-Z]{2,5}-\d{4}-\d{3,6})\b",
        ],
        raw_text,
    )

    container = _first_match(
        [
            r"(?:Container\s*(?:No\.?|Number)?|Contentor|Conteneur)\s*[:#-]?\s*([A-Z]{4}\s?\d{6}[- ]?\d)",
            r"\b([A-Z]{4}\s?\d{6}-\d)\b",
        ],
        raw_text,
    )

    carrier = _first_match(
        [
            r"(?:Carrier|Shipping\s*Line|Transportadora|Armateur)\s*[:\-]\s*([^\n]+)",
        ],
        raw_text,
    )

    vessel = _first_match(
        [
            r"(?:Vessel\s*(?:/\s*Voyage)?|Navio|Navire)\s*[:\-]\s*([^\n]+)",
        ],
        raw_text,
    )

    shipper = _first_match(
        [
            r"(?:Shipper|Exporter|Exportador|Exp[eé]diteur)\s*[:\-]\s*([^\n]+)",
        ],
        raw_text,
    )

    origin_locode = _first_match(
        [
            r"(?:Port\s*of\s*Loading|Porto\s*de\s*Embarque|Port\s*d'Embarquement|Origin\s*Port)[^\n]*?\(([A-Z]{5})\)",
            r"(?:Origin\s*LOCODE|POL\s*Code)\s*[:\-]\s*([A-Z]{5})",
        ],
        raw_text,
    )

    dest_locode = _first_match(
        [
            r"(?:Port\s*of\s*Discharge|Porto\s*de\s*Descarga|Port\s*de\s*D[eé]chargement|Destination\s*Port)[^\n]*?\(([A-Z]{5})\)",
            r"(?:Destination\s*LOCODE|POD\s*Code)\s*[:\-]\s*([A-Z]{5})",
        ],
        raw_text,
    )

    hs_raw = _first_match(
        [
            r"(?:HS\s*(?:Tariff\s*)?Code|NCM\s*/?\s*HS|Code\s*SH|Tariff\s*Code)\s*[:#-]?\s*(\d{4}\.?\d{2})",
        ],
        raw_text,
    )
    hs_code = "".join(ch for ch in (hs_raw or "") if ch.isdigit())[:6]

    commodity = _first_match(
        [
            r"(?:Commodity|Description\s*of\s*Goods|Mercadoria|Marchandise)\s*[:\-]\s*([^\n]+)",
        ],
        raw_text,
    )

    # Weight extraction (supports MT or KG auto-conversion)
    weight_mt = None
    m_mt = re.search(
        r"(?:Gross\s*Weight|Declared\s*Weight|Peso\s*Bruto|Poids\s*Brut)\s*[:\-]\s*([\d,\.]+)\s*(MT|Metric\s*Tons?|Toneladas|Tonnes?)",
        raw_text,
        flags=re.IGNORECASE,
    )
    if m_mt:
        weight_mt = _parse_float(m_mt.group(1))
    else:
        m_kg = re.search(
            r"(?:Gross\s*Weight|Declared\s*Weight|Peso\s*Bruto|Poids\s*Brut)\s*[:\-]\s*([\d,\.]+)\s*(KG|Kilograms?)",
            raw_text,
            flags=re.IGNORECASE,
        )
        if m_kg:
            kg_val = _parse_float(m_kg.group(1))
            weight_mt = round(kg_val / 1000.0, 3) if kg_val is not None else None

    freight_str = _first_match(
        [
            r"(?:Total\s*Ocean\s*Freight|Ocean\s*Freight\s*(?:Invoiced|Charges?)?|Frete\s*Mar[ií]timo|Fret\s*Maritime)\s*[:\-]\s*(?:USD|\$)?\s*([\d,\.]+)",
        ],
        raw_text,
    )
    freight_usd = _parse_float(freight_str) or 0.0

    cargo_str = _first_match(
        [
            r"(?:Cargo\s*Value|FOB\s*Value|CIF\s*Value|Valor\s*FOB|Valeur\s*Marchandise)\s*[:\-]\s*(?:USD|\$)?\s*([\d,\.]+)",
        ],
        raw_text,
    )
    cargo_usd = _parse_float(cargo_str) or 0.0

    temp_str = _first_match(
        [
            r"(?:Reefer\s*Temp(?:erature)?|Container\s*Temp(?:erature)?|Temperatura\s*Reefer|Temp[eé]rature\s*Conteneur)\s*[:\-]\s*([-+]?\d+(?:\.\d+)?)\s*°?\s*C",
        ],
        raw_text,
    )
    temp_c = _parse_float(temp_str)

    return {
        "filename": filename,
        "doc_type": doc_type,
        "language": language,
        "po_reference": (po_ref or "").upper(),
        "container_number": (container or "UNSPECIFIED").upper(),
        "carrier": carrier or "Unspecified Carrier",
        "vessel_name": vessel or "N/A",
        "shipper": shipper or "Unspecified Shipper",
        "origin_locode": (origin_locode or "").upper(),
        "destination_locode": (dest_locode or "").upper(),
        "hs_code": hs_code,
        "commodity": commodity or "Unspecified Cargo",
        "declared_weight_mt": round(weight_mt, 2) if weight_mt is not None else 0.0,
        "invoiced_freight_usd": round(freight_usd, 2),
        "cargo_value_usd": round(cargo_usd, 2),
        "reefer_temp_celsius": temp_c,
        "raw_text": raw_text[:6000],
    }
