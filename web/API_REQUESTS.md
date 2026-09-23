# API requests from the frontend

**Status:** `PLAN.md` and `api/CONTRACT.md` were not available when the frontend was built. Everything below is what `web/index.html` calls today, in the shapes it expects. The mock in `web/mock/engine.js` implements exactly this. If the real contract differs, tell the frontend owner, or match these names. The page also tolerates some alternative names; see `normalizeDataset()` in `index.html`.

All requests go to `CONFIG.API_BASE + path`. The default base is the page's own origin, and `?api=https://host` overrides it. Request and response bodies are JSON.

A **decision** is `{ "measure_id": "m01", "district_id": "nura" | null }`. `district_id` is required only when the measure has `scope: "district"`.

## GET /api/dataset
The shape is identical to `web/mock/dataset.json`. Required fields:
- `budget` (100), `slots` (5), `max_per_direction` (2), `quarters` (8)
- `baseline_aqls` (52.56), `max_aqls` (57.24), `total_plans` (694395), `reelection_threshold` (50)
- `directions[]`: `{id, name}`
- `districts[]`: `{id, name, population, aqls, indices{<direction_id>: number}, deputy{name, initials}}`
- `measures[]`: `{id, name, direction, scope: "city"|"district", cost, lag, effects{<direction_id>: number}, description}`
- `example_plan[]`: a list of decisions. It powers the «Пример из ТЗ» button and should be the example plan from PLAN.md.

## POST /api/validate
Request: `{ decisions: Decision[], budget_limit?: number }`. `budget_limit` is sent during the crisis, when the limit is 85.

Response: `{ valid, errors: string[], message, budget_used, budget_left, budget_limit, direction_counts{dir: n} }`.
- `message` is shown verbatim, so it must be in Russian.
- When the plan is valid, it should say the remainder «не сгорает и не даёт бонуса».
- An incomplete plan with fewer than 5 decisions must return `valid: false` with a friendly message.

## POST /api/evaluate
Request: `{ decisions }`. The response is the **verdict**:
```json
{
  "aqls": 55.18, "baseline": 52.56, "delta": 2.62, "max_aqls": 57.24,
  "approval": 55.6, "reelection_threshold": 50, "reelected": true,
  "rank": 358063, "total_plans": 694395, "top_percent": 51.56,
  "budget_used": 80, "budget_left": 20,
  "decisions": [ ... ],
  "districts": [{ "id", "name", "before", "after", "delta", "indices_before": {}, "indices_after": {} }],
  "timeline": [{ "quarter": 0, "label": "Старт", "aqls": 52.56 }, "... 9 points, quarters 0..8"],
  "contributions": [{ "measure_id", "district_id", "name", "delta" }],
  "pareto": [{ "cost": 43, "best": 54.34 }],
  "user_point": { "cost": 80, "aqls": 55.18 },
  "analysis": {
    "strengths": [], "risks": [], "consequences": [], "tradeoffs": [],
    "recommendation": { "text": "...", "decisions": [ ... ] }
  },
  "mode": "llm" | "offline"
}
```
Field notes:
- `contributions` is the remove-one analysis: AQLS(plan) minus AQLS(plan without this measure).
- `pareto` holds the best achievable AQLS for each cost and drives the scatter chart.
- `recommendation.decisions` must be a full valid plan, because «Применить рекомендацию» loads it into the cabinet.
- Any `mode` other than `llm` or `online` shows the «офлайн-шаблон» badge.

## POST /api/narrative
Request: `{ decisions, verdict? }`. Response:
```json
{
  "mode": "llm" | "offline",
  "newspaper": {
    "masthead": "Астана Times", "edition": "IV квартал 2028", "issue": "№ 208",
    "headline": "...", "subheadline": "...",
    "lead": { "title": "...", "body": ["paragraph", "paragraph"] },
    "articles": [{ "title", "body" }],
    "editorial": { "title", "body" },
    "critical_zones": [{ "district", "aqls", "note" }],
    "stats": [{ "label", "value" }]
  },
  "council": [{ "district_id", "district", "deputy", "initials", "mood": "support"|"neutral"|"oppose", "reaction" }]
}
```

## POST /api/shock
Request: `{ decisions }`. Response: `{ id, title, quarter, description, budget_limit, effects: string[] }`.

## POST /api/shock/resolve
Request: `{ decisions, shock_id, remove_index, add: Decision }`.

Response: `{ ok: true, decisions, verdict, before, comment }`, where `before` is the old plan's verdict under the shock. On failure it returns `{ ok: false, validation }`.

## GET /api/leaderboard?session=room
Response: `{ session, entries: [{ team, aqls, approval, reelected, budget_used, ts }] }`. A bare array is also accepted.

## POST /api/leaderboard
Request: `{ team, session, decisions, aqls, approval }`. The backend should recompute the score from `decisions` and not trust the `aqls` the client sends.

## Nice-to-have
- **CORS:** allow all origins, so the page can run from a static host with `?api=`.
- **Static files:** serve `web/` at `/`, so a single ngrok URL works for the QR.
