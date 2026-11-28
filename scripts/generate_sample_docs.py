"""
DocuRecon AI — Pure-Python Sample PDF & Manifest Generator (v4.0)
Generates 7 realistic multilingual trade documents (EN, PT, FR) including:
  - Brazil -> Angola poultry BOL (weight + freight surcharge)
  - Brazil -> Angola beef Fatura Comercial (Portuguese Green Channel)
  - Brazil -> Angola sunflower oil Fatura Comercial (Portuguese Overcharge + FOB Fraud -> PT Debit Note!)
  - China -> Angola mackerel French Bordereau (Thermal Breach Quarantine)
  - India -> UAE parboiled rice Invoice (KG->MT conversion + HS mismatch + surcharge)
  - Russia (Novorossiysk) -> UAE (Jebel Ali) Granular Urea 46% N Fertilizer Invoice (Green Channel)
  - Netherlands -> Nigeria Whole Milk Powder TXT Manifest (Green Channel)
Every container ID uses a mathematically valid ISO 6346 check digit.
"""

import os
import sys
from typing import List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from engine.parser import compute_iso6346_check_digit

SAMPLE_DIR = os.path.join(BASE_DIR, "sample_docs")
os.makedirs(SAMPLE_DIR, exist_ok=True)


def _iso_container(prefix10: str) -> str:
    cd = compute_iso6346_check_digit(prefix10)
    return f"{prefix10[:4]} {prefix10[4:10]}-{cd}"


def _escape_pdf_text(text: str) -> str:
    replacements = {
        "°": " deg ",
        "ã": "a",
        "ç": "c",
        "é": "e",
        "è": "e",
        "ê": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "Ã": "A",
        "Ç": "C",
        "É": "E",
        "—": "-",
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_simple_pdf(filepath: str, title: str, lines: List[str]) -> None:
    """Writes a clean, standards-compliant PDF 1.4 file readable by pypdf & Adobe."""
    stream_cmds = [
        "BT",
        "/F1 13 Tf",
        "48 764 Td",
        f"({_escape_pdf_text(title)}) Tj",
        "/F1 10 Tf",
        "0 -26 Td",
    ]
    for line in lines:
        stream_cmds.append(f"({_escape_pdf_text(line)}) Tj")
        stream_cmds.append("0 -18 Td")
    stream_cmds.append("ET")
    stream_bytes = "\n".join(stream_cmds).encode("latin-1", errors="replace")

    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        f"5 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii")
        + stream_bytes
        + b"\nendstream\nendobj\n",
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)

    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        pdf.extend(f"{off:010d} 00000 n \n".encode("ascii"))

    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode(
            "ascii"
        )
    )

    with open(filepath, "wb") as f:
        f.write(pdf)


