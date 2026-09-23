"""Exhaustive optimizer and cached comparisons. Run: python -m engine.optimize."""

import heapq
import json
import time
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from itertools import combinations

from .approval import CONSTANTS, _approval_values
from .model import REPO_ROOT, district_names, load_dataset, normalize_plan, plan_cost
from .score import _keys, _state, diff2, quick_score
from .validate import validate

CACHE_PATH = REPO_ROOT / "data/optimizer_cache.json"


@lru_cache(maxsize=1)
def _options():
    return tuple((m["id"], d) for m in load_dataset()["measures"]
                 for d in (district_names() if m["type"] == "district" else [None]))


def _plan(keys):
    return {"decisions": [{"measure": mid, "district": district} for mid, district in keys]}


def _valid_options():
    """Enumerate combinations of all options, pruning with dataset-derived masks.

    A mask admitted here has exactly the required number of distinct measures,
    fits the budget/direction limits and has no global conflict. Location
    conflicts are checked after this inexpensive first filter.
    """
    data = load_dataset()
    measures = data["measures"]
    bits = {m["id"]: 1 << i for i, m in enumerate(measures)}
    allowed = {}
    for selected in combinations(measures, data["decisions_required"]):
        cost = sum(m["cost"] for m in selected)
        if cost > data["budget"]:
            continue
        if max(Counter(m["direction"] for m in selected).values()) > data["max_per_direction"]:
            continue
        ids = {m["id"] for m in selected}
        if any(r["scope"] == "anywhere" and set(r["pair"]) <= ids for r in data["incompatibilities"]):
            continue
        allowed[sum(bits[mid] for mid in ids)] = cost
    local = [r["pair"] for r in data["incompatibilities"] if r["scope"] == "same_district"]
    options = [(mid, district, bits[mid]) for mid, district in _options()]
    for selected in combinations(options, data["decisions_required"]):
        mask = 0
        for _, _, bit in selected:
            mask |= bit
        cost = allowed.get(mask)
        if cost is None:
            continue
        locations = {mid: district for mid, district, _ in selected}
        if any(a in locations and b in locations and locations[a] == locations[b] for a, b in local):
            continue
        yield tuple(sorted(locations.items())), cost


def _record(keys, score, cost, city=None):
    result = {"plan": _plan(keys), "score": round(score, 2), "cost": cost}
    if city is not None:
        result["approval"] = round(city, 2)
    return result


def generate_cache() -> dict:
    """Build cache in memory; all comparisons use full-precision scores."""
    histogram = Counter()
    top = []
    by_cost = {}
    balanced = None
    baseline_ds = _state(())[2]
    total = 0
    for keys, cost in _valid_options():
        # The same precomputed-vector kernel as quick_score, reused for approval.
        state = _state(keys)
        score = state[0]
        city = _approval_values(keys, state, baseline_ds)[0]
        total += 1
        histogram[round(score, 2)] += 1
        entry = (score, -total, keys, cost, city)
        if len(top) < 200:
            heapq.heappush(top, entry)
        elif entry[:2] > top[0][:2]:
            heapq.heapreplace(top, entry)
        if cost not in by_cost or score > by_cost[cost][0]:
            by_cost[cost] = (score, keys)
        if city >= CONSTANTS["THRESHOLD"] and (balanced is None or score > balanced[0]):
            balanced = (score, keys, cost, city)
    best = None
    pareto, budget_best = [], {}
    for budget in range(min(by_cost), load_dataset()["budget"] + 1):
        candidate = by_cost.get(budget)
        if candidate is not None and (best is None or candidate[0] > best[0]):
            best = (candidate[0], candidate[1], budget)
            pareto.append(_record(best[1], best[0], best[2]))
        budget_best[str(budget)] = _record(best[1], best[0], best[2])
    return {"total_valid": total,
            "top": [_record(keys, score, cost, city) for score, _, keys, cost, city in sorted(top, reverse=True)],
            "pareto": pareto, "best_by_max_cost": budget_best,
            "score_hist": [[score, histogram[score]] for score in sorted(histogram, reverse=True)],
            "balanced": _record(balanced[1], balanced[0], balanced[2], balanced[3]) if balanced else None,
            "min_cost": min(by_cost), "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset_version": load_dataset()["version"]}


