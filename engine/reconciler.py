"""
DocuRecon AI — Customs & ERP Reconciliation Engine (v4.0)
Cross-matches parsed shipping documents against:
  1. Authorized SAP Purchase Orders (SQLite)
  2. Official WCO Harmonized System Tariff Codes (SQLite) + Hierarchy NLP Classifier
  3. Official UN/LOCODE Maritime Seaports (SQLite)
  4. Cargo Valuation (FOB/CIF) Invoice Integrity & ISO 6346 Container Check Digit
"""

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Set

from engine.database import (
    get_purchase_order,
    lookup_hs_code,
    lookup_locode,
    save_audit,
)
from engine.parser import parse_document
from engine.tariff_classifier import WCOTariffClassifier
from engine.risk_scorer import compute_trade_risk, generate_dispute_notice

_LEGAL_STOPWORDS = {
    "s", "a", "sa", "ltd", "limited", "pvt", "private", "inc", "corp", "corporation",
    "co", "company", "llc", "bv", "nv", "gmbh", "ag", "plc", "global", "international",
    "export", "exports", "trading", "foods", "group", "brazil", "india", "china", "uae",
}


def _format_hs(code: str) -> str:
    digits = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(digits) >= 6:
        return f"{digits[:4]}.{digits[4:6]}"
    return digits or "N/A"


def _vendor_tokens(name: str) -> Set[str]:
    raw = re.findall(r"[a-z0-9]+", (name or "").lower())
    meaningful = {t for t in raw if len(t) >= 2 and t not in _LEGAL_STOPWORDS}
    return meaningful or {t for t in raw if len(t) >= 2}


def _shippers_match(extracted_shipper: str, po_vendor: str) -> bool:
    t_doc = _vendor_tokens(extracted_shipper)
    t_po = _vendor_tokens(po_vendor)
    if not t_doc or not t_po:
        return False
    overlap = len(t_doc & t_po)
    smaller = min(len(t_doc), len(t_po))
    return (overlap / smaller) >= 0.5


