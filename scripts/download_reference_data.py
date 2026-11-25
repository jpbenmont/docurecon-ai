"""
DocuRecon AI — Official Trade Reference Data Downloader & SQLite Builder
Downloads live official open-data tables from GitHub:
  1. WCO Harmonized System (HS) Tariff Codes (datasets/harmonized-system)
  2. UN/LOCODE Global Maritime Seaports (datasets/un-locode)
Builds a queryable SQLite database (data/docurecon.db) with Purchase Orders,
official HS codes, official UN/LOCODE seaports, and persistent Audit History.
"""

import csv
import io
import os
import sqlite3
import urllib.request
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
DB_PATH = os.path.join(DATA_DIR, "docurecon.db")

HS_URL = "https://raw.githubusercontent.com/datasets/harmonized-system/master/data/harmonized-system.csv"
LOCODE_URL = "https://raw.githubusercontent.com/datasets/un-locode/main/data/code-list.csv"

HS_RAW = os.path.join(RAW_DIR, "harmonized-system.csv")
LOCODE_RAW = os.path.join(RAW_DIR, "un-locode-list.csv")

SEED_PURCHASE_ORDERS = [
    {
        "po_number": "PO-LUA-2026-8841",
        "vendor_name": "JBS S.A. (Brazil)",
        "commodity": "Frozen Whole Poultry 1200g (Sadia)",
        "hs_code": "020712",
        "origin_locode": "BRSSZ",
        "destination_locode": "AOLAD",
        "approved_weight_mt": 26.00,
        "approved_freight_usd": 5450.00,
        "approved_cargo_usd": 44100.00,
        "max_temp_celsius": -18.0,
        "incoterms": "CIF",
        "status": "OPEN",
    },
    {
        "po_number": "PO-LUA-2026-9104",
        "vendor_name": "Marfrig Global Foods S.A.",
        "commodity": "Frozen Boneless Beef Forequarters 25kg",
        "hs_code": "020230",
        "origin_locode": "BRPNG",
        "destination_locode": "AOLAD",
        "approved_weight_mt": 27.50,
        "approved_freight_usd": 6100.00,
        "approved_cargo_usd": 115500.00,
        "max_temp_celsius": -18.0,
        "incoterms": "CIF",
        "status": "OPEN",
    },
    {
        "po_number": "PO-LUA-2026-7729",
        "vendor_name": "Shanghai Oceanic Cold Logistics",
        "commodity": "Frozen Atlantic Horse Mackerel 20kg",
        "hs_code": "030354",
        "origin_locode": "CNSGH",
        "destination_locode": "AOLAD",
        "approved_weight_mt": 28.00,
        "approved_freight_usd": 4900.00,
        "approved_cargo_usd": 55440.00,
        "max_temp_celsius": -18.0,
        "incoterms": "CFR",
        "status": "OPEN",
    },
    {
        "po_number": "PO-DXB-2026-4410",
        "vendor_name": "Maharashtra Agro Exports Pvt. Ltd.",
        "commodity": "Semi-Milled or Wholly Milled Parboiled Rice 25kg",
        "hs_code": "100630",
        "origin_locode": "INNSA",
        "destination_locode": "AEJEA",
        "approved_weight_mt": 25.00,
        "approved_freight_usd": 2150.00,
        "approved_cargo_usd": 18750.00,
        "max_temp_celsius": 25.0,
        "incoterms": "FOB",
        "status": "OPEN",
    },
    {
        "po_number": "PO-LOS-2026-5520",
        "vendor_name": "FrieslandCampina Export B.V.",
        "commodity": "Whole Milk Powder (Fat > 1.5%) 25kg",
        "hs_code": "040221",
        "origin_locode": "NLRTM",
        "destination_locode": "NGLOS",
        "approved_weight_mt": 24.00,
        "approved_freight_usd": 3850.00,
        "approved_cargo_usd": 79200.00,
        "max_temp_celsius": 22.0,
        "incoterms": "CIF",
        "status": "OPEN",
    },
]


