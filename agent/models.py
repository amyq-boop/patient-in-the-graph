"""Which NIM models can the agent use? Lists the live catalog and probes tool calling.

    python -m agent.models                 # list chat-ish models available to your key
    python -m agent.models --probe         # also send one tiny tool call to each likely candidate
    python -m agent.models --probe nvidia/some-model other/model

A model passes the probe if it answers with a real tool call (not text). Put the winner in .env as NIM_MODEL=...
"""
import argparse
import json
import sys
import time

from agent import config as C

LIKELY = ("nemotron", "llama", "qwen", "mistral", "mixtral", "kimi", "glm", "deepseek", "gpt-oss")
SKIP = ("embed", "rerank", "guard", "safety", "vision", "-vl", "parse", "reward", "clip", "whisper", "tts",
        "asr", "retriever", "nemoretriever", "pii", "content", "flux", "sdxl", "cosmos", "translate")

PROBE_TOOL = [{"type": "function", "function": {
    "name": "get_patient", "description": "Get a patient's record.",
    "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}}, "required": ["patient_id"]}}}]


def client():
    from openai import OpenAI
    if not C.NIM_API_KEY:
        sys.exit("NVIDIA_API_KEY is not set (put it in .env)")
    return OpenAI(base_url=C.NIM_BASE_URL, api_key=C.NIM_API_KEY, timeout=45, max_retries=0)


def list_models(cl):
    ids = sorted(m.id for m in cl.models.list().data)
    return [i for i in ids if not any(s in i.lower() for s in SKIP)]


def probe(cl, model):
    try:
        r = cl.chat.completions.create(
            model=model, temperature=0, max_tokens=200, tools=PROBE_TOOL, tool_choice="auto",
            messages=[{"role": "user", "content": "Look up patient C3L-MOCK1 using the tool."}])
        m = r.choices[0].message
        if m.tool_calls:
            tc = m.tool_calls[0]
            ok = tc.function.name == "get_patient" and "C3L-MOCK1" in (tc.function.arguments or "")
            return ("PASS" if ok else "ODD ") + f" tool_call {tc.function.name}({tc.function.arguments})"
        return "FAIL text only: " + json.dumps((m.content or "")[:80])
    except Exception as e:
        return f"ERR  {getattr(e, 'status_code', '')} {str(e)[:100]}"


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", nargs="*", help="probe these models (default: likely candidates from the list)")
    ap.add_argument("--max", type=int, default=12, help="max models to probe")
    a = ap.parse_args(argv)
    cl = client()
    ids = list_models(cl)
    print(f"{len(ids)} chat-like models available. Current NIM_MODEL = {C.NIM_MODEL} "
          f"({'listed' if C.NIM_MODEL in ids else 'NOT listed'})\n")
    for i in ids:
        print("  " + i)
    if a.probe is None:
        return
    cands = a.probe or [i for i in ids if any(k in i.lower() for k in LIKELY)
                        and any(t in i.lower() for t in ("instruct", "chat", "nemotron", "kimi", "glm", "deepseek", "gpt-oss"))]
    print(f"\nprobing {min(len(cands), a.max)} models with one tool call each:")
    for m in cands[: a.max]:
        t = time.perf_counter()
        r = probe(cl, m)
        print(f"  {r}   {m}   ({time.perf_counter() - t:.1f}s)", flush=True)


if __name__ == "__main__":
    main()
