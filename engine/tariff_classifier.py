"""
DocuRecon AI — Hierarchy-Aware Multilingual WCO HS Tariff Classifier (v4.0)
Combines official 6-digit WCO Harmonized System subheadings with their parent
4-digit headings and 2-digit chapters from SQLite, then trains a dual
Word (1,2) + Char-WB (3,5) TF-IDF semantic cosine ensemble with EN/PT/FR
trade synonym normalization and automatic scikit-learn version guarding.
"""

import os
import re
import sys
from typing import Any, Dict, List
import joblib
import numpy as np
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine.database import db_conn

MODELS_DIR = os.path.join(BASE_DIR, "models")
os.makedirs(MODELS_DIR, exist_ok=True)
MODEL_PATH = os.path.join(MODELS_DIR, "wco_hs_tfidf.joblib")
ARTIFACT_VERSION = 4

# Multilingual EN / PT / FR commodity enrichment for real international trade descriptions
TRADE_SYNONYMS = {
    "poultry": "poultry fowls gallus domesticus chicken meat",
    "chicken": "chicken fowls gallus domesticus poultry meat",
    "frango": "poultry fowls gallus domesticus chicken meat frozen",
    "volaille": "poultry fowls gallus domesticus chicken meat",
    "beef": "beef bovine animals meat",
    "carne bovina": "beef bovine animals meat boneless frozen",
    "boeuf": "beef bovine animals meat",
    "mackerel": "mackerel fish scomber horse scad",
    "maquereau": "mackerel fish scomber frozen",
    "carapau": "horse mackerel fish frozen",
    "parboiled": "parboiled semi-milled wholly milled rice cereals",
    "basmati": "semi-milled wholly milled rice cereals",
    "arroz": "rice semi-milled wholly milled cereals",
    "riz": "rice semi-milled wholly milled cereals",
    "broken rice": "broken rice cereals 100640",
    "milk powder": "milk cream powder granules solid concentrated fat",
    "leite em po": "milk cream powder granules solid concentrated",
    "lait en poudre": "milk cream powder granules solid concentrated",
    "sunflower oil": "sunflower-seed safflower oil refined fixed vegetable",
    "oleo de girassol": "sunflower-seed safflower oil refined vegetable",
    "huile de tournesol": "sunflower-seed safflower oil refined vegetable",
    "palm oil": "palm oil refined crude vegetable",
    "urea": "urea nitrogenous fertilizers mineral chemical",
    "ureia": "urea nitrogenous fertilizers mineral chemical",
    "uree": "urea nitrogenous fertilizers mineral chemical",
    "fertilizer": "fertilizers mineral chemical nitrogenous phosphatic potassic",
    "fertilizante": "fertilizers mineral chemical nitrogenous",
    "engrais": "fertilizers mineral chemical nitrogenous",
    "wheat flour": "wheat meslin flour cereals",
    "farinha de trigo": "wheat meslin flour cereals",
    "sugar": "cane beet sugar sucrose refined",
    "acucar": "cane beet sugar sucrose refined",
    "congelado": "frozen",
    "congele": "frozen",
    "desossada": "boneless cuts",
    "refinado": "refined",
}


