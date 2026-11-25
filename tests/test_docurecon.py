"""
DocuRecon AI — Automated Unit & FastAPI Integration Test Suite
Run via: python -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import app
from engine.database import lookup_hs_code, lookup_locode


class TestDocuReconAI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_01_official_wco_hs_and_un_locode_loaded(self):
        hs = lookup_hs_code("0207.12")
        self.assertIsNotNone(hs)
        self.assertIn("fowls", hs["description"].lower())

        port_lua = lookup_locode("AOLAD")
        self.assertIsNotNone(port_lua)
        self.assertEqual(port_lua["country"], "AO")

        port_sha = lookup_locode("CNSGH")
        self.assertIsNotNone(port_sha)
        self.assertEqual(port_sha["country"], "CN")

    def test_02_health_and_overview(self):
        h = self.client.get("/api/health")
        self.assertEqual(h.status_code, 200)
        self.assertGreater(h.json()["wco_hs_codes"], 6000)
        self.assertGreater(h.json()["un_seaports"], 15000)

        ov = self.client.get("/api/overview")
        self.assertEqual(ov.status_code, 200)
        self.assertGreaterEqual(ov.json()["summary"]["total_audited"], 5)

    def test_03_reconcile_english_bol_catches_weight_and_freight(self):
        resp = self.client.post("/api/reconcile/sample/BOL_BRSSZ_AOLAD_8841.pdf")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["extracted"]["language"], "English")
        self.assertEqual(data["action_code"], "HOLD_FOR_AMENDMENT")
        self.assertEqual(data["discrepancies_count"], 2)
        self.assertAlmostEqual(data["metrics"]["freight_variance_usd"], 750.0, places=1)
        self.assertAlmostEqual(data["metrics"]["weight_variance_mt"], 0.80, places=2)

    def test_04_reconcile_portuguese_invoice_green_channel(self):
        resp = self.client.post("/api/reconcile/sample/FATURA_COMERCIAL_BRPNG_AOLAD_9104.pdf")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["extracted"]["language"], "Portuguese")
        self.assertEqual(data["action_code"], "APPROVE_GREEN_CHANNEL")
        self.assertEqual(data["discrepancies_count"], 0)

    def test_05_reconcile_french_bordereau_thermal_quarantine(self):
        resp = self.client.post("/api/reconcile/sample/BORDEREAU_CNSGH_AOLAD_7729.pdf")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["extracted"]["language"], "French")
        self.assertEqual(data["action_code"], "QUARANTINE_VET_INSPECTION")
        self.assertEqual(data["discrepancies_count"], 1)

    def test_06_reconcile_india_uae_catches_hs_tariff_mismatch_and_kg_conversion(self):
        resp = self.client.post("/api/reconcile/sample/INV_INNSA_AEJEA_4410.pdf")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["extracted"]["declared_weight_mt"], 25.0)
        self.assertEqual(data["action_code"], "HOLD_FOR_AMENDMENT")
        self.assertEqual(data["discrepancies_count"], 2)

    def test_07_po_crud_and_live_upload_reconciliation(self):
        po_payload = {
            "po_number": "PO-TEST-2026-9999",
            "vendor_name": "Test Exporter Corp",
            "commodity": "Frozen Whole Poultry",
            "hs_code": "020712",
            "origin_locode": "BRSSZ",
            "destination_locode": "AEJEA",
            "approved_weight_mt": 20.0,
            "approved_freight_usd": 4000.0,
            "max_temp_celsius": -18.0,
            "incoterms": "CIF",
        }
        create_resp = self.client.post("/api/purchase-orders", json=po_payload)
        self.assertEqual(create_resp.status_code, 200)

        manifest_txt = (
            "BILL OF LADING\n"
            "Carrier: Maersk Line\n"
            "PO Reference: PO-TEST-2026-9999\n"
            "Container No: MSKU 111222-3\n"
            "Shipper: Test Exporter Corp\n"
            "Port of Loading: Santos (BRSSZ)\n"
            "Port of Discharge: Jebel Ali (AEJEA)\n"
            "HS Tariff Code: 0207.12\n"
            "Gross Weight: 20.00 MT\n"
            "Total Ocean Freight: USD 4,000.00\n"
            "Reefer Temperature: -19.0 C\n"
        ).encode("utf-8")

        up_resp = self.client.post(
            "/api/reconcile/upload",
            files={"file": ("test_bol.txt", manifest_txt, "text/plain")},
        )
        self.assertEqual(up_resp.status_code, 200)
        audit = up_resp.json()
        self.assertEqual(audit["action_code"], "APPROVE_GREEN_CHANNEL")

        sap_resp = self.client.get(f"/api/audits/{audit['audit_ref']}/sap-json")
        self.assertEqual(sap_resp.status_code, 200)
        self.assertEqual(sap_resp.json()["SAP_IDOC_TYPE"], "INVOIC02_CUSTOMS_RECON")

        del_resp = self.client.delete("/api/purchase-orders/PO-TEST-2026-9999")
        self.assertEqual(del_resp.status_code, 200)

    def test_08_reference_search_endpoints(self):
        hs_resp = self.client.get("/api/reference/hs?q=mackerel")
        self.assertEqual(hs_resp.status_code, 200)
        self.assertGreater(len(hs_resp.json()["results"]), 0)

        port_resp = self.client.get("/api/reference/ports?q=Luanda")
        self.assertEqual(port_resp.status_code, 200)
        self.assertTrue(any(p["locode"] == "AOLAD" for p in port_resp.json()["results"]))

    def test_09_wco_nlp_tariff_classifier_and_fraud_risk_scorer(self):
        clf = self.client.post(
            "/api/classify-hs",
            json={"commodity_description": "Frozen boneless beef forequarters 25kg", "top_k": 3},
        )
        self.assertEqual(clf.status_code, 200)
        preds = clf.json()["predictions"]
        self.assertEqual(len(preds), 3)
        self.assertIn("020230", [p["hscode"] for p in preds])

        # Verify India-UAE broken vs milled rice triggers high fraud risk + debit note
        inv = self.client.post("/api/reconcile/sample/INV_INNSA_AEJEA_4410.pdf").json()
        self.assertGreaterEqual(inv["fraud_risk"]["score"], 35)
        self.assertEqual(inv["dispute_notice"]["required"], "yes")
        self.assertIn("PO-DXB-2026-4410", inv["dispute_notice"]["body"])

    def test_10_trade_ai_copilot_and_batch_upload(self):
        cop = self.client.post("/api/copilot", json={"query": "Show all freight overcharges to recover"})
        self.assertEqual(cop.status_code, 200)
        self.assertIn("Freight Overcharge", cop.json()["answer"])

        cop_hs = self.client.post("/api/copilot", json={"query": "Classify frozen atlantic mackerel fish"})
        self.assertEqual(cop_hs.status_code, 200)
        self.assertIn("0303", cop_hs.json()["answer"])


if __name__ == "__main__":
    unittest.main()
