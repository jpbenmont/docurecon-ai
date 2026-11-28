"""
DocuRecon AI — Automated Unit & FastAPI Integration Test Suite (v4.0)
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
from engine.parser import compute_iso6346_check_digit, validate_iso6346_container


class TestDocuReconAI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_01_official_wco_hs_and_un_locode_loaded(self):
        hs = lookup_hs_code("0207.12")
        self.assertIsNotNone(hs)
        self.assertIn("fowls", hs["description"].lower())

        hs_urea = lookup_hs_code("3102.10")
        self.assertIsNotNone(hs_urea)
        self.assertIn("urea", hs_urea["description"].lower())

        port_lua = lookup_locode("AOLAD")
        self.assertIsNotNone(port_lua)
        self.assertEqual(port_lua["country"], "AO")

        port_sha = lookup_locode("CNSGH")
        self.assertIsNotNone(port_sha)
        self.assertEqual(port_sha["country"], "CN")

        port_nvs = lookup_locode("RUNVS")
        self.assertIsNotNone(port_nvs)
        self.assertEqual(port_nvs["country"], "RU")

    def test_02_health_and_deduplicated_overview(self):
        h = self.client.get("/api/health")
        self.assertEqual(h.status_code, 200)
        self.assertGreater(h.json()["wco_hs_codes"], 6000)
        self.assertGreater(h.json()["un_seaports"], 15000)

        ov1 = self.client.get("/api/overview").json()
        self.assertGreaterEqual(ov1["summary"]["total_audited"], 7)

        # Re-auditing the same sample must NOT double-count financial leakage
        self.client.post("/api/reconcile/sample/BOL_BRSSZ_AOLAD_8841.pdf")
        ov2 = self.client.get("/api/overview").json()
        self.assertEqual(
            ov1["summary"]["overcharges_prevented_usd"],
            ov2["summary"]["overcharges_prevented_usd"],
        )

    def test_03_reconcile_english_bol_catches_weight_and_freight(self):
        resp = self.client.post("/api/reconcile/sample/BOL_BRSSZ_AOLAD_8841.pdf")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["extracted"]["language"], "English")
        self.assertEqual(data["action_code"], "HOLD_FOR_AMENDMENT")
        self.assertEqual(data["discrepancies_count"], 2)
        self.assertTrue(data["extracted"]["container_iso6346"]["valid"])
        self.assertAlmostEqual(data["metrics"]["freight_variance_usd"], 750.0, places=1)
        self.assertAlmostEqual(data["metrics"]["weight_variance_mt"], 0.80, places=2)

    def test_04_reconcile_portuguese_green_and_portuguese_discrepancy(self):
        green = self.client.post("/api/reconcile/sample/FATURA_COMERCIAL_BRPNG_AOLAD_9104.pdf").json()
        self.assertEqual(green["extracted"]["language"], "Portuguese")
        self.assertEqual(green["action_code"], "APPROVE_GREEN_CHANNEL")
        self.assertEqual(green["discrepancies_count"], 0)

        # Portuguese discrepancy sample with EU decimals (5.120,00 & 38.900,00) + PT Debit Note
        disc = self.client.post("/api/reconcile/sample/FATURA_DISCREPANCIA_BRSSZ_AOLAD_9318.pdf").json()
        self.assertEqual(disc["extracted"]["language"], "Portuguese")
        self.assertEqual(disc["action_code"], "HOLD_FOR_AMENDMENT")
        self.assertAlmostEqual(disc["metrics"]["freight_variance_usd"], 620.0, places=1)
        self.assertAlmostEqual(disc["metrics"]["cargo_variance_usd"], 3400.0, places=1)
        self.assertIn("NOTA DE DÉBITO", disc["dispute_notice"]["subject"])

    def test_05_reconcile_french_bordereau_thermal_quarantine(self):
        resp = self.client.post("/api/reconcile/sample/BORDEREAU_CNSGH_AOLAD_7729.pdf")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["extracted"]["language"], "French")
        self.assertEqual(data["action_code"], "QUARANTINE_VET_INSPECTION")
        self.assertEqual(data["discrepancies_count"], 1)

    def test_06_reconcile_india_uae_and_dmcc_urea_fertilizer(self):
        resp = self.client.post("/api/reconcile/sample/INV_INNSA_AEJEA_4410.pdf")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["extracted"]["declared_weight_mt"], 25.0)
        self.assertEqual(data["action_code"], "HOLD_FOR_AMENDMENT")
        self.assertEqual(data["discrepancies_count"], 2)

        urea = self.client.post("/api/reconcile/sample/INV_RUNVS_AEJEA_6042.pdf").json()
        self.assertEqual(urea["action_code"], "APPROVE_GREEN_CHANNEL")
        self.assertEqual(urea["extracted"]["hs_code"], "310210")

    def test_07_po_crud_and_iso6346_validation(self):
        cd = compute_iso6346_check_digit("MSKU111222")
        self.assertIsNotNone(cd)
        good_id = f"MSKU 111222-{cd}"
        bad_id = f"MSKU 111222-{(cd + 1) % 10}"
        self.assertTrue(validate_iso6346_container(good_id)["valid"])
        self.assertFalse(validate_iso6346_container(bad_id)["valid"])

        po_payload = {
            "po_number": "PO-TEST-2026-9999",
            "vendor_name": "Test Exporter Corp",
            "commodity": "Frozen Whole Poultry",
            "hs_code": "020712",
            "origin_locode": "BRSSZ",
            "destination_locode": "AEJEA",
            "approved_weight_mt": 20.0,
            "approved_freight_usd": 4000.0,
            "approved_cargo_usd": 42000.0,
            "max_temp_celsius": -18.0,
            "incoterms": "CIF",
        }
        create_resp = self.client.post("/api/purchase-orders", json=po_payload)
        self.assertEqual(create_resp.status_code, 200)

        manifest_txt = (
            "BILL OF LADING\n"
            "Carrier: Maersk Line\n"
            "PO Reference: PO-TEST-2026-9999\n"
            f"Container No: {good_id}\n"
            "Shipper: Test Exporter Corp\n"
            "Port of Loading: Santos (BRSSZ)\n"
            "Port of Discharge: Jebel Ali (AEJEA)\n"
            "HS Tariff Code: 0207.12\n"
            "Gross Weight: 20.00 MT\n"
            "Total Ocean Freight: USD 4,000.00\n"
            "Cargo Value: USD 42,000.00\n"
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

    def test_09_hierarchy_wco_nlp_multilingual_classifier(self):
        # Test English beef, Urea fertilizer, and Portuguese frango congelado
        for desc, expected_code in [
            ("Frozen boneless beef forequarters 25kg", "020230"),
            ("Granular Urea 46% Nitrogen Fertilizer 50kg", "310210"),
            ("Frango congelado inteiro 1200g", "020712"),
        ]:
            clf = self.client.post("/api/classify-hs", json={"commodity_description": desc, "top_k": 3})
            self.assertEqual(clf.status_code, 200)
            preds = clf.json()["predictions"]
            self.assertEqual(len(preds), 3)
            self.assertIn(expected_code, [p["hscode"] for p in preds], f"Failed for '{desc}': {preds}")

    def test_10_trade_ai_copilot(self):
        cop = self.client.post("/api/copilot", json={"query": "Show all freight overcharges to recover"})
        self.assertEqual(cop.status_code, 200)
        self.assertIn("Freight Overcharge", cop.json()["answer"])

        cop_hs = self.client.post("/api/copilot", json={"query": "Classify granular urea fertilizer"})
        self.assertEqual(cop_hs.status_code, 200)
        self.assertIn("3102", cop_hs.json()["answer"])


if __name__ == "__main__":
    unittest.main()