class WCOTariffClassifier:
    _instance = None

    def __init__(self):
        self.word_vec = None
        self.word_mat = None
        self.char_vec = None
        self.char_mat = None
        self.codes: List[Dict[str, str]] = []
        self._load_or_build()

    @classmethod
    def get(cls) -> "WCOTariffClassifier":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def _load_or_build(self) -> None:
        if os.path.exists(MODEL_PATH):
            try:
                bundle = joblib.load(MODEL_PATH)
                if (
                    bundle.get("version") == ARTIFACT_VERSION
                    and bundle.get("sklearn_version") == sklearn.__version__
                    and "char_vec" in bundle
                ):
                    self.word_vec = bundle["word_vec"]
                    self.word_mat = bundle["word_mat"]
                    self.char_vec = bundle["char_vec"]
                    self.char_mat = bundle["char_mat"]
                    self.codes = bundle["codes"]
                    return
            except Exception:
                pass

        with db_conn() as conn:
            all_rows = conn.execute(
                "SELECT hscode, section, description, parent, level FROM hs_codes"
            ).fetchall()

        lookup = {r["hscode"]: r["description"] for r in all_rows}
        six_digit = [r for r in all_rows if len(r["hscode"]) == 6]
        six_digit.sort(key=lambda r: r["hscode"])

        self.codes = []
        corpus = []
        for r in six_digit:
            code = r["hscode"]
            heading_desc = lookup.get(code[:4], "")
            chapter_desc = lookup.get(code[:2], "")
            self.codes.append(
                {
                    "hscode": code,
                    "section": r["section"],
                    "description": r["description"],
                    "heading_description": heading_desc,
                    "chapter_description": chapter_desc,
                }
            )
            # Repeat subheading description slightly higher weight while preserving heading + chapter context
            corpus.append(
                f"{r['description']} {r['description']} {heading_desc} {chapter_desc} {code}"
            )

        self.word_vec = TfidfVectorizer(
            stop_words="english",
            ngram_range=(1, 2),
            sublinear_tf=True,
            max_features=30000,
        )
        self.word_mat = self.word_vec.fit_transform(corpus)

        self.char_vec = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(3, 5),
            sublinear_tf=True,
            max_features=45000,
        )
        self.char_mat = self.char_vec.fit_transform(corpus)

        joblib.dump(
            {
                "version": ARTIFACT_VERSION,
                "sklearn_version": sklearn.__version__,
                "word_vec": self.word_vec,
                "word_mat": self.word_mat,
                "char_vec": self.char_vec,
                "char_mat": self.char_mat,
                "codes": self.codes,
            },
            MODEL_PATH,
        )

    @staticmethod
    def _enrich_query(text: str) -> str:
        norm = (
            text.lower()
            .replace("é", "e")
            .replace("è", "e")
            .replace("ê", "e")
            .replace("á", "a")
            .replace("ã", "a")
            .replace("ó", "o")
            .replace("í", "i")
            .replace("ú", "u")
            .replace("ç", "c")
        )
        additions = []
        for key, expansion in TRADE_SYNONYMS.items():
            if re.search(rf"\b{re.escape(key)}\b", norm):
                additions.append(expansion)
        return f"{norm} {' '.join(additions)}".strip()

    def classify(self, commodity_text: str, top_k: int = 3) -> List[Dict[str, Any]]:
        if not commodity_text or not self.codes:
            return []
        enriched = self._enrich_query(commodity_text)
        w_sims = linear_kernel(self.word_vec.transform([enriched]), self.word_mat).flatten()
        c_sims = linear_kernel(self.char_vec.transform([enriched]), self.char_mat).flatten()
        sims = 0.72 * w_sims + 0.28 * c_sims

        top_idx = np.argsort(sims)[::-1][:top_k]
        results = []
        max_sim = float(sims[top_idx[0]]) if len(top_idx) else 0.0
        for rank, idx in enumerate(top_idx, start=1):
            raw_score = float(sims[idx])
            if raw_score <= 0.04:
                conf_pct = 0.0
            else:
                rel = raw_score / max(max_sim, 0.30)
                conf_pct = round(min(99.2, max(14.0, rel * 84.0 + raw_score * 24.0)), 1)
            c = self.codes[idx]
            code_fmt = f"{c['hscode'][:4]}.{c['hscode'][4:6]}"
            results.append(
                {
                    "rank": rank,
                    "hscode": c["hscode"],
                    "hscode_formatted": code_fmt,
                    "section": c["section"],
                    "description": c["description"],
                    "heading_description": c.get("heading_description", ""),
                    "cosine_score": round(raw_score, 4),
                    "confidence_pct": conf_pct,
                }
            )
        return results
