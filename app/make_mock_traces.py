"""Write offline demo traces to app/mock_traces/ by running the real agent loop on mock tools and a fake LLM.

    python -m app.make_mock_traces

Every ID in these traces is fake (E9000xx, PMID:0000000x), so they can never pass as real evidence.
Real demo traces go to cache/demo/ via:  python -m agent.run --questions eval/questions.jsonl --demo-patients <pids>
"""
import json
import os

os.environ["BIOHACK_MOCK"] = "1"
os.environ["BIOHACK_FAKE_LLM"] = "1"

from agent.graph_agent import run  # noqa: E402
from agent.mock_tools import PATIENTS  # noqa: E402
from app.data import MOCK_DIR, demo_questions  # noqa: E402


def main():
    MOCK_DIR.mkdir(exist_ok=True)
    qs = demo_questions()
    for pid in PATIENTS:
        for q in qs:
            t = run(q["question"], patient_id=pid, qid=q["qid"], run_id="mock")
            (MOCK_DIR / f"{pid}__{q['qid']}.json").write_text(json.dumps(t, indent=1, default=str))
            print(pid, q["qid"], t["verifier"])


if __name__ == "__main__":
    main()
