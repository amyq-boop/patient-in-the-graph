"""LLM clients.

NimLLM   NVIDIA NIM through the OpenAI-compatible API, with retries and an on-disk
         response cache (cache/llm/) so reruns and the demo don't spend credits.
FakeLLM  Deterministic scripted policy for offline tests (BIOHACK_FAKE_LLM=1).

Both expose .chat(messages, tools=None, json_mode=False, purpose="agent") and
return {"content": str | None, "tool_calls": [{"id", "name", "arguments": dict}]}.
"""
import hashlib
import json
import os
import re
import time

from agent import config as C


def _parse_text_tool_call(content):
    """Some NIM models put the tool call in the text instead of tool_calls. Recover it."""
    if not content:
        return None
    s = content.strip().replace("<|python_tag|>", "").strip()
    m = re.search(r"\{.*\}", s, re.S)
    if not m or not s.startswith(("{", "```")):
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    name = obj.get("name") or obj.get("function")
    args = obj.get("parameters") or obj.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None
    if isinstance(name, str) and isinstance(args, dict):
        return [{"id": "call_text_0", "name": name, "arguments": args}]
    return None


class NimLLM:
    def __init__(self, model=None, cache=True):
        from openai import OpenAI

        if not C.NIM_API_KEY:
            raise RuntimeError("NVIDIA_API_KEY is not set (put it in .env)")
        self.model = model or C.NIM_MODEL
        self.client = OpenAI(base_url=C.NIM_BASE_URL, api_key=C.NIM_API_KEY,
                             timeout=float(os.environ.get("NIM_TIMEOUT", "300")), max_retries=0)  # longer outputs need longer calls
        self.cache = cache and os.environ.get("LLM_CACHE", "1") != "0"
        self.cache_dir = C.CACHE / "llm"
        self.truncated = []  # purposes whose output hit max_tokens during the current run

    def chat(self, messages, tools=None, json_mode=False, purpose="agent"):
        # Reasoning models spend hidden tokens against max_tokens, so leave room: at 2048 the final answer
        # was cut off mid-section, and at 8192 the claims JSON was cut off mid-object (Nov 7, nemotron-3-ultra).
        max_tokens = int(os.environ.get("NIM_MAX_TOKENS_CLAIMS", "16384") if purpose == "claims"
                         else os.environ.get("NIM_MAX_TOKENS", "8192"))
        req = {"model": self.model, "messages": messages, "temperature": C.TEMPERATURE, "max_tokens": max_tokens}
        if tools:
            req["tools"] = tools
            req["tool_choice"] = "auto"
        if json_mode:
            req["response_format"] = {"type": "json_object"}
        key = hashlib.sha256(json.dumps(req, sort_keys=True, default=str).encode()).hexdigest()[:32]
        f = self.cache_dir / f"{key}.json"
        if self.cache and f.exists():
            out = json.loads(f.read_text(encoding="utf-8"))
            if out.get("finish_reason") == "length":
                self.truncated.append(purpose)
            return out

        last = None
        for attempt in range(5):
            try:
                try:
                    resp = self.client.chat.completions.create(**req)
                except Exception as e:  # some models reject response_format; retry without it
                    if json_mode and "response_format" in req and "response_format" in str(e):
                        req.pop("response_format")
                        resp = self.client.chat.completions.create(**req)
                    else:
                        raise
                msg = resp.choices[0].message
                calls = []
                for tc in msg.tool_calls or []:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {"_unparsed": tc.function.arguments}
                    calls.append({"id": tc.id, "name": tc.function.name, "arguments": args})
                if not calls and tools:
                    calls = _parse_text_tool_call(msg.content) or []
                if json_mode and not calls and not (msg.content or "").strip() and "response_format" in req:
                    req.pop("response_format")  # some models return empty content in JSON mode: retry as plain text
                    raise RuntimeError("empty content in JSON mode")
                finish = getattr(resp.choices[0], "finish_reason", None)
                if finish == "length":
                    self.truncated.append(purpose)  # surfaced in the trace notes by graph_agent
                out = {"content": None if calls and not msg.tool_calls else msg.content, "tool_calls": calls,
                       "finish_reason": finish}
                if self.cache and (calls or (msg.content or "").strip()):  # never cache an empty answer
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    f.write_text(json.dumps(out), encoding="utf-8")
                return out
            except Exception as e:
                last = e
                code = getattr(e, "status_code", None)
                if code is not None and 400 <= code < 500 and code not in (408, 409, 429):
                    hint = {401: "check NVIDIA_API_KEY in .env", 403: "this key can't use this model",
                            404: "model id not found", 410: "this model has been retired from the catalog"}.get(code, "")
                    raise RuntimeError(f"NIM error {code} for model {self.model!r}: {hint}. "
                                       f"Run `python -m agent.models --probe` to pick a working NIM_MODEL. ({e})") from e
                time.sleep(min(2 ** attempt, 20))
        raise RuntimeError(f"NIM call failed after retries: {last}")