@lru_cache(maxsize=1)
def _load_cache():
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    if cache["dataset_version"] != load_dataset()["version"]:
        raise ValueError("Кэш устарел: выполните python -m engine.optimize")
    return cache


def optimize_info(plan) -> dict:
    ok, reason = validate(plan)
    if not ok:
        raise ValueError(reason)
    cache = _load_cache()
    score = round(quick_score(plan), 2)
    higher = sum(count for value, count in cache["score_hist"] if value > score)
    lower = sum(count for value, count in cache["score_hist"] if value < score)
    return {"best": cache["top"][0], "rank": higher + 1, "total_valid": cache["total_valid"],
            "percentile": round(lower / cache["total_valid"] * 100, 2), "pareto": cache["pareto"],
            "best_at_same_cost": cache["best_by_max_cost"][str(plan_cost(plan))],
            "balanced": cache["balanced"], "gap_to_best": diff2(cache["top"][0]["score"], score)}


def _replace(plan, swap):
    decisions = normalize_plan(plan)
    if not isinstance(swap, dict) or "out" not in swap or not isinstance(swap.get("in"), dict):
        raise ValueError("Замена должна содержать out и in")
    indices = [i for i, d in enumerate(decisions) if d["measure"] == swap["out"]]
    if len(indices) != 1:
        raise ValueError(f"Заменяемая мера отсутствует или повторяется: {swap['out']}")
    replacement = normalize_plan([swap["in"]])[0]
    if replacement == decisions[indices[0]]:
        raise ValueError("Нужно изменить ровно одно решение")
    decisions[indices[0]] = replacement
    return {"decisions": decisions}


def what_if(plan, swap) -> dict:
    ok, reason = validate(plan)
    if not ok:
        raise ValueError(reason)
    try:
        new_plan = _replace(plan, swap)
    except ValueError as exc:
        return {"valid": False, "reason": str(exc), "plan": {"decisions": normalize_plan(plan)},
                "score": None, "delta_vs_current": None}
    ok, reason = validate(new_plan)
    score = quick_score(new_plan) if ok else None
    return {"valid": ok, "reason": reason, "plan": new_plan,
            "score": round(score, 2) if ok else None,
            "delta_vs_current": diff2(score, quick_score(plan)) if ok else None}


def best_single_swaps(plan, base_values=None, k=3) -> list[dict]:
    ok, reason = validate(plan)
    if not ok:
        raise ValueError(reason)
    if k <= 0:
        return []
    decisions = normalize_plan(plan)
    current = _state(_keys(decisions), base_values)[0]
    candidates = []
    for i, decision in enumerate(decisions):
        for mid, district in _options():
            replacement = {"measure": mid, "district": district}
            if replacement == decision:
                continue
            new = decisions[:i] + [replacement] + decisions[i + 1:]
            if validate(new)[0]:
                score = _state(_keys(new), base_values)[0]
                candidates.append((score, {"out": decision["measure"], "in": replacement,
                                            "score": round(score, 2), "gain": diff2(score, current)}))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [result for _, result in candidates[:k]]


def main():
    start = time.perf_counter()
    cache = generate_cache()
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    _load_cache.cache_clear()
    print(f"Elapsed: {time.perf_counter() - start:.2f}s; total_valid: {cache['total_valid']}; "
          f"top score: {cache['top'][0]['score']}; cache bytes: {CACHE_PATH.stat().st_size}")
    print("Balanced: " + json.dumps(cache["balanced"], ensure_ascii=False))


if __name__ == "__main__":
    main()
