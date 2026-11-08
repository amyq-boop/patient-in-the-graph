#!/usr/bin/env python3
"""
WS2 extra patient edges, matched directly against PrimeKG node names (offline, every edge hits a real node).

  patient_drug_edges         baseline medications -> PrimeKG drug nodes
  patient_exposure_edges     ever-smoker (and optionally heavy drinker) -> PrimeKG exposure nodes
  patient_history_edges      prior non-renal cancers -> PrimeKG disease nodes
  med_mapping_review.csv     every raw medication token, what it matched and how (REVIEW the fuzzy rows)
  ws2_extra_edges_qc.json

Usage:
  python ws2_extra_edges.py --kg data/raw/kg.csv
  python ws2_extra_edges.py --kg data/raw/kg.csv --smoking-node "Tobacco Smoke Pollution"   # override exposure choice
  python ws2_extra_edges.py --kg data/raw/kg.csv --keep-periop    # keep perioperative anticoagulants
"""
import argparse
import difflib
import json
import re
from pathlib import Path

import pandas as pd

OUT = Path("data/processed")

# Brand / local name -> generic. Includes Polish brands used at the European CPTAC sites.
BRAND = {
    "zocor": "simvastatin", "lipitor": "atorvastatin", "crestor": "rosuvastatin", "colace": "docusate",
    "glycolax": "polyethylene glycol", "miralax": "polyethylene glycol", "tylenol": "acetaminophen",
    "paracetamol": "acetaminophen", "clexane": "enoxaparin", "lovenox": "enoxaparin",
    "acard": "acetylsalicylic acid", "aspirin": "acetylsalicylic acid", "polocard": "acetylsalicylic acid",
    "norco": "hydrocodone", "uloric": "febuxostat", "toprol": "metoprolol", "lopressor": "metoprolol",
    "betaloc": "metoprolol", "metocard": "metoprolol", "valium": "diazepam", "sintrom": "acenocoumarol",
    "acenokumarol": "acenocoumarol", "axtil": "ramipril", "polpril": "ramipril", "tritace": "ramipril",
    "xarelto": "rivaroxaban", "eliquis": "apixaban", "coumadin": "warfarin", "synthroid": "levothyroxine",
    "euthyrox": "levothyroxine", "letrox": "levothyroxine", "glucophage": "metformin", "siofor": "metformin",
    "lasix": "furosemide", "furosemid": "furosemide", "norvasc": "amlodipine", "amlozek": "amlodipine",
    "zestril": "lisinopril", "prinivil": "lisinopril", "flomax": "tamsulosin", "proscar": "finasteride",
    "nexium": "esomeprazole", "prilosec": "omeprazole", "controloc": "pantoprazole", "protonix": "pantoprazole",
    "plavix": "clopidogrel", "ambien": "zolpidem", "xanax": "alprazolam", "zoloft": "sertraline",
    "lexapro": "escitalopram", "neurontin": "gabapentin", "advil": "ibuprofen", "motrin": "ibuprofen",
    "aleve": "naproxen", "concor": "bisoprolol", "bisocard": "bisoprolol", "vitamin d": "cholecalciferol",
    "vitamin d3": "cholecalciferol", "vitamin b12": "cyanocobalamin", "fish oil": "omega-3-acid ethyl esters",
    "vitamin c": "ascorbic acid", "vitamin e": "vitamin e",
    "coreg": "carvedilol", "albuterol": "salbutamol", "ventolin": "salbutamol", "asa": "acetylsalicylic acid",
    "hyzaar": "losartan + hydrochlorothiazide", "imdur": "isosorbide mononitrate", "k-dur": "potassium chloride",
    "imodium": "loperamide", "apap": "acetaminophen", "prazol": "omeprazole",
    "insulin": "insulin human", "novolog": "insulin aspart", "humalog": "insulin lispro",
    "lantus": "insulin glargine", "levemir": "insulin detemir",
}
BRAND["norco"] = "hydrocodone + acetaminophen"
BRAND.update({"baby aspirin": "acetylsalicylic acid", "zofran": "ondansetron", "mobic": "meloxicam",
              "zyrtec": "cetirizine", "calan": "verapamil", "klorcon": "potassium chloride",
              "klor-con": "potassium chloride", "citracal": "calcium citrate", "calcitrate": "calcium citrate",
              "vita d": "cholecalciferol"})
FUZZY_BLOCK = {("insulin", "inulin")}
NOISE_WORDS = r"\b(po|iv|im|sc|tab|tabs|tablet|tablets|cap|caps|capsule|xl|er|sr|xr|cr|hcl|hci|prn|daily|bid|tid|" \
              r"qd|oral|inj|injection|diet|manage|prod|otc|apo)\b"