def reconcile_document_bytes(filename: str, content: bytes, persist: bool = True) -> Dict[str, Any]:
    extracted = parse_document(filename, content)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = hashlib.sha256(f"{filename}:{now_iso}:{len(content)}".encode()).hexdigest()[:8].upper()
    audit_ref = f"AUD-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{digest}"

    hs_official = lookup_hs_code(extracted["hs_code"])
    origin_port = lookup_locode(extracted["origin_locode"])
    dest_port = lookup_locode(extracted["destination_locode"])
    ai_tariff_preds = WCOTariffClassifier.get().classify(extracted["commodity"], top_k=3)
    ai_top_codes = {p["hscode"] for p in ai_tariff_preds}
    ai_tariff_agrees = extracted["hs_code"] in ai_top_codes if ai_tariff_preds else True

    po_ref = extracted["po_reference"]
    po = get_purchase_order(po_ref) if po_ref else None

    if not po:
        result = {
            "audit_ref": audit_ref,
            "audited_at_utc": now_iso,
            "filename": filename,
            "po_matched": po_ref or None,
            "extracted": extracted,
            "po_record": None,
            "official_validations": {
                "hs_code": hs_official,
                "origin_port": origin_port,
                "destination_port": dest_port,
            },
            "action_code": "UNMATCHED_PO",
            "severity": "critical",
            "badge_text": "PO Not Found in ERP Ledger",
            "discrepancies_count": 1,
            "discrepancies": [
                f"Purchase Order '{po_ref or 'MISSING'}' was not found in the SAP ERP ledger. Verify PO number or register the PO first."
            ],
            "assessment": "Cannot reconcile shipping manifest without an authorized Purchase Order reference in SQLite ERP ledger.",
            "metrics": {
                "weight_variance_mt": 0.0,
                "weight_variance_pct": 0.0,
                "freight_variance_usd": 0.0,
                "freight_variance_pct": 0.0,
                "cargo_variance_usd": 0.0,
                "cargo_variance_pct": 0.0,
            },
            "ai_tariff_predictions": ai_tariff_preds,
            "ai_tariff_agrees": ai_tariff_agrees,
            "fraud_risk": {"score": 80, "tier": "Critical Risk", "factors": []},
            "dispute_notice": {"required": "yes", "subject": "Unmatched PO Hold", "body": "Purchase Order not found in ERP ledger."},
            "comparison_rows": [],
        }
        if persist:
            save_audit(result)
        return result

    discrepancies: List[str] = []
    rows: List[Dict[str, Any]] = []

    # 1. Vendor / Shipper Entity (Token-set overlap ignoring legal suffixes)
    shipper_match = _shippers_match(extracted["shipper"], po["vendor_name"])
    rows.append(
        {
            "field": "Shipper / Exporter",
            "extracted": extracted["shipper"],
            "po_authorized": po["vendor_name"],
            "variance": "Entity Verified" if shipper_match else "Entity Mismatch",
            "status": "pass" if shipper_match else "warn",
        }
    )
    if not shipper_match:
        discrepancies.append(
            f"Shipper entity '{extracted['shipper']}' differs from authorized vendor '{po['vendor_name']}'."
        )

    # 2. Official WCO HS Tariff Code Validation
    po_hs = "".join(ch for ch in po["hs_code"] if ch.isdigit())[:6]
    doc_hs = extracted["hs_code"]
    hs_match = doc_hs == po_hs and hs_official is not None
    hs_desc = hs_official["description"] if hs_official else "Unrecognized WCO HS code"
    po_hs_official = lookup_hs_code(po_hs)
    po_hs_desc = po_hs_official["description"] if po_hs_official else po["commodity"]

    if not hs_official:
        discrepancies.append(f"HS Tariff code '{_format_hs(doc_hs)}' is not valid in the official WCO Harmonized System registry.")
        hs_status = "fail"
        hs_var = "Invalid WCO Code"
    elif doc_hs != po_hs:
        discrepancies.append(
            f"HS Tariff mismatch: document declares {_format_hs(doc_hs)} ({hs_desc[:45]}) vs PO {_format_hs(po_hs)} ({po_hs_desc[:45]})."
        )
        hs_status = "fail"
        hs_var = "Tariff Mismatch"
    else:
        hs_status = "pass"
        hs_var = "WCO Verified"

    rows.append(
        {
            "field": "WCO HS Tariff Code",
            "extracted": f"{_format_hs(doc_hs)} — {hs_desc[:54]}",
            "po_authorized": f"{_format_hs(po_hs)} — {po_hs_desc[:54]}",
            "variance": hs_var,
            "status": hs_status,
        }
    )

    # 3. Official UN/LOCODE Maritime Route Validation
    route_match = (
        extracted["origin_locode"] == po["origin_locode"]
        and extracted["destination_locode"] == po["destination_locode"]
        and origin_port is not None
        and dest_port is not None
    )
    origin_label = f"{origin_port['name']}, {origin_port['country']} ({origin_port['locode']})" if origin_port else f"Unknown ({extracted['origin_locode'] or 'N/A'})"
    dest_label = f"{dest_port['name']}, {dest_port['country']} ({dest_port['locode']})" if dest_port else f"Unknown ({extracted['destination_locode'] or 'N/A'})"
    po_origin = lookup_locode(po["origin_locode"])
    po_dest = lookup_locode(po["destination_locode"])
    po_route_str = (
        f"{po_origin['name'] if po_origin else po['origin_locode']} ({po['origin_locode']}) → "
        f"{po_dest['name'] if po_dest else po['destination_locode']} ({po['destination_locode']})"
    )
    rows.append(
        {
            "field": "UN/LOCODE Port Corridor",
            "extracted": f"{origin_label} → {dest_label}",
            "po_authorized": po_route_str,
            "variance": "UN Seaports Verified" if route_match else "Route / Port Variance",
            "status": "pass" if route_match else "warn",
        }
    )
    if not route_match:
        discrepancies.append(
            f"Port corridor {extracted['origin_locode']}→{extracted['destination_locode']} diverges from authorized PO corridor {po['origin_locode']}→{po['destination_locode']}."
        )

    # 4. Cargo Gross Weight Variance (> 1% tolerance)
    weight_var_mt = round(extracted["declared_weight_mt"] - float(po["approved_weight_mt"]), 2)
    weight_var_pct = round((weight_var_mt / max(float(po["approved_weight_mt"]), 0.01)) * 100.0, 2)
    weight_flagged = abs(weight_var_pct) > 1.0
    if weight_flagged:
        discrepancies.append(
            f"Gross cargo weight variance of {weight_var_mt:+.2f} MT ({weight_var_pct:+.1f}%) exceeds 1.0% customs tolerance."
        )
    rows.append(
        {
            "field": "Gross Cargo Weight (MT)",
            "extracted": f"{extracted['declared_weight_mt']:.2f} MT",
            "po_authorized": f"{float(po['approved_weight_mt']):.2f} MT",
            "variance": f"{weight_var_mt:+.2f} MT ({weight_var_pct:+.1f}%)" if weight_flagged else "Within 1% Tolerance",
            "status": "warn" if weight_flagged else "pass",
        }
    )

    # 5. Ocean Freight & Unapproved Surcharges
    freight_var_usd = round(extracted["invoiced_freight_usd"] - float(po["approved_freight_usd"]), 2)
    freight_var_pct = round((freight_var_usd / max(float(po["approved_freight_usd"]), 1.0)) * 100.0, 1)
    freight_flagged = freight_var_usd > 50.0
    if freight_flagged:
        discrepancies.append(
            f"Unapproved carrier freight surcharge of +${freight_var_usd:,.2f} USD (+{freight_var_pct}%) above contracted PO rate."
        )
    rows.append(
        {
            "field": "Ocean Freight Charge (USD)",
            "extracted": f"${extracted['invoiced_freight_usd']:,.2f}",
            "po_authorized": f"${float(po['approved_freight_usd']):,.2f}",
            "variance": f"+${freight_var_usd:,.2f} Overcharge" if freight_flagged else "Contract Rate Match",
            "status": "fail" if freight_flagged else "pass",
        }
    )

    # 6. Cargo Valuation (FOB/CIF) Invoice Fraud Check (> 1.5% tolerance when both present)
    po_cargo_usd = float(po.get("approved_cargo_usd") or 0.0)
    doc_cargo_usd = float(extracted.get("cargo_value_usd") or 0.0)
    if po_cargo_usd > 0 and doc_cargo_usd > 0:
        cargo_var_usd = round(doc_cargo_usd - po_cargo_usd, 2)
        cargo_var_pct = round((cargo_var_usd / po_cargo_usd) * 100.0, 2)
        cargo_flagged = abs(cargo_var_pct) > 1.5 and abs(cargo_var_usd) > 100.0
    else:
        cargo_var_usd = 0.0
        cargo_var_pct = 0.0
        cargo_flagged = False

    if cargo_flagged:
        label = "over-invoicing" if cargo_var_usd > 0 else "under-invoicing"
        discrepancies.append(
            f"Cargo customs valuation {label} of {cargo_var_usd:+,.2f} USD ({cargo_var_pct:+.1f}%) vs authorized PO value ${po_cargo_usd:,.2f}."
        )
    rows.append(
        {
            "field": "Cargo Value FOB/CIF (USD)",
            "extracted": f"${doc_cargo_usd:,.2f}" if doc_cargo_usd > 0 else "Not Declared",
            "po_authorized": f"${po_cargo_usd:,.2f}" if po_cargo_usd > 0 else "N/A",
            "variance": f"{cargo_var_usd:+,.2f} ({cargo_var_pct:+.1f}%)" if cargo_flagged else "Valuation Match",
            "status": "fail" if cargo_flagged else "pass",
        }
    )

    # 7. Cold-Chain Reefer Telemetry Check
    temp_val = extracted["reefer_temp_celsius"]
    max_temp = float(po["max_temp_celsius"])
    temp_breach = temp_val is not None and temp_val > max_temp
    if temp_breach:
        discrepancies.append(
            f"Cold-chain thermal breach: telemetry logged {temp_val}°C exceeding maximum allowable {max_temp}°C."
        )
    rows.append(
        {
            "field": "Reefer Temperature Telemetry",
            "extracted": f"{temp_val}°C" if temp_val is not None else "Ambient / N/A",
            "po_authorized": f"Max {max_temp}°C",
            "variance": f"+{round(temp_val - max_temp, 1)}°C Thermal Breach" if temp_breach else "Compliant",
            "status": "fail" if temp_breach else "pass",
        }
    )

    # 8. ISO 6346 Maritime Container Check Digit Validation
    iso_info = extracted.get("container_iso6346") or {}
    iso_valid = bool(iso_info.get("valid", True))
    rows.append(
        {
            "field": "Container ID (ISO 6346)",
            "extracted": extracted["container_number"],
            "po_authorized": f"Check Digit = {iso_info.get('expected_check_digit') if iso_info.get('expected_check_digit') is not None else 'Valid ISO'}",
            "variance": "ISO 6346 Verified" if iso_valid else iso_info.get("reason", "Invalid Check Digit"),
            "status": "pass" if iso_valid else "warn",
        }
    )
    if not iso_valid:
        discrepancies.append(f"Container ID '{extracted['container_number']}': {iso_info.get('reason', 'failed ISO 6346 check digit')}.")

    # Executive Decision
    if temp_breach:
        action_code = "QUARANTINE_VET_INSPECTION"
        severity = "critical"
        badge_text = "Critical Thermal Breach · Quarantine"
        assessment = (
            f"Container {extracted['container_number']} recorded {temp_val}°C (limit {max_temp}°C). "
            "Place immediate customs hold and route to port veterinary cold-chain laboratory inspection."
        )
    elif len(discrepancies) == 0:
        action_code = "APPROVE_GREEN_CHANNEL"
        severity = "healthy"
        badge_text = "100% ERP & WCO Verified · Green Channel"
        assessment = (
            f"Declared weight, WCO HS tariff ({_format_hs(doc_hs)}), UN/LOCODE seaports, contracted freight, "
            "cargo valuation, ISO 6346 container ID, and thermal logs match the authorized SAP PO. Cleared for automated customs filing."
        )
    else:
        action_code = "HOLD_FOR_AMENDMENT"
        severity = "warning"
        badge_text = f"{len(discrepancies)} Discrepanc{'y' if len(discrepancies) == 1 else 'ies'} Flagged · Hold"
        assessment = (
            " ".join(discrepancies)
            + " Issue carrier debit note / manifest amendment prior to customs duty settlement."
        )

    fraud_risk = compute_trade_risk(
        extracted=extracted,
        po=po,
        weight_var_pct=weight_var_pct,
        freight_var_usd=freight_var_usd,
        freight_var_pct=freight_var_pct,
        cargo_var_usd=cargo_var_usd,
        cargo_var_pct=cargo_var_pct,
        hs_match=hs_match,
        ai_tariff_match=ai_tariff_agrees,
        route_match=route_match,
        temp_breach=temp_breach,
        iso_valid=iso_valid,
    )
    dispute_notice = generate_dispute_notice(
        audit_ref=audit_ref,
        extracted=extracted,
        po=po,
        discrepancies=discrepancies,
        freight_var_usd=freight_var_usd,
        cargo_var_usd=cargo_var_usd,
    )

    result = {
        "audit_ref": audit_ref,
        "audited_at_utc": now_iso,
        "filename": filename,
        "po_matched": po["po_number"],
        "extracted": extracted,
        "po_record": po,
        "official_validations": {
            "hs_code": hs_official,
            "origin_port": origin_port,
            "destination_port": dest_port,
        },
        "ai_tariff_predictions": ai_tariff_preds,
        "ai_tariff_agrees": ai_tariff_agrees,
        "fraud_risk": fraud_risk,
        "dispute_notice": dispute_notice,
        "action_code": action_code,
        "severity": severity,
        "badge_text": badge_text,
        "discrepancies_count": len(discrepancies),
        "discrepancies": discrepancies,
        "assessment": assessment,
        "metrics": {
            "weight_variance_mt": weight_var_mt,
            "weight_variance_pct": weight_var_pct,
            "freight_variance_usd": freight_var_usd,
            "freight_variance_pct": freight_var_pct,
            "cargo_variance_usd": cargo_var_usd,
            "cargo_variance_pct": cargo_var_pct,
        },
        "comparison_rows": rows,
    }

    if persist:
        save_audit(result)

    return result
