"""Seeded crisis events and a mandatory single-decision recovery."""

from .model import load_dataset, load_events
from .optimize import _replace, best_single_swaps
from .score import _keys, _state, diff2, evaluate
from .validate import validate


def pick_event(seed: int) -> dict:
    events = load_events()
    return events[seed % len(events)]


def shocked_base(event) -> dict:
    base = {d["name"]: dict(d["values"]) for d in load_dataset()["districts"]}
    for indicator, effect in event["effects"].items():
        base[event["district"]][indicator] += effect
    return base


def shock(plan, seed) -> dict:
    ok, reason = validate(plan)
    if not ok:
        raise ValueError(reason)
    event = pick_event(seed)
    base = shocked_base(event)
    return {"event": event, "score_before": round(_state(_keys(plan))[0], 2),
            "new_baseline_score": round(_state(_keys(plan), base)[0], 2), "must": "swap_one",
            "best_swaps": best_single_swaps(plan, base, 3)}


def resolve(plan, event_id, swap) -> dict:
    ok, reason = validate(plan)
    if not ok:
        raise ValueError(reason)
    event = next((event for event in load_events() if event["id"] == event_id), None)
    if event is None:
        raise ValueError(f"Неизвестное событие: {event_id}")
    new_plan = _replace(plan, swap)
    base = shocked_base(event)
    result = evaluate(new_plan, base)
    s0 = _state(_keys(plan))[0]
    s1 = _state(_keys(plan), base)[0]
    s2 = _state(_keys(new_plan), base)[0]
    best = best_single_swaps(plan, base, 1)[0]
    best_score = _state(_keys(_replace(plan, best)), base)[0]
    result.update({"crisis_cost": diff2(s0, s1), "recovered": diff2(s2, s1),
                   "score_before_crisis": round(s0, 2), "score_after_crisis_no_swap": round(s1, 2),
                   "best_possible_swap": best, "swap_was_optimal": abs(s2 - best_score) < 1e-10,
                   "new_plan": new_plan})
    return result
