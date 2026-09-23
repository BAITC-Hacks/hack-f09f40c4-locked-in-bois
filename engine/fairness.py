"""Descriptive equity metrics, independent of Score and political approval.

``fairness(plan)`` accepts the same valid plans as evaluate. Shares in the
money_vs_people table are percentages of population and actual spending (not
the budget cap). City costs are attributed by population; this accounting
convention does not claim equal benefits. Gini uses unrounded district D and
population weights, on the conventional 0..1 scale; gini_percent also exposes
it on a 0..100 scale so two-decimal output retains useful detail.

``price_of_spread()`` returns the exact-coverage frontier and maximizing plans.
Build its small independent cache with ``python -m engine.fairness``. Only
that build enumerates plans; request functions never enumerate. Full precision
selects maxima, with the first enumerated plan retained on exact score ties.
"""

import hashlib
import json
import time
from collections import Counter
from copy import deepcopy
from functools import lru_cache

from .model import REPO_ROOT, load_dataset, measures_by_id, normalize_plan, plan_cost
from .optimize import _plan, _valid_options
from .score import _keys, _state, diff2
from .validate import validate

CACHE_PATH = REPO_ROOT / "data/fairness_cache.json"
_SCHEMA = 1


def _weighted_gini(values, weights):
    """Sum(p_i p_j |D_i-D_j|)/(2 sum(p) sum(p_i D_i))."""
    mass = sum(weights)
    income = sum(p * value for p, value in zip(weights, values))
    if income == 0:
        return 0.0
    return sum(pa * pb * abs(a - b)
               for a, pa in zip(values, weights)
               for b, pb in zip(values, weights)) / (2 * mass * income)


def _change(before, after):
    return {"before": round(before, 2), "after": round(after, 2),
            "delta": diff2(after, before)}


def fairness(plan) -> dict:
    """Return spread, weighted Gini, spending attribution and tied gain leaders.

    most_gain/least_gain list all ties at the displayed delta_D precision.
    Spread is diff2(max(D), min(D)); every displayed difference uses diff2.
    Invalid plans raise the shared validator's Russian ValueError.
    """
    ok, reason = validate(plan)
    if not ok:
        raise ValueError(reason)
    decisions = normalize_plan(plan)
    districts = load_dataset()["districts"]
    measures = measures_by_id()
    before = _state(())[2]
    after = _state(_keys(decisions))[2]
    pops = [d["pop"] for d in districts]
    mass = sum(pops)
    total_cost = plan_cost(decisions)
    local_cost = Counter()
    projects = Counter()
    city_cost = 0
    for decision in decisions:
        measure = measures[decision["measure"]]
        if measure["type"] == "city":
            city_cost += measure["cost"]
        else:
            local_cost[decision["district"]] += measure["cost"]
            projects[decision["district"]] += 1
    rows = []
    for district, old, new in zip(districts, before, after):
        name = district["name"]
        share = district["pop"] / mass
        city_allocation = city_cost * share
        allocated = local_cost[name] + city_allocation
        money_pct = 100 * allocated / total_cost if total_cost else 0.0
        people_pct = 100 * share
        rows.append({
            "district": name, "D_before": round(old, 2), "D_after": round(new, 2),
            "delta_D": diff2(new, old), "district_projects": projects[name],
            "district_cost": round(local_cost[name], 2),
            "city_cost": round(city_allocation, 2), "allocated": round(allocated, 2),
            "budget_share_pct": round(money_pct, 2),
            "population_share_pct": round(people_pct, 2),
            "share_gap_pp": diff2(money_pct, people_pct),
        })
    spread = _change(diff2(max(before), min(before)), diff2(max(after), min(after)))
    gini_before = _weighted_gini(before, pops)
    gini_after = _weighted_gini(after, pops)
    most = max(row["delta_D"] for row in rows)
    least = min(row["delta_D"] for row in rows)
    leaders = [row["district"] for row in rows if row["delta_D"] == most]
    laggards = [row["district"] for row in rows if row["delta_D"] == least]
    cases = {d["name"]: d["cases"]["loc"] for d in districts}
    leader_places = ", ".join("в " + cases[name] for name in leaders)
    laggard_places = ", ".join("в " + cases[name] for name in laggards)
    direction = ("сократился" if spread["delta"] < 0 else
                 "увеличился" if spread["delta"] > 0 else "не изменился")
    verdict = (
        f"Разрыв D {direction}: {spread['before']:.2f} → {spread['after']:.2f}; "
        f"охват районными проектами — {len(projects)} из {len(districts)} районов; "
        f"наибольшее изменение D {leader_places} ({most:+.2f}), "
        f"наименьшее — {laggard_places} ({least:+.2f})."
    )
    return {
        "label": "Справедливость распределения — отдельно от Score и рейтинга акима",
        "spread": spread, "gini": _change(gini_before, gini_after),
        "gini_percent": _change(100 * gini_before, 100 * gini_after),
        "cost": round(total_cost, 2), "districts_with_project": len(projects),
        "districts_total": len(districts), "money_vs_people": rows,
        "money_vs_people_label": "Деньги vs люди",
        "most_gain": {"districts": leaders, "delta_D": most},
        "least_gain": {"districts": laggards, "delta_D": least},
        "verdict": verdict,
    }


