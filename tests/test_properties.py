"""Deterministic properties over 200 plans; no exhaustive search or Hypothesis.

The oracle reads dataset.json directly and does not use engine score helpers.
Only evaluate's final presentation rounding is bypassed for precision checks.
"""

import json
import random
from copy import deepcopy
from itertools import permutations
from pathlib import Path
from unittest.mock import patch

import pytest

from engine import score as scoring
from engine.approval import CONSTANTS, approval
from engine.validate import validate


ROOT = Path(__file__).resolve().parents[1]
EPSILON = 1e-9


@pytest.fixture(scope="module")
def dataset():
    return json.loads((ROOT / "data/dataset.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def optimizer_cache():
    return json.loads((ROOT / "data/optimizer_cache.json").read_text(encoding="utf-8"))


def _signature(plan):
    return tuple(sorted((d["measure"], d["district"]) for d in plan["decisions"]))


@pytest.fixture(scope="module")
def plans(dataset, optimizer_cache):
    top = optimizer_cache["top"]
    assert len(top) >= 100
    selected = [deepcopy(top[i * (len(top) - 1) // 99]["plan"]) for i in range(100)]
    selected.append({"decisions": deepcopy(dataset["reference"]["doc_example"]["decisions"])})
    seen = {_signature(plan) for plan in selected}
    rng = random.Random(20260923)
    names = [d["name"] for d in dataset["districts"]]
    for _ in range(20_000):
        chosen = rng.sample(dataset["measures"], dataset["decisions_required"])
        plan = {"decisions": [
            {"measure": m["id"], "district": rng.choice(names) if m["type"] == "district" else None}
            for m in chosen
        ]}
        signature = _signature(plan)
        if signature not in seen and validate(plan)[0]:
            selected.append(plan)
            seen.add(signature)
        if len(selected) == 200:
            break
    assert len(selected) == len(seen) == 200
    assert all(validate(plan) == (True, None) for plan in selected)
    # Keep the generated half diverse enough to exercise every measure/target
    # and each synergy, rather than silently testing only optimizer favourites.
    generated = selected[101:]
    assert {d["measure"] for p in generated for d in p["decisions"]} == {
        m["id"] for m in dataset["measures"]
    }
    assert {d["district"] for p in generated for d in p["decisions"]} == set(names) | {None}
    for synergy in dataset["synergies"]:
        assert any(set(synergy["pair"]) <= {d["measure"] for d in p["decisions"]} for p in generated)
    return selected


def _formula(dataset, decisions, base_values=None, q=None):
    """Independent per-cell formula, including lag, synergy and one final clip."""
    horizon = dataset["horizon_quarters"]
    quarter = horizon if q is None else q
    measures = {m["id"]: m for m in dataset["measures"]}
    locations = {d["measure"]: d["district"] for d in decisions}
    raw, values, district_scores = {}, {}, {}
    n_crit = 0
    for district in dataset["districts"]:
        name = district["name"]
        raw[name], values[name] = {}, {}
        for indicator in dataset["indicators"]:
            code = indicator["code"]
            initial = (base_values or {}).get(name, {}).get(code, district["values"][code])
            effects = sum(
                measures[d["measure"]]["effects"].get(code, 0)
                * max(0, quarter - measures[d["measure"]]["lag"]) / horizon
                for d in decisions
                if measures[d["measure"]]["type"] == "city" or d["district"] == name
            )
            bonuses = sum(
                synergy["bonus"].get(code, 0)
                for synergy in dataset["synergies"]
                if set(synergy["pair"]) <= locations.keys()
                and locations[synergy["district_of"]] == name
                and all(quarter > measures[mid]["lag"] for mid in synergy["pair"])
            )
            raw[name][code] = initial + effects + bonuses
            values[name][code] = min(100, max(0, raw[name][code]))
            n_crit += values[name][code] < dataset["crit_threshold"]
        district_scores[name] = sum(
            i["weight"] * values[name][i["code"]] for i in dataset["indicators"]
        )
    average = sum(d["pop"] * district_scores[d["name"]] for d in dataset["districts"])
    minimum = min(district_scores.values())
    weights = dataset["score_weights"]
    return {
        "score": weights["avg"] * average + weights["min"] * minimum - weights["crit_penalty"] * n_crit,
        "d_avg": average, "d_min": minimum, "n_crit": n_crit,
        "ds": district_scores, "values": values, "raw": raw,
    }


def _assert_formula(actual, expected):
    for field in ("score", "d_avg", "d_min"):
        assert abs(actual[field] - expected[field]) <= EPSILON, field
    assert actual["n_crit"] == expected["n_crit"]
    for name, district in actual["districts"].items():
        assert abs(district["D_after"] - expected["ds"][name]) <= EPSILON
        assert district["after"] == pytest.approx(list(expected["values"][name].values()), rel=0, abs=EPSILON)


def test_score_matches_independent_formula_before_rounding(dataset, plans):
    for plan in plans:
        expected = _formula(dataset, plan["decisions"])
        # evaluate normally rounds to 2dp. This exposes its actual numeric path
        # without replacing any computation, validation, timeline or marginal.
        with patch.object(scoring, "_rounded", lambda value: value):
            _assert_formula(scoring.evaluate(plan), expected)
        assert abs(scoring.quick_score(plan) - expected["score"]) <= EPSILON
        assert scoring.evaluate(plan)["score"] == round(expected["score"], 2)


def test_decision_order_never_changes_score(plans):
    for plan in plans:
        expected = scoring.quick_score(plan)
        for order in permutations(plan["decisions"]):
            assert scoring.quick_score(list(order)) == expected, order
        reversed_plan = {"decisions": list(reversed(plan["decisions"]))}
        assert scoring.evaluate(reversed_plan)["score"] == scoring.evaluate(plan)["score"]


def test_indicator_clipping_bounds_and_formula(dataset, plans):
    # Ordinary plans seldom reach the bounds. Synthetic bases force both clips
    # and critical-threshold boundaries without changing the legal decisions.
    levels = (-20, 0, dataset["crit_threshold"] - 1e-7, dataset["crit_threshold"], 99, 120)
    clipped_low = clipped_high = False
    for index, plan in enumerate(plans):
        base = {
            d["name"]: {i["code"]: levels[(index + di + ki) % len(levels)]
                        for ki, i in enumerate(dataset["indicators"])}
            for di, d in enumerate(dataset["districts"])
        }
        for scenario in (None, base):
            expected = _formula(dataset, plan["decisions"], scenario)
            with patch.object(scoring, "_rounded", lambda value: value):
                result = scoring.evaluate(plan, scenario)
            _assert_formula(result, expected)
            for district in result["districts"].values():
                assert all(0 <= value <= 100 for value in district["before"] + district["after"])
            clipped_low |= any(v < 0 for row in expected["raw"].values() for v in row.values())
            clipped_high |= any(v > 100 for row in expected["raw"].values() for v in row.values())
    assert clipped_low and clipped_high


def test_city_measures_apply_equal_unclipped_effects_to_every_district(dataset):
    names = [d["name"] for d in dataset["districts"]]
    codes = [i["code"] for i in dataset["indicators"]]
    assert len(names) == 5
    # Isolate each city measure: a synergy with a district measure has its own
    # local bonus, which must not be mistaken for an unequal city effect.
    for measure in dataset["measures"]:
        if measure["type"] != "city":
            continue
        decisions = [{"measure": measure["id"], "district": None}]
        for q in range(dataset["horizon_quarters"] + 1):
            values = scoring._state(scoring._keys(decisions), q=q)[1]
            expected = _formula(dataset, decisions, q=q)
            for ki, code in enumerate(codes):
                effect = measure["effects"].get(code, 0) * max(0, q - measure["lag"]) / dataset["horizon_quarters"]
                for di, district in enumerate(dataset["districts"]):
                    raw = expected["raw"][district["name"]][code]
                    assert 0 <= raw <= 100, "Equal-effect comparison requires no clipping"
                    assert abs(values[di * len(codes) + ki] - district["values"][code] - effect) <= EPSILON


def test_removing_and_readding_each_decision_is_identity(plans):
    for plan in plans:
        original = deepcopy(plan)
        expected = scoring._state(scoring._keys(plan))
        for index in range(len(plan["decisions"])):
            restored = deepcopy(plan["decisions"])
            removed = restored.pop(index)
            scoring.quick_score(restored)  # Exercise the partial-plan path too.
            restored.append(removed)
            assert validate(restored) == (True, None)
            assert scoring._state(scoring._keys(restored)) == expected
            assert scoring.compute(restored)["score"] == round(expected[0], 2)
        assert plan == original


def test_timeline_endpoints_and_independent_quarter_formula(dataset, plans):
    horizon = dataset["horizon_quarters"]
    baseline = _formula(dataset, [])["score"]
    assert horizon == 8
    for plan in plans:
        result = scoring.evaluate(plan)
        assert [row["q"] for row in result["timeline"]] == list(range(horizon + 1))
        assert result["timeline"][0]["score"] == result["baseline"] == round(baseline, 2)
        assert result["timeline"][-1]["score"] == result["score"]
        for row in result["timeline"]:
            expected = _formula(dataset, plan["decisions"], q=row["q"])["score"]
            actual = scoring._state(scoring._keys(plan), q=row["q"])[0]
            assert abs(actual - expected) <= EPSILON
            assert row["score"] == round(expected, 2)


def test_remove_one_marginals_add_to_displayed_score(dataset, plans):
    for plan in plans:
        result = scoring.evaluate(plan)
        assert len(result["contributions"]) == len(plan["decisions"])
        for index, contribution in enumerate(result["contributions"]):
            decisions = plan["decisions"]
            without = decisions[:index] + decisions[index + 1:]
            expected = _formula(dataset, without)["score"]
            actual = scoring.quick_score(without)
            assert abs(actual - expected) <= EPSILON
            assert contribution["measure"] == decisions[index]["measure"]
            assert contribution["district"] == decisions[index]["district"]
            # Equivalent summation orders can straddle a half-cent boundary.
            # Check the oracle before rounding, then the engine's display rule.
            assert contribution["score_without"] == round(actual, 2)
            assert contribution["marginal"] == scoring.diff2(result["score"], actual)
            assert round(contribution["score_without"] + contribution["marginal"], 2) == result["score"]


def test_approval_never_changes_official_score(plans):
    for plan in plans:
        original = deepcopy(plan)
        expected = scoring.evaluate(plan)["score"]
        state = scoring._state(scoring._keys(plan))
        approval(plan)
        assert scoring.evaluate(plan)["score"] == expected
        # Force opposite political outcomes and prove the Score is independent
        # of the political constants, not merely unchanged by one function call.
        for political_base in (0, 100):
            with patch.dict(CONSTANTS, {"BASE": political_base, "A": 0, "GOT": 0, "MISS": 0, "CRIT": 0}):
                assert approval(plan)["city"] == political_base
                assert scoring.evaluate(plan)["score"] == expected
                assert scoring._state(scoring._keys(plan)) == state
        assert plan == original


def test_every_cached_top_plan_rescores_to_cached_value(dataset, optimizer_cache):
    assert optimizer_cache["top"]
    for entry in optimizer_cache["top"]:
        plan = entry["plan"]
        assert validate(plan) == (True, None)
        expected = _formula(dataset, plan["decisions"])["score"]
        assert abs(scoring.quick_score(plan) - expected) <= EPSILON
        assert scoring.evaluate(plan)["score"] == round(expected, 2) == entry["score"]
