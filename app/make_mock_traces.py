"""Write curated demo traces to app/mock_traces/ (python -m app.make_mock_traces).

They use the real mock tool results (agent/mock_tools.py), so every cited ID exists in the trace's evidence and
the traces pass `common.validate`. All IDs are fake on purpose (E9000xx, PMID:0000000x). They show the demo flow
until the agent writes real traces to cache/demo/ with:
    python -m agent.run --questions eval/questions.jsonl --demo-patients <pid> ...
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import mock_tools as M  # noqa: E402
from common.contracts import CCRCC_ID, CONTRACT_VERSION, patient_curie, patient_edge_id  # noqa: E402

OUT = Path(__file__).parent / "mock_traces"
PID = M.PID
PT = lambda rel, c: patient_edge_id(PID, rel, c)  # noqa: E731
VEGFA_UP, VHL_DOWN = PT("protein_up", "NCBIGene:7422"), PT("protein_down", "NCBIGene:7428")
HTN = PT("has_phenotype", "HP:0000822")


def claim(i, text, edges=(), pmids=(), status="supported", reason=None):
    c = {"claim_id": f"c{i}", "text": text, "support": {"edge_ids": list(edges), "pmids": list(pmids)},
         "status": status}
    if reason:  # extra key (allowed by CONTRACTS.md): why the verifier removed or relabeled the claim
        c["reason"] = reason
    return c


SCRIPTS = {
    "P01": (
        f"This tumor looks driven by the VHL → HIF-2α → VEGF axis. VHL protein is low [{VHL_DOWN}] and VEGFA is "
        f"strongly up [{VEGFA_UP}]. In the curated mechanism, VHL loss stabilizes HIF-2α [PMID:00000001, PMID:00000002], "
        "which drives VEGF-dependent blood-vessel growth [PMID:00000003]. Two drug routes reach this axis in the graph: "
        "sunitinib acts on KDR, the VEGF receptor [E900006], and belzutifan acts on HIF-2α itself [E900007]. "
        "Low VHL protein is not proof of a VHL mutation, and a drug target is not proof of benefit.",
        [claim(1, "VHL protein is low in this tumor.", [VHL_DOWN]),
         claim(2, "VEGFA is strongly overexpressed in this tumor.", [VEGFA_UP]),
         claim(3, "VHL loss stabilizes HIF-2α.", pmids=["PMID:00000001", "PMID:00000002"]),
         claim(4, "HIF-2α drives VEGF-dependent angiogenesis.", pmids=["PMID:00000003"]),
         claim(5, "Sunitinib targets KDR, the VEGF receptor.", ["E900006"]),
         claim(6, "Belzutifan targets HIF-2α.", ["E900007"]),
         claim(7, "This patient will respond to sunitinib.", status="removed",
               reason="A drug-target edge shows sunitinib acts on KDR. Nothing the tools returned measures response in this patient."),
         claim(8, "Blocking HIF-2α may lower VEGFA more than blocking VEGFR in VHL-low tumors.", status="hypothesis",
               reason="Plausible from the evidence but untested, so it is kept and labeled as a hypothesis.")]),
    "P02": (
        f"The record lists hypertension [{HTN}]. The evidence can't tell whether it is caused by the disease or by a "
        "drug: these tumors were collected before treatment, and there is no blood-pressure timeline. VEGF-pathway "
        "drugs are known to raise blood pressure, so it should be watched if treatment starts.",
        [claim(1, "This patient has hypertension on record.", [HTN]),
         claim(2, "The hypertension is caused by the tumor.", status="removed",
               reason="There is no treatment or blood-pressure timeline, and no edge links this tumor to the patient's hypertension."),
         claim(3, "Blood pressure may rise if a VEGF-pathway drug is started.", status="hypothesis",
               reason="Plausible from the evidence but untested, so it is kept and labeled as a hypothesis.")]),
    "P03": (
        f"The strongest supported path runs from this tumor's low VHL [{VHL_DOWN}] through HIF-2α [E900003] to VEGFA "
        f"[E900004], which is up in this tumor [{VEGFA_UP}]. The graph has no edge linking these proteins to the "
        "patient's hypertension or anemia, so that link stays unproven.",
        [claim(1, "VHL protein is low in this tumor.", [VHL_DOWN]),
         claim(2, "VHL interacts with HIF-2α (EPAS1).", ["E900003"]),
         claim(3, "HIF-2α interacts with VEGFA.", ["E900004"]),
         claim(4, "VEGFA is strongly overexpressed in this tumor.", [VEGFA_UP]),
         claim(5, "VEGFA overexpression causes this patient's anemia.", status="removed",
               reason="No edge or citation connects VEGFA to anemia in what the tools returned."),
         claim(6, "High VEGFA may track with blood-pressure changes across the cohort.", status="hypothesis",
               reason="Plausible from the evidence but untested, so it is kept and labeled as a hypothesis.")]),
    "P04": (
        f"Of the known kidney-cancer driver genes, VHL is the one changed here: its protein is low [{VHL_DOWN}], and "
        "VHL is linked to clear cell renal carcinoma in the graph [E900001]. HIF-2α (EPAS1) is also linked to the "
        "disease [E900002] but isn't among this patient's top changes. Lower protein is not the same as a mutation.",
        [claim(1, "VHL protein is low in this tumor.", [VHL_DOWN]),
         claim(2, "VHL is linked to clear cell renal carcinoma.", ["E900001"]),
         claim(3, "EPAS1 (HIF-2α) contributes to clear cell renal carcinoma.", ["E900002"]),
         claim(4, "This patient carries a VHL mutation.", status="removed",
               reason="The patient record measures how much VHL protein there is, not the VHL gene sequence. No mutation evidence was returned.")]),
    "P05": (
        f"The model ranks this tumor as likely higher-stage (score 0.78). The biggest contributors are VEGFA, which is "
        f"strongly up [{VEGFA_UP}], and the hypertension on record [{HTN}]. The score comes from about 100 patients and "
        "has no validated confidence interval, so treat it as a ranking signal, not a probability to quote.",
        [claim(1, "VEGFA is strongly overexpressed in this tumor.", [VEGFA_UP]),
         claim(2, "This patient has hypertension on record.", [HTN]),
         claim(3, "The risk score is 95% accurate.", status="removed",
               reason="The model has no validated accuracy or calibration for a single patient; no tool returned one."),
         claim(4, "VEGFA and hypertension are what drive this score.", status="hypothesis",
               reason="Plausible from the evidence but untested, so it is kept and labeled as a hypothesis.")]),
}


def drafts(text, claims):
    """The agent's draft carries every claim, including the ones the verifier then removes; the answer drops removed
    claims and labels hypotheses, the way agent/verifier.py does."""
    sents = text.split(". ")
    head, tail = ". ".join(sents[:-1]) + ".", sents[-1]
    extra = [c for c in claims if c["status"] in ("removed", "hypothesis")]
    for c in extra:
        c["quote"] = c["text"]
    draft = " ".join([head] + [c["text"] for c in extra] + [tail])
    answer = " ".join([head] + [f"Hypothesis (unverified): {c['text']}" for c in extra if c["status"] == "hypothesis"]
                      + [tail])
    return draft, answer


def trace(qid, question):
    text, claims = SCRIPTS[qid]
    draft, answer = drafts(text, claims)
    calls = [("get_patient", {"patient_id": PID}, 42), ("predict_patient", {"patient_id": PID}, 180),
             ("retrieve_subgraph", {"question": question, "seeds": [patient_curie(PID)], "budget": 50}, 1240),
             ("get_mechanism", {"disease_curie": CCRCC_ID}, 85)]
    tcs = [{"name": n, "args": a, "result": M.dispatch(n, a), "ms": ms} for n, a, ms in calls]
    pat, sub, mech = tcs[0]["result"], tcs[2]["result"], tcs[3]["result"]
    edges = [x["edge_id"] for k in ("phenotypes", "proteins_up", "proteins_down") for x in pat[k]] + sub["edge_ids"]
    pmids = [p for c in mech["chains"] for s in c["steps"] for p in s["pmids"]]
    n = lambda s: sum(c["status"] == s for c in claims)  # noqa: E731
    return {"contract_version": CONTRACT_VERSION, "run_id": "mock_curated", "qid": qid, "mode": "agent",
            "patient_id": PID, "question": question, "model": "mock", "variant": "full", "tool_calls": tcs,
            "evidence": {"edge_ids": edges, "pmids": pmids}, "draft": draft, "claims": claims, "answer": answer,
            "verifier": {"n_claims": len(claims), "n_supported": n("supported"), "n_hypothesis": n("hypothesis"),
                         "n_removed": n("removed")},
            "notes": ["MOCK DATA: curated demo trace"]}


def main():
    from app.data import demo_questions
    OUT.mkdir(exist_ok=True)
    for q in demo_questions():
        if q["qid"] in SCRIPTS:
            t = trace(q["qid"], q["question"])
            (OUT / f"{PID}__{q['qid']}.json").write_text(json.dumps(t, indent=1, ensure_ascii=False))
            print(PID, q["qid"], t["verifier"])


if __name__ == "__main__":
    main()
