"""Official formula, with a shared full-precision kernel and rounded API output."""

from functools import lru_cache

from .model import load_dataset, measures_by_id, normalize_plan, plan_cost
from .validate import validate


@lru_cache(maxsize=1)
def _prepared():
    data = load_dataset()
    codes = [i["code"] for i in data["indicators"]]
    names = [d["name"] for d in data["districts"]]
    width = len(codes)
    base = tuple(d["values"][k] for d in data["districts"] for k in codes)
    weights = tuple(i["weight"] for i in data["indicators"])
    pops = tuple(d["pop"] for d in data["districts"])
    vectors = {}
    for m in data["measures"]:
        targets = names if m["type"] == "district" else [None]
        for target in targets:
            locations = [names.index(target)] if target is not None else range(len(names))
            vectors[m["id"], target] = tuple(
                (di * width + codes.index(k), value * (data["horizon_quarters"] - m["lag"]) / data["horizon_quarters"])
                for di in locations for k, value in m["effects"].items()
            )
    return data, codes, names, width, base, weights, pops, vectors


def _keys(plan):
    decisions = plan["decisions"] if isinstance(plan, dict) else plan
    return tuple(sorted(((d["measure"], d.get("district")) for d in decisions), key=lambda x: x[0]))


def _state(keys, base_values=None, q=None):
    """Accumulate all effects, then clip once; no public result dictionaries."""
    data, codes, names, width, base, weights, pops, vectors = _prepared()
    values = list(base) if base_values is None else [
        base_values.get(name, {}).get(k, base[di * width + ki])
        for di, name in enumerate(names) for ki, k in enumerate(codes)
    ]
    horizon = data["horizon_quarters"]
    measures = measures_by_id()
    for mid, target in keys:
        if q is None or q == horizon:
            for cell, effect in vectors[mid, target]:
                values[cell] += effect
        else:
            m = measures[mid]
            fraction = max(0, q - m["lag"]) / horizon
            locations = [names.index(target)] if m["type"] == "district" else range(len(names))
            for di in locations:
                for k, value in m["effects"].items():
                    values[di * width + codes.index(k)] += value * fraction
    for synergy in data["synergies"]:
        a, b = synergy["pair"]
        if any(mid == a for mid, _ in keys) and any(mid == b for mid, _ in keys):
            if q is not None and q <= max(measures[a]["lag"], measures[b]["lag"]):
                continue
            target = next(target for mid, target in keys if mid == synergy["district_of"])
            offset = names.index(target) * width
            for k, bonus in synergy["bonus"].items():
                values[offset + codes.index(k)] += bonus
    ds, crits = [], []
    for di in range(len(names)):
        total, critical = 0.0, 0
        for ki, weight in enumerate(weights):
            cell = di * width + ki
            value = values[cell]
            value = 0.0 if value < 0 else 100.0 if value > 100 else value
            values[cell] = value
            total += weight * value
            critical += value < data["crit_threshold"]
        ds.append(total)
        crits.append(critical)
    avg = sum(p * d for p, d in zip(pops, ds))
    sw = data["score_weights"]
    score = sw["avg"] * avg + sw["min"] * min(ds) - sw["crit_penalty"] * sum(crits)
    return score, values, ds, crits, avg


def quick_score(decisions) -> float:
    """Full precision, no validation or result-dict construction."""
    return _state(_keys(decisions))[0]


def diff2(a, b) -> float:
    """Difference of the displayed (2dp) values, so UI numbers always add up."""
    return round(round(a, 2) - round(b, 2), 2)


def _rounded(value):
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_rounded(v) for v in value]
    return value


def _compute(decisions, base_values=None):
    decisions = normalize_plan(decisions)
    keys = _keys(decisions)
    data, codes, names, width, _, _, pops, _ = _prepared()
    score, after, ds, crits, avg = _state(keys, base_values)
    baseline, before, before_ds, before_crits, _ = _state((), base_values)
    locations = dict(keys)
    triggered = [
        {"pair": list(s["pair"]), "district": locations[s["district_of"]], "bonus": dict(s["bonus"])}
        for s in data["synergies"] if all(mid in locations for mid in s["pair"])
    ]
    critical, resolved, districts = [], [], {}
    for di, name in enumerate(names):
        start = di * width
        districts[name] = {"before": before[start:start + width], "after": after[start:start + width],
                           "D_before": before_ds[di], "D_after": ds[di], "pop": pops[di]}
        for ki, code in enumerate(codes):
            old, new = before[start + ki], after[start + ki]
            if new < data["crit_threshold"]:
                critical.append({"district": name, "indicator": code, "value": new})
            elif old < data["crit_threshold"]:
                resolved.append({"district": name, "indicator": code, "before": old, "after": new})
    cost = plan_cost(decisions)
    return {"score": score, "baseline": baseline, "delta": diff2(score, baseline),
            "cost": cost, "remaining": data["budget"] - cost, "d_avg": avg,
            "d_min": min(ds), "d_min_district": names[ds.index(min(ds))],
            "districts": districts, "n_crit": sum(crits), "n_crit_before": sum(before_crits),
            "crit_cells": critical, "resolved_crit_cells": resolved, "synergies_triggered": triggered}


def compute(decisions, base_values=None) -> dict:
    """Unvalidated core summary, including empty and remove-one plans."""
    return _rounded(_compute(decisions, base_values))


def evaluate(plan, base_values=None) -> dict:
    ok, reason = validate(plan)
    if not ok:
        raise ValueError(reason)
    decisions = normalize_plan(plan)
    result = _compute(decisions, base_values)
    contributions = []
    for index, decision in enumerate(decisions):
        without = _state(_keys(decisions[:index] + decisions[index + 1:]), base_values)[0]
        cost = measures_by_id()[decision["measure"]]["cost"]
        marginal = diff2(result["score"], without)
        contributions.append({**decision, "cost": cost, "score_without": without,
                              "marginal": marginal, "marginal_per_cost": marginal / cost})
    horizon = load_dataset()["horizon_quarters"]
    result["contributions"] = contributions
    result["timeline"] = [{"q": q, "score": _state(_keys(decisions), base_values, q)[0]}
                          for q in range(horizon + 1)]
    result = _rounded(result)
    # Ratios retain their exact precision, as required by the public contract.
    result["realized_share"] = {d["measure"]: (horizon - measures_by_id()[d["measure"]]["lag"]) / horizon
                                for d in decisions}
    return result
