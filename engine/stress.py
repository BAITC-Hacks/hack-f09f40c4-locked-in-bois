"""Crisis stress tests and exhaustive maximin search: python -m engine.stress.

Selection uses full precision, with normal score breaking maximin ties. Rank
and percentile use displayed scores, just like engine.optimize: ties share a
rank, and percentile counts strictly lower scores. The combined scenario has
district=None and effects grouped by district. Insurance is an actual swap;
its recovered value may be negative when every legal swap makes a plan worse.
"""

import heapq
import json
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache

from .approval import approval
from .model import REPO_ROOT, load_dataset, normalize_plan, plan_cost
from .optimize import _plan, _valid_options, best_single_swaps
from .score import _keys, _rounded, _state, diff2
from .shock import load_events, shocked_base
from .validate import validate

CACHE_PATH = REPO_ROOT / "data/stress_cache.json"
ALL_TITLE = "Чёрная зима: все три кризиса сразу"


def _merged_base(bases):
    """Add each shocked base's shift, including overlapping affected cells."""
    original = {d["name"]: d["values"] for d in load_dataset()["districts"]}
    merged = deepcopy(original)
    for base in bases:
        for district, values in base.items():
            for code, value in values.items():
                merged[district][code] += value - original[district][code]
    return merged


@lru_cache(maxsize=1)
def _scenario_bases():
    singles = tuple((event, shocked_base(event)) for event in load_events())
    combined = _merged_base(base for _, base in singles)
    effects = {}
    for event, _ in singles:
        target = effects.setdefault(event["district"], {})
        for code, shift in event["effects"].items():
            target[code] = target.get(code, 0) + shift
    event = {"id": "all", "title": ALL_TITLE, "district": None, "effects": effects}
    return singles + ((event, combined),)


def _record(keys):
    plan = _plan(keys)
    normal = _state(keys)[0]
    scores = {event["id"]: _state(keys, base)[0] for event, base in _scenario_bases()}
    worst_event = min(load_events(), key=lambda event: scores[event["id"]])["id"]
    worst = scores[worst_event]
    worst_all = min(scores.values())
    return _rounded({
        "plan": plan, "score": normal, "cost": plan_cost(plan),
        "approval": approval(plan)["city"], "worst_event": worst_event,
        "worst_score": worst, "max_loss": diff2(normal, worst),
        "worst_score_all": worst_all, "max_loss_all": diff2(normal, worst_all),
        "all_score": scores["all"], "event_scores": scores,
    })


def generate_cache() -> dict:
    """Enumerate every legal plan once; never invoked by a request handler."""
    start = time.perf_counter()
    bases = tuple(base for _, base in _scenario_bases())
    histogram = Counter()
    top = []
    extreme = optimum = None
    total = 0
    for keys, _ in _valid_options():
        total += 1
        normal = _state(keys)[0]
        scores = tuple(_state(keys, base)[0] for base in bases)
        worst = min(scores[:-1])
        histogram[round(worst, 2)] += 1
        entry = (worst, normal, -total, keys)
        if len(top) < 10:
            heapq.heappush(top, entry)
        elif entry[:3] > top[0][:3]:
            heapq.heapreplace(top, entry)
        all_entry = (scores[-1], normal, -total, keys)
        if extreme is None or all_entry[:3] > extreme[:3]:
            extreme = all_entry
        if optimum is None or normal > optimum[0]:
            optimum = (normal, keys)
    ranked = [_record(entry[-1]) for entry in sorted(top, reverse=True)]
    result = {
        "total_valid": total, "crisis_proof": ranked[0],
        "crisis_proof_all": _record(extreme[-1]), "top_robust": ranked,
        "optimum_under_events": _record(optimum[1]),
        "worst_hist": [[score, histogram[score]] for score in sorted(histogram, reverse=True)],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_version": load_dataset()["version"], "events": deepcopy(load_events()),
    }
    result["generation_seconds"] = round(time.perf_counter() - start, 2)
    return result


@lru_cache(maxsize=1)
def _load_cache():
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("Нет кэша стресс-теста: выполните python -m engine.stress") from exc
    if (cache["dataset_version"] != load_dataset()["version"]
            or cache["events"] != load_events()):
        raise ValueError("Кэш стресс-теста устарел: выполните python -m engine.stress")
    return cache


def robust_info() -> dict:
    """Return the precomputed summary without exposing the shared cache."""
    return deepcopy(_load_cache())


def stress_test(plan) -> dict:
    """Score all crises and their best single swaps, then look up robust rank."""
    ok, reason = validate(plan)
    if not ok:
        raise ValueError(reason)
    keys = _keys(normalize_plan(plan))
    normal, before, _, _, _ = _state(keys)
    data = load_dataset()
    codes = [indicator["code"] for indicator in data["indicators"]]
    width = len(codes)
    scenarios = []
    raw_scores = []
    for event, base in _scenario_bases():
        score, values, _, _, _ = _state(keys, base)
        raw_scores.append(score)
        new_critical = [
            {"district": district["name"], "indicator": code, "value": values[cell]}
            for di, district in enumerate(data["districts"])
            for ki, code in enumerate(codes)
            for cell in (di * width + ki,)
            if values[cell] < data["crit_threshold"] <= before[cell]
        ]
        swaps = best_single_swaps(plan, base_values=base, k=1)
        best = swaps[0] if swaps else None
        insurance = ({"out": best["out"], "in": best["in"], "score": best["score"],
                      "recovered": diff2(best["score"], score)} if best else None)
        scenarios.append({
            "event_id": event["id"], "title": event["title"], "district": event["district"],
            "effects": deepcopy(event["effects"]), "score": score,
            "loss": diff2(normal, score), "new_crit_cells": new_critical, "insurance": insurance,
        })
    worst_index = min(range(len(raw_scores) - 1), key=raw_scores.__getitem__)
    worst = scenarios[worst_index]
    worst_score = round(worst["score"], 2)
    worst_all = min(raw_scores)
    cache = _load_cache()
    higher = sum(count for value, count in cache["worst_hist"] if value > worst_score)
    lower = sum(count for value, count in cache["worst_hist"] if value < worst_score)
    return _rounded({
        "score": normal, "scenarios": scenarios, "worst_event": worst["event_id"],
        "worst_score": worst_score, "max_loss": diff2(normal, worst_score),
        "worst_score_all": worst_all, "max_loss_all": diff2(normal, worst_all),
        "robust_rank": higher + 1, "robust_total": cache["total_valid"],
        "robust_percentile": round(lower / cache["total_valid"] * 100, 2),
        "crisis_proof_plan": deepcopy(cache["crisis_proof"]),
        "verdict": (f"Худший отдельный кризис — «{worst['title']}»: "
                    f"оценка {worst_score:.2f}, снижение на {worst['loss']:.2f}; "
                    f"«чёрная зима» — {raw_scores[-1]:.2f}."),
    })


def main():
    start = time.perf_counter()
    cache = generate_cache()
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    _load_cache.cache_clear()
    print(f"Время: {time.perf_counter() - start:.2f} с; планов: {cache['total_valid']}; "
          f"худшая оценка лучшего плана: {cache['crisis_proof']['worst_score']:.2f}; "
          f"размер кэша: {CACHE_PATH.stat().st_size} байт")


if __name__ == "__main__":
    main()
