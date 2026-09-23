# API contract — «Кабинет акима»

Base URL: `http://localhost:8000`. All bodies are JSON, UTF-8. CORS is open. The frontend is served by the same app at `/`.

This contract follows the implemented routes in `api/main.py`: the PLAN.md §4 endpoints, additive fields (marked **+**), the §12 endpoints, and stress tests, promise pricing and move grading. Full examples for the new endpoints below come from running the real engine through the API.

## Conventions

- **Plan format, everywhere:** `{"decisions":[{"measure":"M7","district":"Нура"}, {"measure":"M12","district":null}, ...]}`, exactly 5 decisions for scoring. City-type measures have `district: null`.
- District names are the Russian names from the dataset: `Есиль`, `Алматы`, `Сарыарка`, `Байконур`, `Нура`.
- **Indicator arrays** (`before`, `after`) have 10 values in the order of `dataset.indicators`: `T1 T2 E1 E2 S1 S2 B1 B2 C1 C2`.
- All scores are floats rounded to 2 decimals. Every number comes from `engine/`. The LLM never computes numbers.
- Displayed differences use `engine.score.diff2(a, b)`: subtract the two rounded values. JSON numbers need not retain trailing zeros. `realized_share` retains exact ratio precision.
- Plan endpoints accept either `{"plan": {"decisions": [...]}}` or `{"decisions": [...]}`; `/api/promise` and `/api/duel` have their own bodies described below.
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
  - **+** `comment: str` — Russian explanation from `api.agent.swap_comment`, grounded in the resolved crisis result, with an offline fallback. It describes the crisis loss, the effect of the mandatory swap and, when useful, a better swap.
  - `approval` is recomputed for `new_plan` against the crisis-adjusted baseline. `swap_comment` internally returns `{comment: str, provider: "openai"|"nvidia"|"offline", grounded: bool}`; the current HTTP route forwards only `comment`, so `provider` and `grounded` are not currently response fields here.
  - Verified offline example: the doc plan with `event_id: "smog_saryarka"`, replacing M12 with M4 in Сарыарка, returns `score: 56.04`, `crisis_cost: 1.15`, `recovered: 0.65`, and `comment: "Кризис стоил 1.15 балла. Ваша замена вернула 0.65 балла — это лучший возможный ход при обязательной замене."`.
- `POST /api/submit` {team, plan, score} → `{ok: true, id, rank}`. **The server recomputes the score from the plan and ignores the client-sent `score`**, so the leaderboard can't be spoofed.
- `GET /api/leaderboard` → `[{team, score, cost, approval, plan, created_at}]` sorted by score desc (top 50).
- `GET /api/brief?plan=<urlencoded plan JSON>` → `text/markdown` one-pager (also accepts `POST /api/brief` {plan}).

## §12 endpoints

### `POST /api/approval` {plan}

Political-risk layer, deterministic, in `engine/approval.py`. **Not part of the official Score (AQLS).** The UI must label it "рейтинг акима — слой политического риска, не входит в Score".
```
{ "city": 50.78, "threshold": 50, "reelected": true,
  "districts": { "Нура": {"approval": 75.13, "delta_D": 3.78, "got_district_measure": true, "crit_cells": 0}, ... } }
```

### `POST /api/narrative` {plan, lang:"ru", event_id?: str, swap?: {...}}

