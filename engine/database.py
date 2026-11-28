"""
DocuRecon AI — SQLite Data Layer (v4.0)
Provides safe parameterized queries across:
  - Official WCO Harmonized System (HS) Tariff Codes (6,940 rows)
  - Official UN/LOCODE Global Maritime Seaports (17,647 rows)
  - SAP Purchase Orders (Full Create/Read/Update/Delete)
  - Persistent Customs Audit History + Deduplicated Shipment Metrics
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "docurecon.db")


@contextmanager
def db_conn():
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(
            f"Database not found at {DB_PATH}. Run 'python scripts/download_reference_data.py' first."
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_reference_metadata() -> Dict[str, str]:
    with db_conn() as conn:
        rows = conn.execute("SELECT key, value FROM reference_metadata").fetchall()
        return {r["key"]: r["value"] for r in rows}


def lookup_hs_code(raw_code: str) -> Optional[Dict[str, Any]]:
    digits = "".join(ch for ch in (raw_code or "") if ch.isdigit())
    if not digits:
        return None
    with db_conn() as conn:
        for candidate in (digits[:6], digits[:4], digits[:2]):
            if not candidate:
                continue
            row = conn.execute(
                "SELECT hscode, section, description, parent, level FROM hs_codes WHERE hscode = ?",
                (candidate,),
            ).fetchone()
            if row:
                return dict(row)
    return None


def search_hs_codes(query: str = "", limit: int = 40) -> List[Dict[str, Any]]:
    limit = max(1, min(limit, 100))
    q = (query or "").strip()
    with db_conn() as conn:
        if not q:
            rows = conn.execute(
                "SELECT hscode, section, description, level FROM hs_codes WHERE level = 6 LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            like = f"%{q}%"
            rows = conn.execute(
                """
                SELECT hscode, section, description, level
                FROM hs_codes
                WHERE hscode LIKE ? OR description LIKE ?
                ORDER BY CASE WHEN hscode LIKE ? THEN 0 ELSE 1 END, length(hscode) DESC
                LIMIT ?
                """,
                (like, like, f"{q}%", limit),
            ).fetchall()
        return [dict(r) for r in rows]


def lookup_locode(code: str) -> Optional[Dict[str, Any]]:
    clean = "".join(ch for ch in (code or "").upper() if ch.isalnum())[:5]
    if len(clean) != 5:
        return None
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM un_locodes WHERE locode = ?", (clean,)
        ).fetchone()
        return dict(row) if row else None


def search_locodes(query: str = "", limit: int = 40) -> List[Dict[str, Any]]:
    limit = max(1, min(limit, 100))
    q = (query or "").strip()
    with db_conn() as conn:
        if not q:
            rows = conn.execute(
                """
                SELECT locode, country, name, subdivision, status, function_code, coordinates
                FROM un_locodes
                WHERE locode IN ('BRSSZ','BRPNG','AOLAD','CNSGH','INNSA','AEJEA','NGLOS','NLRTM','RUNVS','SGSIN','NAWVB','ZADUR')
                ORDER BY locode ASC
                """
            ).fetchall()
        else:
            like = f"%{q}%"
            rows = conn.execute(
                """
                SELECT locode, country, name, subdivision, status, function_code, coordinates
                FROM un_locodes
                WHERE locode LIKE ? OR name LIKE ? OR country LIKE ?
                ORDER BY CASE WHEN locode LIKE ? THEN 0 ELSE 1 END, name ASC
                LIMIT ?
                """,
                (like, like, like, f"{q.upper()}%", limit),
            ).fetchall()
        return [dict(r) for r in rows]


def list_purchase_orders() -> List[Dict[str, Any]]:
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM purchase_orders ORDER BY created_at DESC, po_number ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_purchase_order(po_number: str) -> Optional[Dict[str, Any]]:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM purchase_orders WHERE UPPER(po_number) = UPPER(?)",
            ((po_number or "").strip(),),
        ).fetchone()
        return dict(row) if row else None


def upsert_purchase_order(po: Dict[str, Any]) -> Dict[str, Any]:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hs_clean = "".join(ch for ch in str(po["hs_code"]) if ch.isdigit())[:6]
    record = {
        "po_number": str(po["po_number"]).strip().upper(),
        "vendor_name": str(po["vendor_name"]).strip(),
        "commodity": str(po["commodity"]).strip(),
        "hs_code": hs_clean,
        "origin_locode": str(po["origin_locode"]).strip().upper()[:5],
        "destination_locode": str(po["destination_locode"]).strip().upper()[:5],
        "approved_weight_mt": float(po["approved_weight_mt"]),
        "approved_freight_usd": float(po["approved_freight_usd"]),
        "approved_cargo_usd": float(po.get("approved_cargo_usd", 0.0)),
        "max_temp_celsius": float(po.get("max_temp_celsius", -18.0)),
        "incoterms": str(po.get("incoterms", "CIF")).strip().upper(),
        "status": str(po.get("status", "OPEN")).strip().upper(),
        "created_at": now_iso,
    }
    with db_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO purchase_orders
            (po_number, vendor_name, commodity, hs_code, origin_locode, destination_locode,
             approved_weight_mt, approved_freight_usd, approved_cargo_usd, max_temp_celsius,
             incoterms, status, created_at)
            VALUES (:po_number, :vendor_name, :commodity, :hs_code, :origin_locode, :destination_locode,
                    :approved_weight_mt, :approved_freight_usd, :approved_cargo_usd, :max_temp_celsius,
                    :incoterms, :status, :created_at)
            """,
            record,
        )
        conn.commit()
    return record