DOSE = r"\b\d+(\.\d+)?\s*(mg|mcg|g|ml|iu|units?|%)?\b"
SKIP = {"multivitamin", "multi vitamin", "vitamins", "vitamin", "supplement", "supplements", "none", "unknown",
        "nan", "lancets", "test strips", "syringes", "minerals", "mult vitamin for women", "multivitamins w",
        "blood glucose test", "glucose blood vi test strips", "refresh ophthalmic solution"}
PERIOP = {"enoxaparin", "heparin", "fondaparinux"}  # often perioperative prophylaxis -> documentation artifact
SMOKERS = re.compile(r"current smoker|reformed smoker", re.I)
HEAVY_DRINK = re.compile(r"more than 2 drinks", re.I)
RENAL = re.compile(r"renal|kidney|rcc", re.I)  # current cancer or its metastases -> leakage, exclude


def save(df, name):
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        import pyarrow  # noqa: F401
        df.to_parquet(OUT / f"{name}.parquet", index=False)
        print(f"  saved {OUT / name}.parquet  shape={df.shape}")
    except ImportError:
        df.to_csv(OUT / f"{name}.csv", index=False)
        print(f"  saved {OUT / name}.csv  shape={df.shape}")


def load_clinical():
    import cptac
    c = cptac.Ccrcc().get_dataframe("clinical", "mssm")
    c = c.loc[:, ~c.columns.duplicated()]
    c.index = c.index.astype(str)
    return c


NODE_CACHE = OUT / "primekg_nodes.csv"


def load_primekg_nodes(kg_path):
    """Unique PrimeKG nodes. Every edge appears twice in kg.csv (x->y and y->x), so the x_* columns alone
    cover every node. Built once from kg.csv, then cached as a small file (~130k rows) that loads instantly."""
    if NODE_CACHE.exists():
        nodes = pd.read_csv(NODE_CACHE, dtype=str)
        print(f"Using cached node list {NODE_CACHE} ({len(nodes)} nodes)")
    else:
        print(f"Building node list from {kg_path} (one time only)...")
        cols = ["x_index", "x_id", "x_type", "x_name", "x_source"]
        parts = []  # chunked so the 982 MB file stays light on laptop memory
        for kg in pd.read_csv(kg_path, usecols=cols, dtype=str, chunksize=1_000_000):
            parts.append(kg.drop_duplicates("x_index"))
        nodes = (pd.concat(parts).drop_duplicates("x_index")
                 .rename(columns=lambda c: c[2:]))  # x_index -> index, x_id -> id, ...
        OUT.mkdir(parents=True, exist_ok=True)
        nodes.to_csv(NODE_CACHE, index=False)
        print(f"  saved {NODE_CACHE} ({len(nodes)} nodes)")
    nodes["key"] = nodes["name"].str.lower().str.strip()
    print(f"PrimeKG nodes by type: {nodes['type'].value_counts().to_dict()}")
    return nodes


class Matcher:
    """Exact name match, then first word, then fuzzy (difflib) within one PrimeKG node type."""

    def __init__(self, nodes, node_type, cutoff=0.85):
        sub = nodes[nodes["type"] == node_type].drop_duplicates("key")
        self.by_key = sub.set_index("key")
        self.keys = list(self.by_key.index)
        self.cutoff = cutoff

    def match(self, s):
        if not s:
            return None, None, 0.0
        if s in self.by_key.index:
            return self.by_key.loc[s], "exact", 1.0
        first = s.split()[0]
        if len(first) > 3 and first in self.by_key.index:
            return self.by_key.loc[first], "first_word", 1.0
        prefixed = [k for k in self.keys if k.startswith(s + " ")]
        if len(prefixed) == 1:  # 'beclomethasone' -> 'beclomethasone dipropionate'
            return self.by_key.loc[prefixed[0]], "prefix", 1.0
        hit = difflib.get_close_matches(s, self.keys, n=1, cutoff=self.cutoff)
        if hit and self.fuzzy_ok(s, hit[0]):
            return self.by_key.loc[hit[0]], "fuzzy", difflib.SequenceMatcher(None, s, hit[0]).ratio()
        return None, None, 0.0

    @staticmethod
    def fuzzy_ok(s, h):
        """Typos keep their first two letters (ibprofen, metfomin); short last words are not typos
        (vitamin c vs vitamin e); plus an explicit blocklist (insulin vs inulin)."""
        if (s, h) in FUZZY_BLOCK or s[:2] != h[:2]:
            return False
        if len(s.split()[-1]) <= 2 or len(h.split()[-1]) <= 2:
            return False
        return True