One call returns the District Council and the newspaper front page. Offline mode fills the same shape from templates.
```
{ "council": [
    { "district": "Нура", "deputy": "Депутат от Нуры", "mood": "positive|neutral|negative",
      "quote": "…", "delta_D": 3.78, "approval": 75.13 }
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

## Новые функции

Ниже приведены реальные ответы текущих функций движка, полученные через FastAPI `TestClient`. Пример плана во всех запросах: M7 Нура, M8 Нура, M10 Нура, M12 город, M5 Сарыарка. Порядок решений сохранён для разбора партии. Числа JSON могут записываться без конечных нулей; вычисляемые оценки и отображаемые разности округлены до двух знаков, разности — через `engine.score.diff2`.

Общие типы для схем ниже: `Decision = {measure: str, district: str|null}`, `Plan = {decisions: Decision[]}`, `Cell = {district: str, indicator: str, value: number}`. Поля обязательны, если не помечены `?`; `null` — значение поля, а не его отсутствие.

### `POST /api/stress`

Запрос: `{plan: Plan}` либо сам `Plan`. Нужен полный допустимый план; ошибки валидации возвращаются как HTTP 422 с русским `detail`.

```json
{
  "plan": {
    "decisions": [
      {
        "measure": "M7",
        "district": "Нура"
      },
      {
        "measure": "M8",
        "district": "Нура"
      },
      {
        "measure": "M10",
        "district": "Нура"
      },
      {
        "measure": "M12",
        "district": null
      },
      {
        "measure": "M5",
        "district": "Сарыарка"
      }
    ]
  }
}
```

Схема ответа:

```text
{
  score: number,
  scenarios: [{
    event_id: str, title: str, district: str|null,
    effects: {indicator: number}|{district: {indicator: number}},
    score: number, loss: number,
    new_crit_cells: Cell[],
    insurance: {out: str, in: Decision, score: number, recovered: number}|null
  }],
  worst_event: str, worst_score: number, max_loss: number,
  worst_score_all: number, max_loss_all: number,
  robust_rank: int, robust_total: int, robust_percentile: number,
  crisis_proof_plan: {
    plan: Plan, score: number, cost: number, approval: number,
    worst_event: str, worst_score: number, max_loss: number,
    worst_score_all: number, max_loss_all: number, all_score: number,
    event_scores: {event_id: number}
  },
  verdict: str
}
```

`scenarios` содержит события из `data/events.json` в их порядке, затем объединённый сценарий `event_id: "all"` («Чёрная зима»). Для него `district: null`, а `effects` сгруппированы по районам. `new_crit_cells` — ячейки, ставшие критическими относительно того же плана без кризиса. `insurance` — лучшая реальная одиночная замена при данном кризисе; `recovered` может быть отрицательным.

`worst_event`, `worst_score`, `max_loss` и место по устойчивости учитывают **только одиночные кризисы, без замены**. `worst_score_all` учитывает и совместное наступление событий. `robust_rank` использует округлённые худшие оценки, равные оценки делят место; `robust_percentile` — процент планов со строго худшим результатом. `crisis_proof_plan` максимизирует худший одиночный Score по полному перебору; при равенстве выбирается более высокий обычный Score. Его `score` и `approval` относятся к обычным условиям.

<details><summary>Полный ответ для примера из условия</summary>

```json
{
  "score": 56.54,
  "scenarios": [
    {
      "event_id": "heating_almaty",
      "title": "Прорыв теплотрассы в Алматы",
      "district": "Алматы",
      "effects": {
        "C1": -12
      },
      "score": 55.34,
      "loss": 1.2,
      "new_crit_cells": [
        {
          "district": "Алматы",
          "indicator": "C1",
          "value": 38
        }
      ],
      "insurance": {
        "out": "M5",
        "in": {
          "measure": "M14",
          "district": null
        },
        "score": 56.78,
        "recovered": 1.44
      }
    },
    {
      "event_id": "smog_saryarka",
      "title": "Смоговая зима в Сарыарке",
      "district": "Сарыарка",
      "effects": {
        "E2": -10
      },
      "score": 55.39,
      "loss": 1.15,
      "new_crit_cells": [
        {
          "district": "Сарыарка",
          "indicator": "E2",
          "value": 38.75
        }
      ],
      "insurance": {
        "out": "M12",
        "in": {
          "measure": "M4",
          "district": "Сарыарка"
        },
        "score": 56.04,
        "recovered": 0.65
      }
    },
    {
      "event_id": "schools_esil",
      "title": "Переполнение школ в Есиле",
      "district": "Есиль",
      "effects": {
        "S1": -8
      },
      "score": 56.38,
      "loss": 0.16,
      "new_crit_cells": [],
      "insurance": {
        "out": "M5",
        "in": {
          "measure": "M3",
          "district": "Нура"
        },
        "score": 57.04,
        "recovered": 0.66
      }
    },
    {
      "event_id": "all",
      "title": "Чёрная зима: все три кризиса сразу",
      "district": null,
      "effects": {
        "Алматы": {
          "C1": -12
        },
        "Сарыарка": {
          "E2": -10
        },
        "Есиль": {
          "S1": -8
        }
      },
      "score": 54.02,
      "loss": 2.52,
      "new_crit_cells": [
        {
          "district": "Алматы",
          "indicator": "C1",
          "value": 38
        },
        {
          "district": "Сарыарка",
          "indicator": "E2",
          "value": 38.75
        }
      ],
      "insurance": {
        "out": "M5",
        "in": {
          "measure": "M14",
          "district": null
        },
        "score": 55.46,
        "recovered": 1.44
      }
    }
  ],
  "worst_event": "heating_almaty",
  "worst_score": 55.34,
  "max_loss": 1.2,
  "worst_score_all": 54.02,
  "max_loss_all": 2.52,
  "robust_rank": 623,
  "robust_total": 694395,
  "robust_percentile": 99.91,
  "crisis_proof_plan": {
    "plan": {
      "decisions": [
        {
          "measure": "M14",
          "district": null
        },
        {
          "measure": "M5",
          "district": "Сарыарка"
        },
        {
          "measure": "M6",
          "district": null
        },
        {
          "measure": "M8",
          "district": "Нура"
        },
        {
          "measure": "M9",
          "district": "Нура"
        }
      ]
    },
    "score": 56.49,
    "cost": 91,
    "approval": 52.21,
    "worst_event": "heating_almaty",
    "worst_score": 56.29,
    "max_loss": 0.2,
    "worst_score_all": 55.97,
    "max_loss_all": 0.52,
    "all_score": 55.97,
    "event_scores": {
      "heating_almaty": 56.29,
      "smog_saryarka": 56.34,
      "schools_esil": 56.33,
      "all": 55.97
    }
  },
  "verdict": "Худший отдельный кризис — «Прорыв теплотрассы в Алматы»: оценка 55.34, снижение на 1.20; «чёрная зима» — 54.02."
}
```

</details>

### `POST /api/promise`

Запрос: `{promises: Promise[], plan?: Plan|null}`. `promises` обязателен, пустой список допустим. Необязательный `plan` нужен только для проверки ваших обещаний и не ограничивает поиск оптимума; передавать его нужно именно в поле `plan`.

Допустимые формы `Promise` (лишние поля внутри обещания запрещены):

| Форма | Смысл |
|---|---|
| `{"type":"include","measure":str,"district"?:str\|null}` | Включить меру; без района или с `null` районная мера разрешена в любом районе. У городской меры район только `null` или отсутствует. |
| `{"type":"exclude","measure":str}` | Исключить меру во всех районах. |
| `{"type":"district_project","district":str}` | Хотя бы одна районная мера в указанном районе. |
| `{"type":"min_districts","value":int}` | Не менее указанного числа районов с районными мерами; значение неотрицательное. |
| `{"type":"max_cost","value":number}` | Максимальная стоимость плана; значение неотрицательное. |
| `{"type":"min_approval","value":number}` | Рейтинг не ниже значения в диапазоне от 0 до 100. |
| `{"type":"no_critical"}` | Ни одной критической ячейки после реализации плана. |
| `{"type":"min_district_delta","district":str,"value":number}` | Прирост индекса D указанного района не ниже значения. |

Числовые значения должны быть конечными числами, не boolean. Пороги проверяются с полной точностью до округления, включая прирост D; общегородские меры не считаются районными проектами. Все обещания выполняются одновременно. Неизвестный тип, мера, район, лишнее/отсутствующее поле, некорректное число или переданный невалидный план дают HTTP 422. Невыполнимое сочетание корректных обещаний даёт HTTP 200 с `feasible: false`.

```json
{
  "promises": [
    {
      "type": "min_approval",
      "value": 50
    }
  ],
  "plan": {
    "decisions": [
      {
        "measure": "M7",
        "district": "Нура"
      },
      {
        "measure": "M8",
        "district": "Нура"
      },
      {
        "measure": "M10",
        "district": "Нура"
      },
      {
        "measure": "M12",
        "district": null
      },
      {
        "measure": "M5",
        "district": "Сарыарка"
      }
    ]
  }
}
```

Схема ответа:

```text
PromiseRow = Promise + {label: str, feasible_alone: bool, price_alone: number|null}
{
  promises: PromiseRow[],
  feasible: bool, count_feasible: int, total_valid: int,
  best: {plan: Plan, score: number, cost: number, approval: number}|null,
  unconstrained_best: number, price: number|null, verdict: str,
  your_plan?: {score: number, keeps_promises: bool, broken: str[]}
}
```

`price_alone` — цена каждого обещания по отдельности, `price` — цена их совместного выполнения в баллах Score относительно `unconstrained_best`. При невозможности соответствующая цена равна `null`; при невозможности всего набора также `best: null` и `count_feasible: 0`, а `verdict` объясняет конфликт. `your_plan` присутствует только при переданном ненулевом `plan`; `broken` содержит русские названия нарушенных обещаний.

Полный ответ:

```json
{
  "promises": [
    {
      "type": "min_approval",
      "value": 50,
      "label": "Рейтинг ≥ 50",
      "feasible_alone": true,
      "price_alone": 0.24
    }
  ],
  "feasible": true,
  "count_feasible": 657031,
  "total_valid": 694395,
  "best": {
    "plan": {
      "decisions": [
        {
          "measure": "M11",
          "district": "Есиль"
        },
        {
          "measure": "M14",
          "district": null
        },
        {
          "measure": "M3",
          "district": "Нура"
        },
        {
          "measure": "M7",
          "district": "Нура"
        },
        {
          "measure": "M8",
          "district": "Нура"
        }
      ]
    },
    "score": 57,
    "cost": 100,
    "approval": 53.35
  },
  "unconstrained_best": 57.24,
  "price": 0.24,
  "verdict": "Обещание выполнимо. Цена в баллах Score — 0.24: лучший план даёт 57.00 вместо 57.24.",
  "your_plan": {
    "score": 56.54,
    "keeps_promises": true,
    "broken": []
  }
}
```

Для `promises: [{"type":"min_districts","value":5}]` и того же плана ответ содержит `count_feasible: 14520`, `best.score: 54.82`, `best.cost: 91`, `best.approval: 64.86`, `price: 2.42` и `your_plan.keeps_promises: false`.

### `GET /api/promise/catalog`

Тело запроса и параметры не нужны. Ответ — **массив без обёртки**. Каждая строка имеет схему `PromiseRow + {promises: Promise[], price: number|null, feasible: bool, best_score: number|null}`: готовый список для отправки, цена, выполнимость и лучший Score. Плоские поля исходного обещания, `price_alone` и `feasible_alone` также сохранены.

```json
[
  {
    "type": "min_districts",
    "value": 5,
    "label": "Каждому району — свой проект",
    "feasible_alone": true,
    "price_alone": 2.42,
    "promises": [
      {
        "type": "min_districts",
        "value": 5
      }
    ],
    "price": 2.42,
    "feasible": true,
    "best_score": 54.82
  },
  {
    "type": "min_approval",
    "value": 50,
    "label": "Рейтинг ≥ 50",
    "feasible_alone": true,
    "price_alone": 0.24,
    "promises": [
      {
        "type": "min_approval",
        "value": 50
      }
    ],
    "price": 0.24,
    "feasible": true,
    "best_score": 57
  },
  {
    "type": "include",
    "measure": "M7",
    "district": "Есиль",
    "label": "Школа + детсад в Есиле",
    "feasible_alone": true,
    "price_alone": 1.2,
    "promises": [
      {
        "measure": "M7",
        "district": "Есиль",
        "type": "include"
      }
    ],
    "price": 1.2,
    "feasible": true,
    "best_score": 56.04
  },
  {
    "type": "exclude",
    "measure": "M3",
    "label": "Без ЛРТ",
    "feasible_alone": true,
    "price_alone": 0.17,
    "promises": [
      {
        "measure": "M3",
        "type": "exclude"
      }
    ],
    "price": 0.17,
    "feasible": true,
    "best_score": 57.07
  },
  {
    "type": "max_cost",
    "value": 80,
    "label": "Бюджет — не больше 80",
    "feasible_alone": true,
    "price_alone": 0.37,
    "promises": [
      {
        "type": "max_cost",
        "value": 80
      }
    ],
    "price": 0.37,
    "feasible": true,
    "best_score": 56.87
  },
  {
    "type": "no_critical",
    "label": "Без красных зон",
    "feasible_alone": true,
    "price_alone": 0,
    "promises": [
      {
        "type": "no_critical"
      }
    ],
    "price": 0,
    "feasible": true,
    "best_score": 57.24
  },
  {
    "type": "district_project",
    "district": "Алматы",
    "label": "Проект в Алматы",
    "feasible_alone": true,
    "price_alone": 0.31,
    "promises": [
      {
        "district": "Алматы",
        "type": "district_project"
      }
    ],
    "price": 0.31,
    "feasible": true,
    "best_score": 56.93
  },
  {
    "type": "district_project",
    "district": "Есиль",
    "label": "Проект в Есиле",
    "feasible_alone": true,
    "price_alone": 0.24,
    "promises": [
      {
        "district": "Есиль",
        "type": "district_project"
      }
    ],
    "price": 0.24,
    "feasible": true,
    "best_score": 57
  }
]
```

Для запроса `POST /api/promise` передавайте `{"promises": row.promises, "plan": ваш_план}`. Также можно извлечь из строки только поля формы `Promise`: `type` и соответствующие `measure`, `district`, `value`. Всю строку каталога передавать как обещание нельзя: дополнительные поля ответа во входном обещании запрещены.

### `POST /api/grade`

Запрос: `{plan: Plan}` либо сам `Plan`, как в примере `POST /api/stress`. План должен содержать ровно пять допустимых решений. Порядок массива сохраняется: `moves` и `eval_bar` описывают именно эту последовательность; итоговый Score от порядка не зависит.

Схема ответа:

```text
NamedDecision = Decision + {name: str}
Alternative = NamedDecision + {score: number}
Move = {
  index: int, measure: str, district: str|null, name: str,
  grade: "best"|"excellent"|"good"|"inaccuracy"|"mistake"|"blunder"|"sacrifice",
  symbol: str, label: str, loss: number,
  best_alternative: Alternative|null, comment: str, traps: Trap[],
  contribution: number,
  score_best_alternative: Alternative|null,
  score_best_alternative_approval: number|null,
  approval: number, best_alternative_approval: number|null
}
{
  score: number, accuracy: number, moves: Move[],
  eval_bar: [
    {step: 0, ceiling: number},
    {step: int, ceiling: number|null, dead_end: bool,
     decision: NamedDecision, drop: number|null, reason?: str}
  ],
  biggest_blunder: Move|null, summary: str
}
```

Первая запись `eval_bar` содержит только `step` и `ceiling`; далее следует запись для каждого решения. `index` начинается с 1. У допустимого полного плана все его префиксы имеют допустимое завершение, поэтому `dead_end: false`, `ceiling`/`drop` числовые, `reason` отсутствует. Движок также предоставляет `prefix_ceiling` для частичных планов, но отдельного HTTP-маршрута для него нет.

`Trap` — одна из точных форм:

```text
{kind: "critical_cell"|"critical_rescue", district: str, indicator: str, before: number, after: number}
{kind: "lag", realized_percent: number}
{kind: "synergy", pair: [str, str], gain: number}
{kind: "missed_synergy", pair: [str, str], replacement: NamedDecision, score: number, gain: number}
```

`loss` сравнивает замену одного хода при остальных четырёх фиксированных. Пороги включительны: 0 → `best` («!!», «Лучший ход»); до 0.10 → `excellent` («!», «Отличный ход»); до 0.25 → `good` (пустой символ, «Хороший ход»); до 0.50 → `inaccuracy` («?!», «Неточность»); до 1.00 → `mistake` («?», «Ошибка»); выше → `blunder` («??», «Зевок»). `contribution` — вклад remove-one, он отличается от `loss`.

Если текущий план обеспечивает переизбрание, а лучшие по Score замены его лишают, `best_alternative` и `loss` используют лучшую сохраняющую переизбрание замену. Когда ни одна такая замена не повышает отображаемый Score, ход получает `sacrifice` («!?», «Жертва»), сохраняя числовую потерю относительно максимума Score. `score_best_alternative` сохраняет неограниченную альтернативу. У `best` альтернативы и их рейтинги равны `null`; поле `approval` — городской рейтинг текущего плана, а не района.

`accuracy = round(100 * R / (R + sum(loss)), 2)`, где `R = max(0.01, diff2(глобальный потолок, база))`. Локальные потери могут перекрываться, поэтому их сумма не равна отставанию от глобального оптимума. Точность 100 возможна и у локального оптимума по одиночным заменам. `eval_bar` показывает точный максимальный Score при сохранении каждого префикса; его падения суммируются в общее отставание. `biggest_blunder` — ход с наибольшей потерей среди `inaccuracy`, `mistake`, `blunder`, иначе `null`.

<details><summary>Полный ответ для примера из условия</summary>

```json
{
  "score": 56.54,
  "accuracy": 91.94,
  "moves": [
    {
      "index": 1,
      "measure": "M7",
      "district": "Нура",
      "name": "Школа + детсад (модульное строительство)",
      "grade": "best",
      "symbol": "!!",
      "label": "Лучший ход",
      "loss": 0,
      "best_alternative": null,
      "comment": "Это уже лучший ход по Score при остальных фиксированных решениях. Снят критический штраф в Нуре: S1 растёт с 38.00 до 48.00.",
      "traps": [
        {
          "kind": "critical_rescue",
          "district": "Нура",
          "indicator": "S1",
          "before": 38,
          "after": 48
        }
      ],
      "contribution": 1.45,
      "score_best_alternative": null,
      "score_best_alternative_approval": null,
      "approval": 50.78,
      "best_alternative_approval": null
    },
    {
      "index": 2,
      "measure": "M8",
      "district": "Нура",
      "name": "Центр семейного здоровья / поликлиника",
      "grade": "best",
      "symbol": "!!",
      "label": "Лучший ход",
      "loss": 0,
      "best_alternative": null,
      "comment": "Это уже лучший ход по Score при остальных фиксированных решениях. Снят критический штраф в Нуре: S2 растёт с 35.00 до 43.75.",
      "traps": [
        {
          "kind": "critical_rescue",
          "district": "Нура",
          "indicator": "S2",
          "before": 35,
          "after": 43.75
        }
      ],
      "contribution": 1.39,
      "score_best_alternative": null,
      "score_best_alternative_approval": null,
      "approval": 50.78,
      "best_alternative_approval": null
    },
    {
      "index": 3,
      "measure": "M10",
      "district": "Нура",
      "name": "Освещение и камеры (расширение Safe City)",
      "grade": "excellent",
      "symbol": "!",
      "label": "Отличный ход",
      "loss": 0.09,
      "best_alternative": {
        "measure": "M14",
        "district": null,
        "name": "Аварийные бригады ЖКХ + раннее оповещение",
        "score": 56.63
      },
      "comment": "При остальных решениях сильнее M14 «Аварийные бригады ЖКХ + раннее оповещение» по всему городу: Score 56.63 вместо 56.54; потеря — 0.09. Замена поднимает средний индекс города с 58.08 до 58.48. Работает связка M10 + M12: сам бонус добавляет 0.07 к Score.",
      "traps": [
        {
          "kind": "synergy",
          "pair": [
            "M10",
            "M12"
          ],
          "gain": 0.07
        }
      ],
      "contribution": 0.53,
      "score_best_alternative": {
        "measure": "M14",
        "district": null,
        "name": "Аварийные бригады ЖКХ + раннее оповещение",
        "score": 56.63
      },
      "score_best_alternative_approval": 52.41,
      "approval": 50.78,
      "best_alternative_approval": 52.41
    },
    {
      "index": 4,
      "measure": "M12",
      "district": null,
      "name": "Единая цифровая платформа обращений",
      "grade": "excellent",
      "symbol": "!",
      "label": "Отличный ход",
      "loss": 0.1,
      "best_alternative": {
        "measure": "M14",
        "district": null,
        "name": "Аварийные бригады ЖКХ + раннее оповещение",
        "score": 56.64
      },
      "comment": "При остальных решениях сильнее M14 «Аварийные бригады ЖКХ + раннее оповещение» по всему городу: Score 56.64 вместо 56.54; потеря — 0.10. Замена поднимает средний индекс города с 58.08 до 58.22. Работает связка M10 + M12: сам бонус добавляет 0.07 к Score.",
      "traps": [
        {
          "kind": "synergy",
          "pair": [
            "M10",
            "M12"
          ],
          "gain": 0.07
        }
      ],
      "contribution": 0.51,
      "score_best_alternative": {
        "measure": "M14",
        "district": null,
        "name": "Аварийные бригады ЖКХ + раннее оповещение",
        "score": 56.64
      },
      "score_best_alternative_approval": 51.37,
      "approval": 50.78,
      "best_alternative_approval": 51.37
    },
    {
      "index": 5,
      "measure": "M5",
      "district": "Сарыарка",
      "name": "Перевод частного сектора на чистое топливо",
      "grade": "good",
      "symbol": "",
      "label": "Хороший ход",
      "loss": 0.22,
      "best_alternative": {
        "measure": "M3",
        "district": "Есиль",
        "name": "Линия ЛРТ / расширение",
        "score": 56.76
      },
      "comment": "При остальных решениях сильнее M3 «Линия ЛРТ / расширение» в Есиле: Score 56.76 вместо 56.54; потеря — 0.22. Замена поднимает средний индекс города с 58.08 до 58.38. Замена сохраняет переизбрание: рейтинг 53.53.",
      "traps": [],
      "contribution": 0.17,
      "score_best_alternative": {
        "measure": "M3",
        "district": "Нура",
        "name": "Линия ЛРТ / расширение",
        "score": 57.21
      },
      "score_best_alternative_approval": 46.7,
      "approval": 50.78,
      "best_alternative_approval": 53.53
    }
  ],
  "eval_bar": [
    {
      "step": 0,
      "ceiling": 57.24
    },
    {
      "step": 1,
      "ceiling": 57.21,
      "dead_end": false,
      "decision": {
        "measure": "M7",
        "district": "Нура",
        "name": "Школа + детсад (модульное строительство)"
      },
      "drop": 0.03
    },
    {
      "step": 2,
      "ceiling": 57.21,
      "dead_end": false,
      "decision": {
        "measure": "M8",
        "district": "Нура",
        "name": "Центр семейного здоровья / поликлиника"
      },
      "drop": 0
    },
    {
      "step": 3,
      "ceiling": 57.21,
      "dead_end": false,
      "decision": {
        "measure": "M10",
        "district": "Нура",
        "name": "Освещение и камеры (расширение Safe City)"
      },
      "drop": 0
    },
    {
      "step": 4,
      "ceiling": 57.21,
      "dead_end": false,
      "decision": {
        "measure": "M12",
        "district": null,
        "name": "Единая цифровая платформа обращений"
      },
      "drop": 0
    },
    {
      "step": 5,
      "ceiling": 56.54,
      "dead_end": false,
      "decision": {
        "measure": "M5",
        "district": "Сарыарка",
        "name": "Перевод частного сектора на чистое топливо"
      },
      "drop": 0.67
    }
  ],
  "biggest_blunder": null,
  "summary": "Score 56.54 из достижимых 57.24: потеря потолка 0.70; точность 91.94%. Наибольшая цена одиночного выбора — 0.22 у M5 в Сарыарке."
}
```

</details>


### `POST /api/receipt`

Тело: `{"plan": {"decisions": [...]}}` (также принимается план без обёртки).
Вызывает `engine.receipt.receipt`: возвращает `score`, `cost`, `d_avg`, `d_min`,
`d_min_district`, `n_crit`, `lines`, `districts`, `critical_penalties`, `decisions`,
`application_order`, `synergy_lines`, `clip_notes` и пояснения округления.
В итог складываются только строки `lines` с `additive: true`; промежуточные
итоги повторно не прибавляются. `rounding_adjustment` уже включена в `amount`.
Невалидный план → 422 с причиной валидатора.

### `POST /api/duel`

Тело: `{"plan_a": {"decisions": [...]}, "plan_b": {"decisions": [...]}}`.
Вызывает `engine.duel.duel`: возвращает сводки `a`, `b` (план, Score, стоимость,
районные индексы и критические ячейки), `score_delta`, `cost_delta`, `d_avg_delta`,
`d_min_delta`, `n_crit_delta`, `winner: "A" | "B" | "tie"`, `display_tie`,
`terms`, `districts`, `decisions`, `verdict`, `rounding_note`.
Все разности имеют направление **А минус Б** и рассчитаны через `diff2`.
`terms.avg`, `terms.min`, `terms.crit` содержат `a`, `b`, `delta` — вклады
среднего индекса, минимального индекса и штрафа; поправка округления включена
в слагаемое минимума. Победитель определяется до округления.

Если ключ `plan_b` отсутствует, вызывается `engine.duel.duel_vs_best`:
ответ содержит `best`, `best_at_same_cost` (каждый — полное сравнение выше),
`cost_limit`, `same_cost_label`. Второй эталон имеет стоимость **не выше**
стоимости плана А. Отсутствующий или невалидный `plan_a`, а также переданный
невалидный `plan_b` (включая `null`) → 422 с причиной валидатора.

### `POST /api/fairness`

Тело: `{"plan": {"decisions": [...]}}` (также принимается план без обёртки).
Вызывает `engine.fairness.fairness`: возвращает `spread`, `gini`, `gini_percent`
(каждый содержит `before`, `after`, `delta`), `cost`, `districts_with_project`,
`districts_total`, `money_vs_people`, `most_gain`, `least_gain`, `verdict` и подписи.
Джини взвешен по населению; `gini_percent` — та же величина в процентах.
Общегородские расходы распределяются пропорционально населению для учёта затрат;
это не утверждение о равенстве пользы. Метрики не входят в Score или рейтинг акима.
Невалидный план → 422 с причиной валидатора.

### `POST /api/calendar`

Тело: `{"plan": {"decisions": [...]}}` (также принимается план без обёртки).
Вызывает `engine.calendar.calendar`: возвращает `score`, `threshold`,
`horizon_quarters`, `timeline`, `cells`, `districts`, `n_crit`, `crit_cells`,
`waiting_quarter_cells`, `waiting_quarters`, `thin_margin_cells` и подписи.
`timeline` включает исходное состояние; `waiting_quarter_cells` — сумма числа
критических ячеек на отметках кварталов 1–8, без исходного состояния и без
взвешивания по населению. Единица — квартал-ячейка, а не часы ожидания жителей.
Для каждой ячейки `first_cleared_q` — первый выход из красной зоны,
`cleared_q` — выход после последнего критического квартала; `null` означает,
что выхода в пределах горизонта нет. `thin_margin_cells` содержит ранее
критические ячейки с конечным запасом до порога менее одного балла.
Невалидный план → 422 с причиной валидатора.

Проверенные ответы новых маршрутов через `TestClient` (пример из условия и
глобальный оптимум из `/api/optimize`; для дуэли `plan_b` опущен):

| Поле ответа | Пример из условия | Глобальный оптимум |
|---|---:|---:|
| receipt: `score` / `cost` | 56.54 / 95 | 57.24 / 98 |
| receipt: `d_avg` / `d_min` | 58.08 / 52.96 | 58.58 / 54.09 |
| duel: `best.score_delta` | −0.70 | 0.00 |
| duel: `best_at_same_cost.score_delta` | −0.69 | 0.00 |
| fairness: `spread.after` / `spread.delta` | 10.47 / −3.34 | 10.02 / −3.79 |
| fairness: `gini_percent.after` | 3.27 | 3.34 |
| fairness: `districts_with_project` | 2 | 1 |
| calendar: `waiting_quarter_cells` | 8 | 10 |
| calendar: `n_crit` | 0 | 0 |

## Status

| Endpoint | Status |
|---|---|
| everything above | **implemented and tested** (`pytest -q`). Live: `uvicorn api.main:app` → `/docs` |
