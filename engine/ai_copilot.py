"""
DocuRecon AI — Grounded Trade Compliance & Customs Intelligence Copilot (v4.0)
Uses deduplicated unique shipment audits + Hierarchy WCO HS NLP Classifier.
Supports optional live Gemini API via `.env` (GEMINI_API_KEY) with deterministic local fallback.
"""

import json
import os
import urllib.request
from typing import Any, Dict

from engine.database import list_latest_unique_audits, list_purchase_orders
from engine.tariff_classifier import WCOTariffClassifier

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_gemini_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if key:
        return key
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    return ""


class TradeComplianceCopilot:
    def ask(self, query: str) -> Dict[str, Any]:
        audits = list_latest_unique_audits()
        pos = list_purchase_orders()

        api_key = _load_gemini_key()
        if api_key:
            try:
                compact = {
                    "unique_shipments": [
                        {
                            "ref": a["audit_ref"],
                            "file": a["filename"],
                            "po": a["po_number"],
                            "action": a["action_code"],
                            "risk": a.get("risk_score", 0),
                            "freight_var_usd": a["freight_variance_usd"],
                            "cargo_var_usd": a.get("cargo_variance_usd", 0.0),
                            "weight_var_mt": a["weight_variance_mt"],
                        }
                        for a in audits[:12]
                    ],
                    "open_pos": len(pos),
                }
                prompt = (
                    "You are DocuRecon AI, a senior customs broker & ERP trade compliance auditor. "
                    "Answer concisely in 3-5 concrete bullet points grounded in these live SQLite audits:\n"
                    f"{json.dumps(compact)}\n\nQuestion: {query}"
                )
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
                payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
                req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=15) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                    return {
                        "mode": "Gemini 2.5 Flash (Grounded Trade Audit Telemetry)",
                        "answer": body["candidates"][0]["content"]["parts"][0]["text"].strip(),
                    }
            except Exception:
                pass

        q = query.lower()
        if any(k in q for k in ["classify", "hs code", "tariff", "what code"]):
            cleaned = (
                q.replace("classify", "")
                .replace("hs code for", "")
                .replace("what hs code", "")
                .replace("tariff", "")
                .strip()
            )
            preds = WCOTariffClassifier.get().classify(cleaned or query, top_k=3)
            if preds:
                lines = [f"**WCO Hierarchy NLP Classification for '{cleaned or query}':**"]
                for p in preds:
                    lines.append(
                        f"• **{p['hscode_formatted']}** ({p['confidence_pct']}% match) — {p['description'][:88]}"
                    )
                return {"mode": "Local WCO Hierarchy Word+Char NLP (6,940 Codes)", "answer": "\n".join(lines)}

        if any(k in q for k in ["overcharge", "freight", "recover", "money", "saving", "cost", "leakage", "valuation"]):
            flagged_f = [a for a in audits if (a["freight_variance_usd"] or 0) > 50]
            flagged_c = [a for a in audits if abs(a.get("cargo_variance_usd") or 0) > 100]
            total_f = sum(float(a["freight_variance_usd"]) for a in flagged_f)
            total_c = sum(max(0.0, float(a.get("cargo_variance_usd") or 0)) for a in flagged_c)
            lines = [
                f"**Freight Overcharge & Valuation Recovery Summary (${total_f + total_c:,.2f} Total Flagged):**",
            ]
            for a in flagged_f[:4]:
                lines.append(
                    f"• **{a['filename']}** ({a['po_number']}): **+${a['freight_variance_usd']:,.2f} USD** carrier surcharge — Debit Note to {a['carrier']}."
                )
            for a in flagged_c[:3]:
                lines.append(
                    f"• **{a['filename']}** ({a['po_number']}): **${a['cargo_variance_usd']:+,.2f} USD** customs cargo valuation variance."
                )
            if not flagged_f and not flagged_c:
                lines.append("• Zero freight or cargo valuation discrepancies detected across unique shipments.")
            return {"mode": "Local Deduplicated Audit Ledger (SQLite)", "answer": "\n".join(lines)}

        if any(k in q for k in ["quarantine", "temp", "thermal", "cold", "spoil"]):
            quar = [a for a in audits if a["action_code"] == "QUARANTINE_VET_INSPECTION"]
            lines = [f"**Cold-Chain Quarantine Report ({len(quar)} Container Hold(s)):**"]
            for a in quar[:4]:
                lines.append(
                    f"• **{a['filename']}** ({a['container_number']}): Thermal limit breached on {a['origin_locode']}→{a['destination_locode']}. Hold for veterinary inspection."
                )
            if not quar:
                lines.append("• All audited cold-chain containers remained within temperature tolerances.")
            return {"mode": "Local Cold-Chain Compliance Engine", "answer": "\n".join(lines)}

        green = sum(1 for a in audits if a["action_code"] == "APPROVE_GREEN_CHANNEL")
        holds = sum(1 for a in audits if a["action_code"] != "APPROVE_GREEN_CHANNEL")
        total_f = sum(max(0.0, float(a["freight_variance_usd"] or 0)) for a in audits if (a["freight_variance_usd"] or 0) > 50)
        total_c = sum(max(0.0, float(a.get("cargo_variance_usd") or 0)) for a in audits if (a.get("cargo_variance_usd") or 0) > 100)
        lines = [
            f"**Trade Compliance Summary ({len(audits)} Unique Shipments · {len(pos)} Active SAP POs):**",
            f"• **Clearance Split:** {green} Green Channel approved, {holds} held for amendment or veterinary quarantine.",
            f"• **Financial Leakage Stopped:** **${total_f:,.2f}** carrier freight overcharges + **${total_c:,.2f}** FOB/CIF over-invoicing flagged.",
            "• **Quick Commands:** Ask *'Classify granular urea fertilizer'* or *'Show freight overcharges to recover'*.",
        ]
        return {"mode": "Local Trade Audit Engine (SQLite + WCO NLP)", "answer": "\n".join(lines)}
