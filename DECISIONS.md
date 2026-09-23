# Decisions log

One line per choice made without asking. Newest at the bottom.

- Dataset transcribed from the organizers' «Датасет районов» doc into `data/dataset.json`; verified baseline 52.56, doc example 56.54, cheapest 55.67, optimum 57.24 before any engine code.
- Python 3.12 venv (`.venv`); the machine's default `python` is 3.8.
- Indicator arrays in API responses are ordered as `dataset.indicators` (T1…C2), per PLAN §4 `before:[10]`.
- Approval constants: PLAN §12's literal ones (45 / 10·ΔD / +6 / −8 / 3) put the 57.24 optimum at ≈56, *above* 50, which breaks §12's own goal. Changed to BASE 50, A 4, GOT +10, MISS −12, CRIT 3 → optimum ≈48 (fired), doc example ≈51 (survives). Final values in `engine/approval.py`.
- Crisis events shift the district's *starting* indicator by the event effect (before measures); score under crisis = same formula on the shocked base. `crisis_cost = s0 − s1`, `recovered = s2 − s1`.
- `/api/submit` recomputes the score server-side and ignores the client's `score` field.
- Added `balanced` to the optimizer cache: best plan with approval ≥ 50. It is the AI's "politically survivable" recommendation.
- Every displayed difference (delta, marginal, delta_D, gain, crisis_cost, recovered) = difference of the 2-dp rounded values, so 56.54 − 52.56 shows +3.98, not +3.99 from full precision (`engine.score.diff2`).
- LLM loop budget `LLM_TIMEOUT` (default 60 s) with `reasoning_effort=low` for gpt-5 models; on timeout the offline template is returned with `fallback_reason`.
- District case forms (`cases.gen/acc/loc`) added to `dataset.json` so offline Russian text declines district names correctly.
- LLM fallback chain: `LLM_PROVIDER` is an ordered list (`openai,nvidia`), each provider takes several comma-separated keys (`*_API_KEYS`). Keys are tried in order under one shared `LLM_TIMEOUT`; 401/403/404/410 marks a key dead for the process (fingerprint only); failed runs are not cached. Responses show slot labels (`openai#2`), never keys.
- Agent's first turn is forced to call `optimize_same_budget` (live gpt-5-mini otherwise answered from the prompt with zero tool calls and recommended the user's own plan).
- NVIDIA default model → `nvidia/nemotron-3-super-120b-a12b` (`meta/llama-3.3-70b-instruct` reached end-of-life 2026-08-26, HTTP 410). The two NVIDIA keys supplied return 401 at integrate.api.nvidia.com, so the NVIDIA path is unverified live; OpenAI keys verified.