def _fingerprint():
    digest = hashlib.sha256(json.dumps(load_dataset(), ensure_ascii=False,
                                      sort_keys=True).encode("utf-8"))
    for name in ("model.py", "score.py", "validate.py", "optimize.py"):
        # read_text normalizes CRLF, keeping the cache portable across systems.
        digest.update((REPO_ROOT / "engine" / name).read_text(encoding="utf-8").encode("utf-8"))
    return digest.hexdigest()


def _build_cache():
    best, counts = {}, Counter()
    overall = None
    for keys, cost in _valid_options():
        coverage = len({district for _, district in keys if district is not None})
        score = _state(keys)[0]
        counts[coverage] += 1
        if coverage not in best or score > best[coverage][0]:
            best[coverage] = (score, keys, cost)
        if overall is None or score > overall[0]:
            overall = (score, keys, cost)
    if overall is None:
        raise ValueError("Нет допустимых планов для расчёта цены охвата")
    rows = []
    for coverage in range(1, len(load_dataset()["districts"]) + 1):
        candidate = best.get(coverage)
        record = ({"score": round(candidate[0], 2), "plan": _plan(candidate[1]),
                   "cost": round(candidate[2], 2)} if candidate else None)
        rows.append({"district_count": coverage, "count_feasible": counts[coverage],
                     "best": record,
                     "price": diff2(overall[0], candidate[0]) if candidate else None})
    return {
        "schema": _SCHEMA, "fingerprint": _fingerprint(),
        "dataset_version": load_dataset()["version"],
        "label": "Цена охвата районов: лучший Score при точном числе районов с проектами",
        "coverage_mode": "exact", "total_valid": sum(counts.values()),
        "unconstrained_best": round(overall[0], 2), "rows": rows,
    }


@lru_cache(maxsize=1)
def _load_cache():
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Нет корректного кэша справедливости: выполните python -m engine.fairness") from exc
    if cache.get("schema") != _SCHEMA or cache.get("fingerprint") != _fingerprint():
        raise ValueError("Кэш справедливости устарел: выполните python -m engine.fairness")
    return cache


def price_of_spread() -> dict:
    """Best Score for EXACTLY each district count, with loss from global best.

    An infeasible count has best=None and price=None. The returned object is
    independent of the lazy cache and can safely be modified by its caller.
    """
    return deepcopy(_load_cache())


if __name__ == "__main__":
    started = time.perf_counter()
    cache = _build_cache()
    encoded = json.dumps(cache, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) >= 200_000:
        raise ValueError("Кэш справедливости превышает лимит размера")
    CACHE_PATH.write_text(encoded, encoding="utf-8")
    print(f"Время: {time.perf_counter() - started:.2f} с; "
          f"планов: {cache['total_valid']}; размер: {CACHE_PATH.stat().st_size} байт")