def _clean(piece):
    p = re.sub(DOSE, " ", piece)
    p = re.sub(NOISE_WORDS, " ", p)
    p = re.sub(r"[^a-z0-9\- ]", " ", p)
    return re.sub(r"\s+", " ", p).strip(" -")


def _resolve(p):
    """One cleaned name -> list of generic names (brands expanded, hyphenated combos split)."""
    if not p or p in SKIP:
        return []
    for key in (p, p.split()[0]):
        if key in BRAND:
            return [x.strip() for x in BRAND[key].split("+")]
    if "-" in p and not re.match(r"^(co|apo)-", p):  # 'hydrocodone-acetaminophen', 'losartan-hctz'
        return [g for part in p.split("-") for g in _resolve(_clean(part))]
    return [p]


def med_candidates(token):
    """'Noliprel (indapamidum + perindoprilum)' -> ['indapamidum', 'perindoprilum'];
    'METOPROLOL & DIET MANAGE PROD PO' -> ['metoprolol']; 'Vitamin B 12' -> ['cyanocobalamin']."""
    t = token.lower().replace("_", " ").strip()
    t = re.sub(r"vitamin\s*b\s*-?\s*12", "vitamin b12", t)
    inside = re.findall(r"\(([^)]*)\)", t)
    parts = inside if inside else [re.sub(r"\([^)]*\)", " ", t)]  # generic names in parentheses beat brands
    out = []
    for part in parts:
        for piece in re.split(r"&|/|\+|\band\b|,", part):
            out += _resolve(_clean(piece))
    return [o for o in dict.fromkeys(out) if o not in SKIP and re.search(r"[a-z]{3,}", o)]


def drug_edges(clin, nodes, keep_periop):
    m = Matcher(nodes, "drug")
    meds = clin["medication_name_vitamins_supplements"] if "medication_name_vitamins_supplements" in clin else None
    rows, review = [], []
    for pid, raw in (meds.items() if meds is not None else []):
        if pd.isna(raw):
            continue
        for token in str(raw).split("|"):
            if not token.strip():
                continue
            matched_any = False
            for cand in med_candidates(token):
                node, how, score = m.match(cand)
                review.append({"patient_id": pid, "raw": token.strip(), "normalized": cand,
                               "primekg_name": None if node is None else node["name"],
                               "primekg_id": None if node is None else node["id"],
                               "method": how, "score": round(score, 2)})
                if node is not None:
                    matched_any = True
                    if node["name"].lower() in PERIOP and not keep_periop:
                        continue
                    rows.append({"patient_id": pid, "primekg_type": "drug", "primekg_index": node["index"], "primekg_id": node["id"],
                                 "primekg_name": node["name"], "curie": f"DRUGBANK:{node['id']}",
                                 "raw": token.strip(), "method": how, "score": round(score, 2)})
            if not matched_any and not med_candidates(token):
                review.append({"patient_id": pid, "raw": token.strip(), "normalized": None,
                               "primekg_name": None, "primekg_id": None, "method": "skipped", "score": 0.0})
    edges = pd.DataFrame(rows).drop_duplicates(["patient_id", "primekg_id"]) if rows else pd.DataFrame(
        columns=["patient_id", "primekg_type", "primekg_index", "primekg_id", "primekg_name", "curie", "raw", "method", "score"])
    return edges, pd.DataFrame(review, columns=["patient_id", "raw", "normalized", "primekg_name", "primekg_id",
                                                "method", "score"])


def pick_exposure(nodes, override, patterns):
    exp = nodes[nodes["type"] == "exposure"]
    if override:
        hit = exp[exp["key"] == override.lower()]
        return (hit.iloc[0] if len(hit) else None), exp
    for pat in patterns:
        hit = exp[exp["key"].str.contains(pat, regex=True, na=False)]
        if len(hit):
            return hit.iloc[0], exp
    return None, exp


def exposure_edges(clin, nodes, smoking_override, alcohol_mode):
    rows, chosen = [], {}
    smk_node, exp = pick_exposure(nodes, smoking_override, [r"^tobacco smoke", r"smoking", r"tobacco", r"nicotine"])
    cand = exp[exp["key"].str.contains("tobacco|smok|nicotin|ethanol|alcohol", na=False)]["name"].tolist()
    print(f"PrimeKG exposure nodes mentioning tobacco/smoking/alcohol: {cand[:15]}")
    if smk_node is not None and "tobacco_smoking_history" in clin:
        chosen["smoking"] = smk_node["name"]
        for pid, v in clin["tobacco_smoking_history"].items():
            if isinstance(v, str) and SMOKERS.search(v):
                rows.append({"patient_id": pid, "primekg_type": "exposure", "primekg_index": smk_node["index"], "primekg_id": smk_node["id"],
                             "primekg_name": smk_node["name"], "raw": v})
    elif smk_node is None:
        print("WARNING: no smoking exposure node found; rerun with --smoking-node '<name from the list above>'")
    if alcohol_mode == "heavy" and "alcohol_consumption" in clin:
        alc_node, _ = pick_exposure(nodes, None, [r"^ethanol$", r"alcohol", r"ethanol"])
        if alc_node is not None:
            chosen["alcohol"] = alc_node["name"]
            for pid, v in clin["alcohol_consumption"].items():
                if isinstance(v, str) and HEAVY_DRINK.search(v):
                    rows.append({"patient_id": pid, "primekg_type": "exposure", "primekg_index": alc_node["index"], "primekg_id": alc_node["id"],
                                 "primekg_name": alc_node["name"], "raw": v})
    return pd.DataFrame(rows, columns=["patient_id", "primekg_type", "primekg_index", "primekg_id", "primekg_name", "raw"]), chosen


