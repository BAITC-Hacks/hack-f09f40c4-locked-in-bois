"""Pairwise official-score explanations; every displayed delta is A minus B.

Score is always evaluated by score._state, at full precision. Displayed weighted
terms are balanced per plan: round the average and critical penalty, then assign
the remaining displayed Score to the minimum term. Its explicit rounding
adjustment reconciles independent rounding without adding a fourth score term.
Consequently term deltas telescope exactly in decimal cents, including when the
plans are reversed. JSON consumers should sum in decimal or round sums to 2dp.
"""

from math import isclose

from .model import district_names, load_dataset, normalize_plan, plan_cost
from .optimize import optimize_info
from .score import _keys, _state, diff2
from .validate import validate


def _checked(plan):
    ok, reason = validate(plan)
    if not ok:
        raise ValueError(reason)
    return {"decisions": normalize_plan(plan)}


def _summary(plan, state):
    score, _, ds, crits, avg = state
    minimum = min(ds)
    return {
        "plan": plan, "score": round(score, 2), "cost": round(plan_cost(plan), 2),
        "d_avg": round(avg, 2), "d_min": round(minimum, 2),
        "d_min_districts": [name for name, value in zip(district_names(), ds)
                            if isclose(value, minimum, rel_tol=0, abs_tol=1e-10)],
        "n_crit": sum(crits),
    }


def _display_terms(state):
    weights = load_dataset()["score_weights"]
    score, _, ds, crits, avg = state
    average = round(weights["avg"] * avg, 2)
    critical = round(-weights["crit_penalty"] * sum(crits), 2)
    minimum = diff2(diff2(score, critical), average)
    adjustment = diff2(minimum, weights["min"] * min(ds))
    return {"avg": average, "min": minimum, "crit": critical}, adjustment


def _decision_diff(plan_a, plan_b):
    locations_a, locations_b = dict(_keys(plan_a)), dict(_keys(plan_b))
    cases = {d["name"]: d["cases"] for d in load_dataset()["districts"]}
    result = {"only_a": [], "only_b": [], "same_measure_different_district": [], "same": []}
    # Relocations are a separate category, not duplicated in only_a / only_b.
    for measure in load_dataset()["measures"]:
        mid = measure["id"]
        in_a, in_b = mid in locations_a, mid in locations_b
        if in_a and in_b:
            a, b = locations_a[mid], locations_b[mid]
            if a == b:
                result["same"].append({"measure": mid, "district": a})
            else:
                result["same_measure_different_district"].append({
                    "measure": mid, "name": measure["name"], "district_a": a, "district_b": b,
                    "description": (f"{mid} «{measure['name']}»: в плане А — в {cases[a]['loc']}, "
                                    f"в плане Б — в {cases[b]['loc']}."),
                })
        elif in_a or in_b:
            side, locations = ("only_a", locations_a) if in_a else ("only_b", locations_b)
            result[side].append({"measure": mid, "district": locations[mid]})
    return result


def duel(plan_a, plan_b) -> dict:
    """Compare two valid plans (dicts or bare lists), without mutating them.

    All deltas, including the three ``terms.*.delta`` values, mean A minus B.
    ``winner`` uses full precision; ``display_tie`` identifies equal 2dp scores.
    ``terms.min.rounding_adjustment_a/b`` disclose the display-only balancing.
    The three ``terms.*.a/b`` values also sum to each displayed plan score.
    """
    a, b = _checked(plan_a), _checked(plan_b)
    state_a, state_b = _state(_keys(a)), _state(_keys(b))
    summary_a, summary_b = _summary(a, state_a), _summary(b, state_b)
    values_a, adjustment_a = _display_terms(state_a)
    values_b, adjustment_b = _display_terms(state_b)
    weights = load_dataset()["score_weights"]
    labels = {"avg": "Средний индекс города", "min": "Минимальный индекс района",
              "crit": "Штраф за критические показатели"}
    formulas = {"avg": f"{weights['avg']:g}·ΔD_avg", "min": f"{weights['min']:g}·Δmin(D)",
                "crit": "−ΔN_crit" if weights["crit_penalty"] == 1
                else f"−{weights['crit_penalty']:g}·ΔN_crit"}
    terms = {
        key: {"label": labels[key], "formula": formulas[key],
              "a": values_a[key], "b": values_b[key], "delta": diff2(values_a[key], values_b[key])}
        for key in ("avg", "min", "crit")
    }
    terms["min"].update(rounding_adjustment_a=adjustment_a, rounding_adjustment_b=adjustment_b)
    delta = diff2(state_a[0], state_b[0])
    tied = isclose(state_a[0], state_b[0], rel_tol=0, abs_tol=1e-10)
    winner = "tie" if tied else "A" if state_a[0] > state_b[0] else "B"
    if tied:
        verdict = f"Планы А и Б равны по официальной оценке: {summary_a['score']:.2f}"
    elif delta == 0:
        letter = "А" if winner == "A" else "Б"
        verdict = (f"План {letter} лучше по точному расчёту, но после округления "
                   f"оба плана имеют оценку {summary_a['score']:.2f}")
    else:
        letter, other = ("А", "Б") if winner == "A" else ("Б", "А")
        verdict = f"План {letter} лучше плана {other} на {abs(delta):.2f} балла"
    verdict += (f"; разница А минус Б: средний индекс {terms['avg']['delta']:+.2f}, "
                f"минимальный индекс {terms['min']['delta']:+.2f}, "
                f"штраф за критические показатели {terms['crit']['delta']:+.2f}."
                )
    return {
        "a": summary_a, "b": summary_b, "score_delta": delta,
        "cost_delta": diff2(summary_a["cost"], summary_b["cost"]),
        "d_avg_delta": diff2(state_a[4], state_b[4]),
        "d_min_delta": diff2(min(state_a[2]), min(state_b[2])),
        "n_crit_delta": diff2(sum(state_a[3]), sum(state_b[3])),
        "winner": winner, "display_tie": delta == 0, "terms": terms,
        "rounding_note": ("Разности указаны как А минус Б. Слагаемые рассчитаны до округления "
                          "индексов; поправка округления включена в слагаемое минимального "
                          "индекса, чтобы сумма точно совпадала с разницей оценок до сотой."),
        "districts": {
            name: {"D_a": round(state_a[2][i], 2), "D_b": round(state_b[2][i], 2),
                   "delta_D": diff2(state_a[2][i], state_b[2][i]),
                   "n_crit_a": state_a[3][i], "n_crit_b": state_b[3][i],
                   "delta_n_crit": diff2(state_a[3][i], state_b[3][i])}
            for i, name in enumerate(district_names())
        },
        "decisions": _decision_diff(a, b), "verdict": verdict,
    }


def duel_vs_best(plan) -> dict:
    """Compare the submitted plan (A) to both cached benchmark plans (B).

    As in optimize_info, ``best_at_same_cost`` means the best plan costing
    *at most* the submitted cost, not necessarily exactly that cost. Only the
    existing lazily loaded optimizer cache is used; no enumeration or build.
    """
    plan = _checked(plan)
    info = optimize_info(plan)
    return {
        "best": duel(plan, info["best"]["plan"]),
        "best_at_same_cost": duel(plan, info["best_at_same_cost"]["plan"]),
        "cost_limit": round(plan_cost(plan), 2),
        "same_cost_label": "Лучший план с расходами не выше стоимости вашего плана",
    }
