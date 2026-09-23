# API contract — «Кабинет акима»

Base URL: `http://localhost:8000`. All bodies are JSON, UTF-8. CORS is open. The frontend is served by the same app at `/`.

This file = PLAN.md §4 verbatim, plus the extra fields the backend returns (marked **+**) and the two §12 endpoints (`/api/approval`, `/api/narrative`). Extra fields are additive: nothing from §4 was renamed or removed.

## Conventions

- **Plan format, everywhere:** `{"decisions":[{"measure":"M7","district":"Нура"}, {"measure":"M12","district":null}, ...]}`, exactly 5 decisions for scoring. City-type measures have `district: null`.
- District names are the Russian names from the dataset: `Есиль`, `Алматы`, `Сарыарка`, `Байконур`, `Нура`.
- **Indicator arrays** (`before`, `after`) have 10 values in the order of `dataset.indicators`: `T1 T2 E1 E2 S1 S2 B1 B2 C1 C2`.
- All scores are floats rounded to 2 decimals. Every number comes from `engine/`. The LLM never computes numbers.
- An invalid plan on a scoring endpoint → HTTP 422 `{"detail": "<Russian reason>"}`.

## Endpoints (PLAN.md §4 verbatim)

- `GET /api/dataset` → **the contents of `data/dataset.json` verbatim** (no wrapper): `budget`, `directions`, `indicators`, `districts`, `measures`, `synergies`, `incompatibilities`, `rules`, `reference`. The frontend renders everything from this and never hardcodes.
- `POST /api/validate` {plan} → `{valid: bool, reason: str|null, cost: int, remaining: int}`. Call on every change for the live budget bar and red errors. Works on partial plans (fewer than 5 decisions gives `valid:false` with a reason, but `cost` and `remaining` are still correct).
  - **+** `direction_counts: {transport: n, ecology: n, social: n, safety: n, services: n}`
- `POST /api/score` {plan} →
  ```
  {score, baseline:52.56, delta, cost,
   districts:{Нура:{before:[10], after:[10], D_before, D_after}, ...},
   n_crit, crit_cells:[{district,indicator,value}],
   contributions:[{measure, district, cost, score_without, marginal}],   // remove-one
   timeline:[{q:0..8, score}],                                           // linear ramp after lag
   synergies_triggered:[...]}
  ```
  **+** `remaining`, `d_avg`, `d_min`, `d_min_district`, `n_crit_before`, `districts.*.pop`,
  `resolved_crit_cells:[{district, indicator, before, after}]`, `contributions[].marginal_per_cost`,
  `synergies_triggered:[{pair:[a,b], district, bonus:{T1:2}}]`, `realized_share:{M7:0.625,...}`,
  **`approval`**: the same object `/api/approval` returns, embedded so the Verdict screen needs one call.
- `POST /api/optimize` {plan} → `{best:{plan,score,cost}, rank, total_valid:694395, percentile, pareto:[{cost,score,plan}], best_at_same_cost}`
  - **+** `best.approval`, `gap_to_best`, `balanced:{plan, score, cost, approval}` (the best plan whose approval rating is ≥ 50, i.e. "the politically survivable optimum"). `percentile` = % of valid plans with a strictly lower score.
- `POST /api/analyze` {plan, lang:"ru"} →
  ```
  {summary, strengths[], risks[], consequences[], tradeoffs[],
   recommendation:{plan, expected_score, why}, provider:"openai|nvidia|offline", grounded:true}
  ```
  `strengths`, `risks`, `consequences` and `tradeoffs` are arrays of strings. `grounded:false` means the number guard rejected the LLM text twice and the offline template was used.
  - **+** `recommendation.expected_score` always comes from the engine. `guard: {attempts, rejected: [numbers]}`, `tool_trace: [{tool, args}]` (empty offline), `fallback_reason` (only when the LLM path failed or timed out). Show `tool_trace` in the UI as "агент вызвал: score → optimize_same_budget → what_if". It is the visible proof that the agent really calls tools.
- `POST /api/shock` {plan, seed} → `{event:{id,title,district,effects}, new_baseline_score, must:"swap_one"}`
  - **+** `event.description`, `event.hint`, `event.quarter`, `score_before` (the plan's score without the crisis), `best_swaps:[{out, in:{measure,district}, score, gain}]` (top 3; use it for a hint, or keep it hidden so the AI can judge the swap).
  - `seed` is any integer; the event is `events[seed % 3]`.
- `POST /api/shock/resolve` {plan, event_id, swap:{out, in}} → same shape as /score + `{crisis_cost, recovered}`
  - `swap = {"out": "M7", "in": {"measure": "M13", "district": "Алматы"}}`. The new plan must stay valid (same budget of 100), otherwise 422 with the reason.
  - **+** `score_before_crisis`, `score_after_crisis_no_swap`, `best_possible_swap:{out,in,score,gain}`, `swap_was_optimal: bool`, `new_plan`.
- `POST /api/submit` {team, plan, score} → `{ok: true, id, rank}`. **The server recomputes the score from the plan and ignores the client-sent `score`**, so the leaderboard can't be spoofed.
- `GET /api/leaderboard` → `[{team, score, cost, approval, plan, created_at}]` sorted by score desc (top 50).
- `GET /api/brief?plan=<urlencoded plan JSON>` → `text/markdown` one-pager (also accepts `POST /api/brief` {plan}).

## §12 endpoints

### `POST /api/approval` {plan}

Political-risk layer, deterministic, in `engine/approval.py`. **Not part of the official Score (AQLS).** The UI must label it "рейтинг акима — слой политического риска, не входит в Score".
```
{ "city": 48.4, "threshold": 50, "reelected": false,
  "districts": { "Нура": {"approval": 79.6, "delta_D": 3.78, "got_district_measure": true, "crit_cells": 0}, ... } }
```

### `POST /api/narrative` {plan, lang:"ru", event_id?: str, swap?: {...}}

One call returns the District Council and the newspaper front page. Offline mode fills the same shape from templates.
```
{ "council": [
    { "district": "Нура", "deputy": "Депутат от Нуры", "mood": "positive|neutral|negative",
      "quote": "…", "delta_D": 3.78, "approval": 79.6 }
    // exactly 5, one per district, in dataset order
  ],
  "newspaper": {
    "masthead": "Астана Times", "date": "IV квартал 2028",
    "headlines": [ {"title": "В Нуре отменили вторую смену", "lead": "…"} ],   // 4–5
    "editorial": "…one paragraph…",
    "crit_sidebar": [ {"district": "Алматы", "indicator": "T1", "value": 38.25} ]  // cells still < 40
  },
  "provider": "openai|nvidia|offline", "grounded": true, "guard": {"attempts": 0, "rejected": []} }
```
If `event_id` (+ optional `swap`) is passed, the newspaper covers the crisis as well.

## Status

| Endpoint | Status |
|---|---|
| everything above | **implemented and tested** (`pytest -q`). Live: `uvicorn api.main:app` → `/docs` |