class FakeLLM:
    """Scripted stand-in for tests: follows the tool plan in the system prompt, then drafts
    an answer citing real IDs from tool results plus one fabricated ID (to exercise the verifier)."""

    model = "fake-llm"

    def chat(self, messages, tools=None, json_mode=False, purpose="agent"):
        if purpose == "claims":
            from agent.verifier import deterministic_claims
            return {"content": json.dumps({"claims": deterministic_claims(messages[-1]["content"].split("ANSWER:", 1)[-1])}),
                    "tool_calls": []}
        text = " ".join(m.get("content") or "" for m in messages if m["role"] in ("system", "user"))
        done = [m for m in messages if m["role"] == "assistant" and m.get("tool_calls")]
        pid = re.search(r"Patient: (\S+)", text)
        plan = self._plan(pid.group(1) if pid else None, messages)
        if tools and len(done) < len(plan):
            name, args = plan[len(done)]
            return {"content": None, "tool_calls": [{"id": f"call_{len(done)}", "name": name, "arguments": args}]}
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        edges = [e for r in results for e in r.get("edge_ids", [])]
        pt = [x["edge_id"] for r in results for k in ("proteins_up",) for x in r.get(k, [])]
        pmids = [p for r in results for c in r.get("chains", []) for s in c["steps"] for p in s["pmids"]]
        pmids += [p for r in results for p in re.findall(r"PMID:\d+", r.get("citable_lines", ""))]  # get_mechanism view
        return {"content": self._draft(edges, pt, pmids), "tool_calls": []}

    @staticmethod
    def _plan(pid, messages):
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        q = next(m["content"] for m in messages if m["role"] == "user")
        if pid:
            pat = next((r for r in results if "proteins_up" in r), None)
            seeds = [f"PATIENT:{pid}"]
            if pat:
                seeds += [p["curie"] for p in pat["phenotypes"]] + [p["curie"] for p in pat["proteins_up"][:3]]
            return [("get_patient", {"patient_id": pid}), ("predict_patient", {"patient_id": pid}),
                    ("rank_for_patient", {"patient_id": pid, "node_type": "drug", "k": 5}),
                    ("retrieve_subgraph", {"question": q, "seeds": seeds, "budget": 50}),
                    ("get_mechanism", {"disease_curie": C.CCRCC_ID})]
        res = next((r for r in results if "results" in r), None)
        seeds = [m["curie"] for r in (res or {}).get("results", []) for m in r["matches"][:1]] or [C.CCRCC_ID]
        return [("resolve_terms", {"texts": ["clear cell renal carcinoma"]}),
                ("retrieve_subgraph", {"question": q, "seeds": seeds, "budget": 50}),
                ("get_mechanism", {"disease_curie": C.CCRCC_ID})]

    @staticmethod
    def _draft(edges, pt, pmids):
        lines = ["**Summary**"]
        if edges:
            lines.append(f"- VHL is linked to clear cell renal carcinoma [{edges[0]}].")
        if pt:
            lines.append(f"- The patient's tumor overexpresses VEGFA [{pt[0]}].")
        if pmids:
            lines.append(f"- VHL inactivation stabilizes HIF-2alpha [{pmids[0]}].")
        lines += ["- PBRM1 loss doubles metastasis risk [E999999999].",
                  "- BAP1 mutations are associated with worse outcome.",
                  "**Testable hypothesis**",
                  "- Hypothesis: high CA9 marks a hypoxic, VEGF-high tumor that would respond to belzutifan.",
                  "**Caveats**", "- Model estimates only."]
        return "\n".join(lines)


def get_llm(model=None):
    if os.environ.get("BIOHACK_FAKE_LLM") == "1":
        return FakeLLM()
    return NimLLM(model=model)
