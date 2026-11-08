"""OpenAI tool schemas the agent sends to the LLM (CONTRACTS.md §7).

Same names and arguments as agent/mock_tools.TOOLS, with fuller descriptions to steer the model.
"""


def _fn(name, description, properties, required):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


_S = {"type": "string"}

TOOLS = [
    _fn("resolve_terms",
        "Map free-text biomedical terms (genes, diseases, phenotypes, drugs) to graph CURIEs. "
        "Call this before using a term as a seed.",
        {"texts": {"type": "array", "items": _S, "description": "Terms to resolve"}}, ["texts"]),
    _fn("get_patient",
        "A patient's clinical summary, phenotypes, top up- and down-regulated tumor proteins (z-scores), "
        "somatic tumor mutations (gene + protein change) and baseline medications. "
        "Each item has a citable edge_id (pt:...). Never includes stage, grade or outcome.",
        {"patient_id": {"type": "string", "description": "CPTAC case ID, e.g. C3L-00004"}}, ["patient_id"]),
    _fn("predict_patient",
        "GNN-A out-of-fold risk estimate (stage III-IV) for the patient, with the nodes that contributed most, "
        "split into tumor_drivers (tumor proteins) and clinical_drivers (recorded phenotypes) with their shares.",
        {"patient_id": _S}, ["patient_id"]),
    _fn("rank_for_patient",
        "GNN-A ranking of the genes, pathways or drugs most relevant to this patient's tumor proteins. "
        "Drugs are approved drugs ranked by how much more they score for this patient than for the rest of the cohort.",
        {"patient_id": _S, "node_type": {"type": "string", "enum": ["gene", "pathway", "drug"]},
         "k": {"type": "integer", "description": "How many (default 10)"}},
        ["patient_id", "node_type"]),
    _fn("retrieve_subgraph",
        "GNN-B: retrieve a compact evidence subgraph (<= budget nodes) for a question, from seed CURIEs "
        "(a PATIENT:<id> seed expands to the patient's phenotypes and proteins). Returns triples with edge IDs "
        "(E...). Only edges returned here or by get_patient can be cited.",
        {"question": _S, "seeds": {"type": "array", "items": _S, "description": "Seed CURIEs"},
         "budget": {"type": "integer", "description": "Max nodes (default 50). Usually leave unset."}},
        ["question", "seeds"]),
    _fn("get_mechanism",
        "Curated dismech causal mechanism chains for a disease, with PubMed IDs and supporting quotes.",
        {"disease_curie": {"type": "string", "description": "MONDO CURIE, e.g. MONDO:0005005"}},
        ["disease_curie"]),
]

TOOL_NAMES = [t["function"]["name"] for t in TOOLS]

DRUG_TARGETS_TOOL = _fn(
    "get_drug_targets",
    "Approved drugs whose PrimeKG drug-protein targets are among this patient's up-regulated tumor proteins or "
    "somatically mutated genes. Each target comes with two citable IDs: the drug-target edge (E...) and the "
    "patient edge (pt:...). Also flags drugs with an indication edge to this patient's cancer. A shared target is a "
    "rationale to investigate, not evidence that the drug works for this patient.",
    {"patient_id": _S, "k": {"type": "integer", "description": "How many drugs (default 15)"}},
    ["patient_id"])


def tools_for(drug_targets=False):
    """The tool list the agent sends. TOOLS itself never changes, so runs without get_drug_targets stay identical."""
    return TOOLS + [DRUG_TARGETS_TOOL] if drug_targets else TOOLS