def _download(url: str, dest: str, force: bool = False) -> str:
    os.makedirs(RAW_DIR, exist_ok=True)
    if os.path.exists(dest) and not force:
        size_kb = os.path.getsize(dest) / 1024
        print(f"[INFO] Using cached file: {dest} ({size_kb:.1f} KB)")
        return dest

    print(f"[DOWNLOAD] Fetching {url} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "DocuRecon-AI/2.0"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            out.write(chunk)
    size_kb = os.path.getsize(dest) / 1024
    print(f"[OK] Saved {dest} ({size_kb:.1f} KB)")
    return dest


def build_database(force_download: bool = False) -> dict:
    hs_path = _download(HS_URL, HS_RAW, force=force_download)
    locode_path = _download(LOCODE_URL, LOCODE_RAW, force=force_download)

    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE hs_codes (
            hscode TEXT PRIMARY KEY,
            section TEXT,
            description TEXT NOT NULL,
            parent TEXT,
            level INTEGER
        )
        """
    )
    cur.execute("CREATE INDEX idx_hs_desc ON hs_codes(description)")

    cur.execute(
        """
        CREATE TABLE un_locodes (
            locode TEXT PRIMARY KEY,
            country TEXT NOT NULL,
            location TEXT NOT NULL,
            name TEXT NOT NULL,
            subdivision TEXT,
            status TEXT,
            function_code TEXT,
            is_seaport INTEGER NOT NULL,
            coordinates TEXT
        )
        """
    )
    cur.execute("CREATE INDEX idx_locode_name ON un_locodes(name)")
    cur.execute("CREATE INDEX idx_locode_country ON un_locodes(country)")

    cur.execute(
        """
        CREATE TABLE purchase_orders (
            po_number TEXT PRIMARY KEY,
            vendor_name TEXT NOT NULL,
            commodity TEXT NOT NULL,
            hs_code TEXT NOT NULL,
            origin_locode TEXT NOT NULL,
            destination_locode TEXT NOT NULL,
            approved_weight_mt REAL NOT NULL,
            approved_freight_usd REAL NOT NULL,
            approved_cargo_usd REAL NOT NULL,
            max_temp_celsius REAL NOT NULL,
            incoterms TEXT DEFAULT 'CIF',
            status TEXT DEFAULT 'OPEN',
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE audit_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audit_ref TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            language_detected TEXT,
            po_number TEXT,
            container_number TEXT,
            carrier TEXT,
            origin_locode TEXT,
            destination_locode TEXT,
            hs_code TEXT,
            action_code TEXT NOT NULL,
            severity TEXT NOT NULL,
            discrepancies_count INTEGER NOT NULL,
            freight_variance_usd REAL DEFAULT 0.0,
            weight_variance_mt REAL DEFAULT 0.0,
            payload_json TEXT NOT NULL,
            audited_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE reference_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    # 1. Load WCO HS Codes
    hs_rows = []
    with open(hs_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("hscode") or "").strip()
            if not code:
                continue
            hs_rows.append(
                (
                    code,
                    (row.get("section") or "").strip(),
                    (row.get("description") or "").strip(),
                    (row.get("parent") or "").strip(),
                    int(row["level"]) if (row.get("level") or "").isdigit() else 0,
                )
            )
    cur.executemany(
        "INSERT OR REPLACE INTO hs_codes (hscode, section, description, parent, level) VALUES (?, ?, ?, ?, ?)",
        hs_rows,
    )

    # 2. Load UN/LOCODE Maritime Seaports (Function[0] == '1')
    port_rows = []
    total_locodes = 0
    with open(locode_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_locodes += 1
            country = (row.get("Country") or "").strip()
            loc = (row.get("Location") or "").strip()
            func = (row.get("Function") or "").strip()
            if not country or not loc:
                continue
            is_seaport = 1 if func.startswith("1") else 0
            # Store all seaports plus major hubs to keep SQLite fast & lightweight
            if is_seaport:
                port_rows.append(
                    (
                        f"{country}{loc}",
                        country,
                        loc,
                        (row.get("NameWoDiacritics") or row.get("Name") or "").strip(),
                        (row.get("Subdivision") or "").strip(),
                        (row.get("Status") or "").strip(),
                        func,
                        is_seaport,
                        (row.get("Coordinates") or "").strip(),
                    )
                )
    cur.executemany(
        """
        INSERT OR REPLACE INTO un_locodes
        (locode, country, location, name, subdivision, status, function_code, is_seaport, coordinates)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        port_rows,
    )

    # 3. Seed SAP Purchase Orders
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur.executemany(
        """
        INSERT INTO purchase_orders
        (po_number, vendor_name, commodity, hs_code, origin_locode, destination_locode,
         approved_weight_mt, approved_freight_usd, approved_cargo_usd, max_temp_celsius,
         incoterms, status, created_at)
        VALUES (:po_number, :vendor_name, :commodity, :hs_code, :origin_locode, :destination_locode,
                :approved_weight_mt, :approved_freight_usd, :approved_cargo_usd, :max_temp_celsius,
                :incoterms, :status, :created_at)
        """,
        [{**po, "created_at": now_iso} for po in SEED_PURCHASE_ORDERS],
    )

    metadata = {
        "hs_source_url": HS_URL,
        "hs_code_count": str(len(hs_rows)),
        "locode_source_url": LOCODE_URL,
        "locode_total_scanned": str(total_locodes),
        "seaport_count": str(len(port_rows)),
        "ingested_at_utc": now_iso,
    }
    cur.executemany(
        "INSERT INTO reference_metadata (key, value) VALUES (?, ?)",
        list(metadata.items()),
    )

    conn.commit()
    conn.close()

    print(f"[OK] Built SQLite reference DB at {DB_PATH}")
    print(f"     WCO HS Codes: {len(hs_rows):,} | Official UN Seaports: {len(port_rows):,} | Seed POs: {len(SEED_PURCHASE_ORDERS)}")
    return metadata


if __name__ == "__main__":
    build_database()
