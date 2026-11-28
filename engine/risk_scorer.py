"""
DocuRecon AI — Multi-Factor Trade Fraud Risk Scorer & Automated Legal Debit Note Generator (v4.0)
Scores each shipment (0-100) across freight overcharge, cargo over/under-invoicing,
WCO HS tariff misclassification, cargo weight tampering, cold-chain thermal failure,
UN/LOCODE corridor deviation, and ISO 6346 container check-digit validity.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List


def compute_trade_risk(
    extracted: Dict[str, Any],
    po: Dict[str, Any],
    weight_var_pct: float,
    freight_var_usd: float,
    freight_var_pct: float,
    cargo_var_usd: float,
    cargo_var_pct: float,
    hs_match: bool,
    ai_tariff_match: bool,
    route_match: bool,
    temp_breach: bool,
    iso_valid: bool,
) -> Dict[str, Any]:
    factors: List[Dict[str, Any]] = []
    score = 0.0

    # 1. Carrier Freight Overcharge (0-30 pts)
    if freight_var_usd > 50:
        pts = min(30.0, round(10.0 + abs(freight_var_pct) * 1.0, 1))
        score += pts
        factors.append(
            {
                "factor": "Carrier Freight Overcharge",
                "points": pts,
                "max_points": 30,
                "detail": f"+${freight_var_usd:,.2f} (+{freight_var_pct}%) above contracted PO rate",
                "level": "high" if pts >= 20 else "medium",
            }
        )
    else:
        factors.append(
            {
                "factor": "Carrier Freight Overcharge",
                "points": 0,
                "max_points": 30,
                "detail": "Matches contracted freight terms",
                "level": "low",
            }
        )

    # 2. Cargo Valuation / Invoice Fraud (0-25 pts)
    if abs(cargo_var_pct) > 1.5 and abs(cargo_var_usd) > 100:
        pts = min(25.0, round(10.0 + abs(cargo_var_pct) * 2.2, 1))
        score += pts
        direction = "Over-invoiced" if cargo_var_usd > 0 else "Under-invoiced"
        factors.append(
            {
                "factor": "Customs Cargo Valuation",
                "points": pts,
                "max_points": 25,
                "detail": f"{direction} by {cargo_var_usd:+,.2f} USD ({cargo_var_pct:+.1f}%)",
                "level": "high" if abs(cargo_var_pct) >= 5.0 else "medium",
            }
        )
    else:
        factors.append(
            {
                "factor": "Customs Cargo Valuation",
                "points": 0,
                "max_points": 25,
                "detail": "Invoice value matches authorized SAP PO",
                "level": "low",
            }
        )

    # 3. WCO HS Tariff Classification (0-25 pts)
    if not hs_match:
        pts = 25.0
        score += pts
        factors.append(
            {
                "factor": "WCO HS Tariff Compliance",
                "points": pts,
                "max_points": 25,
                "detail": "Declared HS code conflicts with SAP PO & duty schedule",
                "level": "high",
            }
        )
    elif not ai_tariff_match:
        pts = 8.0
        score += pts
        factors.append(
            {
                "factor": "WCO HS Tariff Compliance",
                "points": pts,
                "max_points": 25,
                "detail": "Matches PO but NLP semantic classifier suggests review",
                "level": "medium",
            }
        )
    else:
        factors.append(
            {
                "factor": "WCO HS Tariff Compliance",
                "points": 0,
                "max_points": 25,
                "detail": "Verified against official WCO registry & NLP model",
                "level": "low",
            }
        )

    # 4. Cargo Weight Tolerance (0-20 pts)
    if abs(weight_var_pct) > 1.0:
        pts = min(20.0, round(6.0 + abs(weight_var_pct) * 3.5, 1))
        score += pts
        factors.append(
            {
                "factor": "Cargo Weight Variance",
                "points": pts,
                "max_points": 20,
                "detail": f"{weight_var_pct:+.1f}% variance exceeds 1.0% tolerance",
                "level": "high" if abs(weight_var_pct) >= 3.0 else "medium",
            }
        )
    else:
        factors.append(
            {
                "factor": "Cargo Weight Variance",
                "points": 0,
                "max_points": 20,
                "detail": "Within 1.0% customs weight tolerance",
                "level": "low",
            }
        )

    # 5. Cold-Chain Thermal Integrity (0-30 pts)
    if temp_breach:
        pts = 30.0
        score += pts
        factors.append(
            {
                "factor": "Cold-Chain Thermal Integrity",
                "points": pts,
                "max_points": 30,
                "detail": f"Logged {extracted['reefer_temp_celsius']}°C exceeding {po['max_temp_celsius']}°C limit",
                "level": "high",
            }
        )
    else:
        factors.append(
            {
                "factor": "Cold-Chain Thermal Integrity",
                "points": 0,
                "max_points": 30,
                "detail": "Within authorized temperature band",
                "level": "low",
            }
        )

    # 6. UN/LOCODE Corridor & ISO 6346 Container Integrity (0-15 pts)
    corridor_pts = (10.0 if not route_match else 0.0) + (5.0 if not iso_valid else 0.0)
    score += corridor_pts
    if corridor_pts > 0:
        notes = []
        if not route_match:
            notes.append("Port corridor mismatch")
        if not iso_valid:
            notes.append("Invalid ISO 6346 check digit")
        factors.append(
            {
                "factor": "Port & Container Integrity",
                "points": corridor_pts,
                "max_points": 15,
                "detail": " · ".join(notes),
                "level": "medium",
            }
        )
    else:
        factors.append(
            {
                "factor": "Port & Container Integrity",
                "points": 0,
                "max_points": 15,
                "detail": "UN/LOCODE seaports & ISO 6346 container verified",
                "level": "low",
            }
        )

    final_score = int(min(100, round(score)))
    if final_score >= 55:
        tier = "Critical Risk"
    elif final_score >= 28:
        tier = "Elevated Risk"
    elif final_score >= 10:
        tier = "Moderate Watch"
    else:
        tier = "Verified Low Risk"

    return {
        "score": final_score,
        "tier": tier,
        "factors": factors,
    }


def generate_dispute_notice(
    audit_ref: str,
    extracted: Dict[str, Any],
    po: Dict[str, Any],
    discrepancies: List[str],
    freight_var_usd: float,
    cargo_var_usd: float = 0.0,
) -> Dict[str, str]:
    if not discrepancies:
        return {
            "required": "no",
            "subject": f"Customs Clearance Approved — {extracted['container_number']} ({po['po_number']})",
            "body": (
                f"Document {extracted['filename']} for container {extracted['container_number']} "
                f"is 100% reconciled against SAP Purchase Order {po['po_number']}. No carrier dispute required."
            ),
        }

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lang = extracted.get("language", "English")
    bullet_lines = "\n".join(f"  - {d}" for d in discrepancies)

    total_claim = max(0.0, freight_var_usd if freight_var_usd > 50 else 0.0) + max(0.0, cargo_var_usd if cargo_var_usd > 100 else 0.0)
    if total_claim > 0:
        claim_en = f"Requested Credit / Debit Note Adjustment: USD ${total_claim:,.2f}"
        claim_pt = f"Ajuste Solicitado via Nota de Crédito / Débito: USD ${total_claim:,.2f}"
        claim_fr = f"Ajustement Demandé par Note de Débit / Avoir : USD ${total_claim:,.2f}"
    else:
        claim_en = "Requested Action: Immediate Amended Manifest / Veterinary Clearance Hold"
        claim_pt = "Ação Solicitada: Retificação Imediata do Manifesto / Bloqueio Aduaneiro"
        claim_fr = "Action Demandée : Rectification Immédiate du Manifeste / Blocage Vétérinaire"

    if lang == "French":
        subject = f"AVIS DE CONTESTATION / NOTE DE DÉBIT — Conteneur {extracted['container_number']} ({po['po_number']})"
        body = (
            f"Date: {today} | Référence d'Audit: {audit_ref}\n"
            f"À: Service Facturation & Opérations, {extracted['carrier']}\n"
            f"Navire: {extracted['vessel_name']} | Corridor: {extracted['origin_locode']} -> {extracted['destination_locode']}\n\n"
            f"Madame, Monsieur,\n\n"
            f"Suite à notre rapprochement automatisé du document '{extracted['filename']}' avec le Bon de Commande SAP {po['po_number']}, "
            f"les anomalies suivantes ont été identifiées :\n{bullet_lines}\n\n"
            f"{claim_fr}\n\n"
            f"Merci d'émettre un document rectifié sous 24 heures avant le dédouanement final.\n"
            f"Cordialement,\nTrade Compliance & Logistics Audit Desk (DocuRecon AI)"
        )
    elif lang == "Portuguese":
        subject = f"NOTIFICAÇÃO DE DIVERGÊNCIA / NOTA DE DÉBITO — Contentor {extracted['container_number']} ({po['po_number']})"
        body = (
            f"Data: {today} | Referência de Auditoria: {audit_ref}\n"
            f"Para: Departamento Comercial e Operações, {extracted['carrier']}\n"
            f"Navio: {extracted['vessel_name']} | Rota: {extracted['origin_locode']} -> {extracted['destination_locode']}\n"
            f"Pedido SAP Autorizado: {po['po_number']} | Fornecedor: {po['vendor_name']}\n\n"
            f"Prezados,\n\n"
            f"Na reconciliação aduaneira do documento '{extracted['filename']}' contra o Pedido SAP {po['po_number']}, "
            f"foram detetadas as seguintes divergências:\n{bullet_lines}\n\n"
            f"{claim_pt}\n\n"
            f"Solicitamos retificação imediata e emissão de nota de crédito em até 24 horas antes do despacho aduaneiro.\n"
            f"Atenciosamente,\nTrade Compliance & Logistics Audit Desk (DocuRecon AI)"
        )
    else:
        subject = f"FORMAL DISCREPANCY NOTICE & DEBIT NOTE — Container {extracted['container_number']} ({po['po_number']})"
        body = (
            f"Date: {today} | Audit Reference: {audit_ref}\n"
            f"To: Billing & Claims Desk, {extracted['carrier']}\n"
            f"Vessel: {extracted['vessel_name']} | Corridor: {extracted['origin_locode']} -> {extracted['destination_locode']}\n"
            f"Authorized SAP PO: {po['po_number']} | Shipper: {po['vendor_name']}\n\n"
            f"Dear Operations Team,\n\n"
            f"Our automated customs and ERP reconciliation of '{extracted['filename']}' against authorized Purchase Order "
            f"{po['po_number']} flagged the following compliance exceptions:\n{bullet_lines}\n\n"
            f"{claim_en}\n\n"
            f"Please issue a revised freight invoice / amended manifest within 24 hours to avoid port demurrage and customs hold.\n\n"
            f"Regards,\nTrade Compliance & Customs Audit Desk (DocuRecon AI)"
        )

    return {"required": "yes", "subject": subject, "body": body}