def history_edges(clin, nodes):
    m = Matcher(nodes, "disease", cutoff=0.8)
    rows, excluded = [], []
    if "cancer_type" not in clin:
        return pd.DataFrame(), excluded
    for pid, raw in clin["cancer_type"].items():
        if pd.isna(raw):
            continue
        for tok in set(t.strip() for t in str(raw).split("|") if t.strip()):
            if RENAL.search(tok):
                excluded.append(f"{pid}: {tok}")
                continue
            name = re.sub(r"\b(left|right|lt|rt|bilateral)\b", " ", tok.lower().replace(" of skin", ""))
            node, how, score = m.match(re.sub(r"\s+", " ", name).strip())
            if node is not None:
                rows.append({"patient_id": pid, "primekg_type": "disease", "primekg_index": node["index"], "primekg_id": node["id"],
                             "primekg_name": node["name"], "raw": tok, "method": how, "score": round(score, 2)})
            else:
                excluded.append(f"{pid}: {tok} (no PrimeKG match)")
    return pd.DataFrame(rows), excluded


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kg", default="data/raw/kg.csv", help="PrimeKG kg.csv")
    ap.add_argument("--smoking-node", help="exact PrimeKG exposure name to use for smoking")
    ap.add_argument("--alcohol", choices=["heavy", "none"], default="heavy")
    ap.add_argument("--keep-periop", action="store_true", help="keep enoxaparin/heparin edges")
    args = ap.parse_args()

    clin = load_clinical()
    nodes = load_primekg_nodes(args.kg)

    drugs, review = drug_edges(clin, nodes, args.keep_periop)
    expo, chosen = exposure_edges(clin, nodes, args.smoking_node, args.alcohol)
    hist, excluded = history_edges(clin, nodes)

    save(drugs, "patient_drug_edges")
    save(expo, "patient_exposure_edges")
    save(hist, "patient_history_edges")
    OUT.mkdir(parents=True, exist_ok=True)
    review.to_csv(OUT / "med_mapping_review.csv", index=False)

    patients = list(clin.index)
    per = (pd.concat([drugs[["patient_id"]], expo[["patient_id"]], hist[["patient_id"]] if len(hist) else None])
           .groupby("patient_id").size().reindex(patients, fill_value=0))
    unmatched = review[review["primekg_id"].isna() & (review["method"] != "skipped")]
    qc = {
        "patients_with_med_list": int(clin["medication_name_vitamins_supplements"].notna().sum()),
        "drug_edges": int(len(drugs)),
        "patients_with_drug_edge": int(drugs["patient_id"].nunique()),
        "drug_match_methods": drugs["method"].value_counts().to_dict(),
        "fuzzy_matches_to_review": int((review["method"] == "fuzzy").sum()),
        "unmatched_med_tokens": int(len(unmatched)),
        "top_drugs": drugs["primekg_name"].value_counts().head(15).to_dict(),
        "periop_dropped": not args.keep_periop,
        "exposure_nodes_used": chosen,
        "exposure_edges": expo["primekg_name"].value_counts().to_dict(),
        "history_edges": hist[["raw", "primekg_name"]].to_dict("records") if len(hist) else [],
        "history_excluded": excluded,
        "new_edges_per_patient": per.value_counts().sort_index().to_dict(),
    }
    qc = json.loads(json.dumps(qc, default=str))
    (OUT / "ws2_extra_edges_qc.json").write_text(json.dumps(qc, indent=2))
    print(json.dumps(qc, indent=2))
    print("\nFuzzy matches (check these by eye):")
    print(review[review["method"] == "fuzzy"][["raw", "normalized", "primekg_name", "score"]]
          .drop_duplicates().to_string(index=False))
    print("\nMost common unmatched medication tokens:")
    print(unmatched["normalized"].value_counts().head(15).to_string())


if __name__ == "__main__":
    main()