PDF_SAMPLES = [
    (
        "BOL_BRSSZ_AOLAD_8841.pdf",
        "MARITIME OCEAN BILL OF LADING (ORIGINAL)",
        [
            "Carrier: MSC Mediterranean Shipping Company S.A.",
            "Vessel / Voyage: MSC CAPUCINE / VOY 241A",
            "PO Reference: PO-LUA-2026-8841",
            f"Container No: {_iso_container('MSCU892341')}",
            "Shipper: JBS S.A. (Brazil)",
            "Port of Loading: Santos, Brazil (BRSSZ)",
            "Port of Discharge: Luanda, Angola (AOLAD)",
            "Incoterms: CIF",
            "HS Tariff Code: 0207.12",
            "Description of Goods: Frozen Whole Poultry 1200g (Sadia Export Grade)",
            "Gross Weight: 26.80 MT",
            "Total Ocean Freight: USD 6,200.00 (Includes unapproved BAF & Congestion Surcharge)",
            "Cargo Value: USD 44,100.00",
            "Reefer Temperature: -18.4 C",
        ],
    ),
    (
        "FATURA_COMERCIAL_BRPNG_AOLAD_9104.pdf",
        "FATURA COMERCIAL DE EXPORTACAO (COMMERCIAL INVOICE)",
        [
            "Transportadora: CMA CGM Shipping Line",
            "Navio: CMA CGM BENGUELA / VOY 118W",
            "Pedido de Compra: PO-LUA-2026-9104",
            f"Contentor: {_iso_container('CMAU452109')}",
            "Exportador: Marfrig Global Foods S.A.",
            "Porto de Embarque: Paranagua, Brazil (BRPNG)",
            "Porto de Descarga: Luanda, Angola (AOLAD)",
            "Incoterms: CIF",
            "NCM / HS: 0202.30",
            "Mercadoria: Frozen Boneless Beef Forequarters 25kg Cartons",
            "Peso Bruto: 27.50 MT",
            "Frete Maritimo: USD 6,100.00",
            "Valor FOB: USD 115,500.00",
            "Temperatura Reefer: -19.2 C",
        ],
    ),
    (
        "FATURA_DISCREPANCIA_BRSSZ_AOLAD_9318.pdf",
        "FATURA COMERCIAL DE EXPORTACAO - ANGOLA LUANDA",
        [
            "Transportadora: Maersk Line S.A.",
            "Navio: MAERSK LUANDA / VOY 309S",
            "Pedido de Compra: PO-LUA-2026-9318",
            f"Contentor: {_iso_container('MRKU741825')}",
            "Exportador: Bunge Alimentos S.A. (Brazil)",
            "Porto de Embarque: Santos, Brazil (BRSSZ)",
            "Porto de Descarga: Luanda, Angola (AOLAD)",
            "Incoterms: CIF",
            "NCM / HS: 1512.19",
            "Mercadoria: Oleo de Girassol Refinado 20L (Refined Sunflower Oil)",
            "Peso Bruto: 22,00 MT",
            "Frete Maritimo: USD 5.120,00",
            "Valor FOB: USD 38.900,00",
            "Temperatura Reefer: 21.0 C",
        ],
    ),
    (
        "BORDEREAU_CNSGH_AOLAD_7729.pdf",
        "BORDEREAU D'EXPEDITION & LISTE DE COLISAGE",
        [
            "Armateur: COSCO Shipping Lines",
            "Navire: COSCO GUANGZHOU / VOY 092E",
            "Bon de Commande: PO-LUA-2026-7729",
            f"Conteneur: {_iso_container('COSU901423')}",
            "Expediteur: Shanghai Oceanic Cold Logistics",
            "Port d'Embarquement: Shanghai, China (CNSGH)",
            "Port de Dechargement: Luanda, Angola (AOLAD)",
            "Incoterms: CFR",
            "Code SH: 0303.54",
            "Marchandise: Frozen Atlantic Horse Mackerel 20kg",
            "Poids Brut: 28.00 MT",
            "Fret Maritime: USD 4,900.00",
            "Valeur Marchandise: USD 55,440.00",
            "Temperature Conteneur: -15.4 C",
        ],
    ),
    (
        "INV_INNSA_AEJEA_4410.pdf",
        "COMMERCIAL EXPORT INVOICE - INDIA TO UAE",
        [
            "Carrier: Maersk Line A/S",
            "Vessel / Voyage: MAERSK SENTOSA / VOY 405W",
            "PO Reference: PO-DXB-2026-4410",
            f"Container No: {_iso_container('MSKU661204')}",
            "Exporter: Maharashtra Agro Exports Pvt. Ltd.",
            "Port of Loading: Nhava Sheva, India (INNSA)",
            "Port of Discharge: Jebel Ali, UAE (AEJEA)",
            "Incoterms: FOB",
            "HS Tariff Code: 1006.40",
            "Commodity: Parboiled Long-Grain Rice 25kg PP Bags",
            "Gross Weight: 25,000 KG",
            "Total Ocean Freight: USD 2,530.00",
            "Cargo Value: USD 18,750.00",
            "Container Temp: 22.0 C",
        ],
    ),
    (
        "INV_RUNVS_AEJEA_6042.pdf",
        "COMMERCIAL EXPORT INVOICE - BULK GRANULAR UREA FERTILIZER",
        [
            "Carrier: MSC Mediterranean Shipping Company",
            "Vessel / Voyage: MSC EMIRATES / VOY 512E",
            "PO Reference: PO-DXB-2026-6042",
            f"Container No: {_iso_container('MEDU518904')}",
            "Exporter: EuroChem Trading DMCC",
            "Port of Loading: Novorossiysk, Russia (RUNVS)",
            "Port of Discharge: Jebel Ali, UAE (AEJEA)",
            "Incoterms: CFR",
            "HS Tariff Code: 3102.10",
            "Commodity: Granular Urea 46% Nitrogen Fertilizer 50kg PP Bags",
            "Gross Weight: 27.00 MT",
            "Total Ocean Freight: USD 3,400.00",
            "Cargo Value: USD 10,800.00",
            "Container Temp: 24.0 C",
        ],
    ),
]

TXT_SAMPLE = f"""MARITIME CARGO MANIFEST - NORTH EUROPE TO WEST AFRICA
Carrier: Hapag-Lloyd AG
Vessel / Voyage: EXPRESS ROME / VOY 314S
PO Reference: PO-LOS-2026-5520
Container No: {_iso_container('HLXU330918')}
Shipper: FrieslandCampina Export B.V.
Port of Loading: Rotterdam, Netherlands (NLRTM)
Port of Discharge: Lagos, Nigeria (NGLOS)
Incoterms: CIF
HS Tariff Code: 0402.21
Commodity: Whole Milk Powder (Fat > 1.5%) 25kg Kraft Bags
Gross Weight: 24.00 MT
Total Ocean Freight: USD 3,850.00
Cargo Value: USD 79,200.00
Container Temp: 19.5 C
"""


def build_samples():
    for filename, title, lines in PDF_SAMPLES:
        out_path = os.path.join(SAMPLE_DIR, filename)
        write_simple_pdf(out_path, title, lines)
        print(f"[OK] Generated PDF: {out_path} ({os.path.getsize(out_path):,} bytes)")

    txt_path = os.path.join(SAMPLE_DIR, "MANIFEST_NLRTM_NGLOS_5520.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(TXT_SAMPLE)
    print(f"[OK] Generated TXT: {txt_path}")


if __name__ == "__main__":
    build_samples()
