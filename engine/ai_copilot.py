"""
DocuRecon AI — Trade Compliance & Customs Intelligence Copilot
Answers analytical questions about audited shipments, WCO HS tariff classifications,
UN/LOCODE maritime corridors, and freight overcharge recovery.
Uses live Gemini API via `.env` (GEMINI_API_KEY) when present, with a complete
local SQLite + NLP tool-calling fallback so every query works out-of-the-box.
"""

import json
import os
import urllib.request
from typing import Any, Dict

from engine.database import list_audits, list_purchase_orders, search_hs_codes, search_locodes
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
        audits = list_audits(limit=25)
        pos = list_purchase_orders()

        api_key = _load_gemini_key()
        if api_key:
            try:
                compact = {
                    "recent_audits": [
                        {
                            "ref": a["audit_ref"],
                            "file": a["filename"],
                            "po": a["po_number"],
                            "action": a["action_code"],
                            "discrepancies": a["discrepancies_count"],
                            "freight_var_usd": a["freight_variance_usd"],
                            "weight_var_mt": a["weight_variance_mt"],
                        }
                        for a in audits[:10]
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

        # Deterministic Local Tool-Calling Analytical Engine
        q = query.lower()
        if any(k in q for k in ["classify", "hs code", "tariff", "what code"]):
            cleaned = q.replace("classify", "").replace("hs code for", "").replace("what hs code", "").replace("tariff", "").strip()
            preds = WCOTariffClassifier.get().classify(cleaned or query, top_k=3)
            if preds:
                lines = [f"**WCO NLP Tariff Classification for '{cleaned or query}':**"]
                for p in preds:
                    lines.append(f"• **{p['hscode_formatted']}** ({p['confidence_pct']}% match) — {p['description'][:85]}")
                return {"mode": "Local WCO TF-IDF NLP Classifier (6,940 Codes)", "answer": "\n".join(lines)}

        if any(k in q for k in ["overcharge", "freight", "recover", "money", "saving", "cost"]):
            flagged = [a for a in audits if (a["freight_variance_usd"] or 0) > 50]
            total_over = sum(float(a["freight_variance_usd"]) for a in flagged)
            lines = [
                f"**Freight Overcharge Recovery Summary (${total_over:,.2f} Flagged):**",
            ]
            for a in flagged[:4]:
                lines.append(
                    f"• **{a['filename']}** ({a['po_number']}): **+${a['freight_variance_usd']:,.2f} USD** unapproved carrier surcharge — issue Debit Note to {a['carrier']}."
                )
            if not flagged:
                lines.append("• Zero carrier freight overcharges detected in recent audits.")
            return {"mode": "Local Audit Ledger Engine (SQLite)", "answer": "\n".join(lines)}

        if any(k in q for k in ["quarantine", "temp", "thermal", "cold", "spoil"]):
            quar = [a for a in audits if a["action_code"] == "QUARANTINE_VET_INSPECTION"]
            lines = [f"**Cold-Chain Quarantine Report ({len(quar)} Container Hold(s)):**"]
            for a in quar[:4]:
                lines.append(
                    f"• **{a['filename']}** (Container {a['container_number']}): Thermal limit breached on route {a['origin_locode']}→{a['destination_locode']}. Hold for veterinary inspection."
                )
            if not quar:
                lines.append("• All audited cold-chain containers remained within temperature tolerances.")
            return {"mode": "Local Cold-Chain Compliance Engine", "answer": "\n".join(lines)}

        # Default executive trade compliance summary
        green = sum(1 for a in audits if a["action_code"] == "APPROVE_GREEN_CHANNEL")
        holds = sum(1 for a in audits if a["action_code"] != "APPROVE_GREEN_CHANNEL")
        total_over = sum(max(0.0, float(a["freight_variance_usd"] or 0)) for a in audits)
        lines = [
            f"**Trade Compliance Audit Summary ({len(audits)} Audits · {len(pos)} Active SAP POs):**",
            f"• **Clearance Split:** {green} Green Channel approved, {holds} held for amendment or quarantine.",
            f"• **Financial Leakage Stopped:** **${total_over:,.2f} USD** in unapproved ocean freight surcharges flagged.",
            "• **Tip:** Ask me *'Classify frozen boneless beef'* to run the live WCO NLP tariff model, or *'Show freight overcharges'* for recovery targets.",
        ]
        return {"mode": "Local Trade Audit Engine (SQLite + WCO NLP)", "answer": "\n".join(lines)}