def ensure_seed_purchase_orders(seed_list: List[Dict[str, Any]]) -> None:
    """Idempotently inserts any missing seed POs without overwriting user edits."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db_conn() as conn:
        for po in seed_list:
            exists = conn.execute(
                "SELECT 1 FROM purchase_orders WHERE UPPER(po_number) = UPPER(?)",
                (po["po_number"],),
            ).fetchone()
            if not exists:
                conn.execute(
                    """
                    INSERT INTO purchase_orders
                    (po_number, vendor_name, commodity, hs_code, origin_locode, destination_locode,
                     approved_weight_mt, approved_freight_usd, approved_cargo_usd, max_temp_celsius,
                     incoterms, status, created_at)
                    VALUES (:po_number, :vendor_name, :commodity, :hs_code, :origin_locode, :destination_locode,
                            :approved_weight_mt, :approved_freight_usd, :approved_cargo_usd, :max_temp_celsius,
                            :incoterms, :status, :created_at)
                    """,
                    {**po, "created_at": now_iso},
                )
        conn.commit()


def delete_purchase_order(po_number: str) -> bool:
    with db_conn() as conn:
        cur = conn.execute(
            "DELETE FROM purchase_orders WHERE UPPER(po_number) = UPPER(?)",
            (po_number.strip(),),
        )
        conn.commit()
        return cur.rowcount > 0


def save_audit(result: Dict[str, Any]) -> None:
    with db_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO audit_history
            (audit_ref, filename, doc_type, language_detected, po_number, container_number,
             carrier, origin_locode, destination_locode, hs_code, action_code, severity,
             discrepancies_count, freight_variance_usd, weight_variance_mt, payload_json, audited_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result["audit_ref"],
                result["filename"],
                result["extracted"].get("doc_type", "Shipping Manifest"),
                result["extracted"].get("language", "English"),
                result.get("po_matched"),
                result["extracted"].get("container_number"),
                result["extracted"].get("carrier"),
                result["extracted"].get("origin_locode"),
                result["extracted"].get("destination_locode"),
                result["extracted"].get("hs_code"),
                result["action_code"],
                result["severity"],
                int(result["discrepancies_count"]),
                float(result["metrics"].get("freight_variance_usd", 0.0)),
                float(result["metrics"].get("weight_variance_mt", 0.0)),
                json.dumps(result),
                result["audited_at_utc"],
            ),
        )
        conn.commit()


def list_audits(limit: int = 50) -> List[Dict[str, Any]]:
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT audit_ref, filename, doc_type, language_detected, po_number,
                   container_number, carrier, origin_locode, destination_locode,
                   hs_code, action_code, severity, discrepancies_count,
                   freight_variance_usd, weight_variance_mt, audited_at
            FROM audit_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_latest_unique_audits() -> List[Dict[str, Any]]:
    """Returns only the latest audit per unique (filename, po_number) to prevent metric double-counting."""
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT a.audit_ref, a.filename, a.doc_type, a.language_detected, a.po_number,
                   a.container_number, a.carrier, a.origin_locode, a.destination_locode,
                   a.hs_code, a.action_code, a.severity, a.discrepancies_count,
                   a.freight_variance_usd, a.weight_variance_mt, a.payload_json, a.audited_at
            FROM audit_history a
            INNER JOIN (
                SELECT filename, COALESCE(po_number, '') AS po_key, MAX(id) AS max_id
                FROM audit_history
                GROUP BY filename, COALESCE(po_number, '')
            ) latest ON a.id = latest.max_id
            ORDER BY a.id DESC
            """
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                payload = json.loads(d.pop("payload_json", "{}"))
                d["cargo_variance_usd"] = float(payload.get("metrics", {}).get("cargo_variance_usd", 0.0))
                d["risk_score"] = int(payload.get("fraud_risk", {}).get("score", 0))
            except Exception:
                d["cargo_variance_usd"] = 0.0
                d["risk_score"] = 0
            out.append(d)
        return out


def get_audit_by_ref(audit_ref: str) -> Optional[Dict[str, Any]]:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT payload_json FROM audit_history WHERE audit_ref = ?", (audit_ref,)
        ).fetchone()
        return json.loads(row["payload_json"]) if row else None
