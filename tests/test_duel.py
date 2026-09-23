"""Official-score attribution, decimal display identities and cached duels."""

from copy import deepcopy
from decimal import Decimal
import json
import random
from time import perf_counter

import pytest

from engine.duel import duel, duel_vs_best
from engine.model import district_names, load_dataset, measures_by_id
from engine.optimize import _load_cache, _options, optimize_info
from engine.score import _keys, _state, diff2, evaluate
from engine.validate import validate


@pytest.fixture
def doc():
    return {"decisions": deepcopy(load_dataset()["reference"]["doc_example"]["decisions"])}


@pytest.fixture
def optimum():
    return deepcopy(_load_cache()["top"][0]["plan"])


def _plan(*ids, district="Нура"):
    return {"decisions": [
        {"measure": mid, "district": district if measures_by_id()[mid]["type"] == "district" else None}
        for mid in ids
    ]}


def _assert_display_identity(result):
    """Use exact decimal arithmetic, not a tolerance hiding a missing cent."""
    terms = result["terms"]
    assert set(terms) == {"avg", "min", "crit"}
    assert sum(Decimal(str(t["delta"])) for t in terms.values()) == Decimal(str(result["score_delta"]))
    for side in ("a", "b"):
        assert sum(Decimal(str(t[side])) for t in terms.values()) == Decimal(str(result[side]["score"]))
    for term in terms.values():
        assert term["delta"] == diff2(term["a"], term["b"])
    assert result["score_delta"] == diff2(result["a"]["score"], result["b"]["score"])
    for key in ("cost", "d_avg", "d_min", "n_crit"):
        assert result[key + "_delta"] == diff2(result["a"][key], result["b"][key])
    for district in result["districts"].values():
        assert district["delta_D"] == diff2(district["D_a"], district["D_b"])
        assert district["delta_n_crit"] == diff2(district["n_crit_a"], district["n_crit_b"])


def test_doc_against_optimum_golden_decomposition(doc, optimum):
    result = duel(doc, optimum)
    assert (result["a"]["score"], result["a"]["cost"]) == (56.54, 95)
    assert (result["b"]["score"], result["b"]["cost"]) == (57.24, 98)
    assert result["score_delta"] == -0.70
    assert [t["delta"] for t in result["terms"].values()] == [-0.36, -0.34, 0]
    assert result["districts"]["Нура"]["delta_D"] == -1.13
    assert result["districts"]["Сарыарка"]["delta_D"] == 0.53
    assert result["winner"] == "B"
    assert "План Б лучше плана А на 0.70 балла" in result["verdict"]
    assert list(result["districts"]) == district_names()
    for side, plan in (("a", doc), ("b", optimum)):
        evaluation = evaluate(plan)
        for key in ("score", "cost", "d_avg", "d_min", "n_crit"):
            assert result[side][key] == evaluation[key]
        for name in district_names():
            assert result["districts"][name]["D_" + side] == evaluation["districts"][name]["D_after"]
    _assert_display_identity(result)


def test_cached_comparisons_and_rounding_reconciliation(doc, optimum):
    result = duel_vs_best(doc)
    info = optimize_info(doc)
    assert result["best"] == duel(doc, info["best"]["plan"])
    assert result["best_at_same_cost"] == duel(doc, info["best_at_same_cost"]["plan"])
    same_cost = result["best_at_same_cost"]
    assert result["cost_limit"] == 95
    assert "не выше" in result["same_cost_label"]
    assert (same_cost["b"]["score"], same_cost["b"]["cost"]) == (57.23, 91)
    assert same_cost["score_delta"] == -0.69
    assert [t["delta"] for t in same_cost["terms"].values()] == [-0.14, -0.55, 0]
    assert same_cost["terms"]["min"]["rounding_adjustment_b"] == 0.01
    assert "поправка округления" in same_cost["rounding_note"]
    _assert_display_identity(same_cost)
    for comparison in ("best", "best_at_same_cost"):
        at_optimum = duel_vs_best(optimum)[comparison]
        assert at_optimum["winner"] == "tie"
        assert at_optimum["score_delta"] == 0
        _assert_display_identity(at_optimum)


def test_identical_and_reordered_plans_are_ties(doc):
    result = duel(doc, list(reversed(doc["decisions"])))
    assert result["winner"] == "tie"
    assert result["display_tie"] is True
    assert result["score_delta"] == 0
    assert all(t["delta"] == 0 for t in result["terms"].values())
    assert all(d["delta_D"] == 0 for d in result["districts"].values())
    assert len(result["decisions"]["same"]) == load_dataset()["decisions_required"]
    assert not any(result["decisions"][key] for key in ("only_a", "only_b", "same_measure_different_district"))
    assert "Планы А и Б равны" in result["verdict"]
    _assert_display_identity(result)


def test_decision_categories_separate_relocations_and_new_measures(doc, optimum):
    moved = deepcopy(doc)
    moved["decisions"][0]["district"] = "Есиль"
    result = duel(doc, moved)
    changes = result["decisions"]
    assert changes["only_a"] == changes["only_b"] == []
    assert len(changes["same"]) == 4
    relocation, = changes["same_measure_different_district"]
    assert (relocation["measure"], relocation["district_a"], relocation["district_b"]) == ("M7", "Нура", "Есиль")
    assert "в Нуре" in relocation["description"]
    assert "в Есиле" in relocation["description"]
    changes = duel(doc, optimum)["decisions"]
    assert {d["measure"] for d in changes["only_a"]} == {"M5", "M7", "M10", "M12"}
    assert {d["measure"] for d in changes["only_b"]} == {"M2", "M3", "M9", "M14"}
    assert changes["same"] == [{"measure": "M8", "district": "Нура"}]


def test_critical_penalty_from_almaty_trap_and_cheapest_golden(optimum):
    cheapest = _plan("M9", "M11", "M10", "M4", "M12")
    trap = deepcopy(cheapest)
    trap["decisions"][1]["district"] = "Алматы"
    result = duel(cheapest, trap)
    assert (result["a"]["score"], result["a"]["cost"]) == (55.67, 61)
    assert (result["a"]["n_crit"], result["b"]["n_crit"]) == (1, 2)
    assert result["n_crit_delta"] == -1
    assert result["terms"]["crit"]["delta"] == 1
    assert result["districts"]["Алматы"]["delta_n_crit"] == -1
    assert duel(optimum, cheapest)["terms"]["crit"]["delta"] == 1
    _assert_display_identity(result)


def test_reversing_plans_negates_every_difference(doc, optimum):
    forward, reverse = duel(doc, optimum), duel(optimum, doc)
    assert reverse["winner"] == "A"
    for key in ("score_delta", "cost_delta", "d_avg_delta", "d_min_delta", "n_crit_delta"):
        assert forward[key] == -reverse[key]
    for key in forward["terms"]:
        assert forward["terms"][key]["delta"] == -reverse["terms"][key]["delta"]
    for name in district_names():
        assert forward["districts"][name]["delta_D"] == -reverse["districts"][name]["delta_D"]
    assert forward["decisions"]["only_a"] == reverse["decisions"]["only_b"]
    _assert_display_identity(reverse)


def test_formulas_and_exact_cent_sums_over_varied_valid_plans(doc):
    rng = random.Random(871)
    plans = [entry["plan"] for entry in _load_cache()["best_by_max_cost"].values()]
    while len(plans) < 180:
        candidate = [{"measure": mid, "district": district} for mid, district in rng.sample(_options(), 5)]
        if validate(candidate)[0]:
            plans.append(candidate)
    weights = load_dataset()["score_weights"]
    for plan in plans:
        result = duel(plan, doc)
        _assert_display_identity(result)
        _assert_display_identity(duel(doc, plan))
        for side, item in (("a", plan), ("b", doc)):
            _, _, ds, crits, avg = _state(_keys(item))
            assert result["terms"]["avg"][side] == round(weights["avg"] * avg, 2)
            assert result["terms"]["crit"][side] == round(-weights["crit_penalty"] * sum(crits), 2)
            adjustment = result["terms"]["min"]["rounding_adjustment_" + side]
            assert abs(adjustment) <= 0.01
            assert adjustment == diff2(result["terms"]["min"][side], weights["min"] * min(ds))


def test_equal_display_scores_keep_full_precision_winner():
    # The cache contains genuinely distinct exact scores in the same 2dp bin.
    groups = {}
    pair = None
    for entry in _load_cache()["top"]:
        score = entry["score"]
        previous = groups.get(score)
        if previous is not None and abs(_state(_keys(previous))[0] - _state(_keys(entry["plan"]))[0]) > 1e-10:
            pair = previous, entry["plan"]
            break
        groups[score] = entry["plan"]
    assert pair is not None
    result = duel(*pair)
    assert result["display_tie"] is True
    assert result["winner"] == "A"
    assert "по точному расчёту" in result["verdict"]
    assert "после округления" in result["verdict"]
    _assert_display_identity(result)


@pytest.mark.parametrize("invalid", [None, {}, [], {"decisions": [None] * 5}, _plan("M9", "M9", "M10", "M12", "M14")])
def test_invalid_plans_use_validator_reason(invalid, doc):
    reason = validate(invalid)[1]
    for invoke in (lambda: duel(invalid, doc), lambda: duel(doc, invalid), lambda: duel_vs_best(invalid)):
        with pytest.raises(ValueError) as error:
            invoke()
        assert str(error.value) == reason


def test_inputs_and_cached_records_are_not_mutated_or_exposed(doc, optimum):
    original, cache = deepcopy(doc), deepcopy(_load_cache())
    assert duel(doc, optimum) == duel(doc["decisions"], optimum["decisions"])
    assert duel_vs_best(doc) == duel_vs_best(doc["decisions"])
    result = duel_vs_best(doc)
    result["best"]["a"]["plan"]["decisions"][0]["measure"] = "changed"
    result["best"]["b"]["plan"]["decisions"][0]["measure"] = "changed"
    assert doc == original
    assert _load_cache() == cache


def test_city_district_can_be_omitted(doc, optimum):
    del doc["decisions"][3]["district"]
    result = duel(doc, optimum)
    assert result["a"]["plan"]["decisions"][3]["district"] is None
    assert "district" not in doc["decisions"][3]


def test_json_output_has_only_two_decimal_numbers(doc):
    result = json.loads(json.dumps(duel_vs_best(doc), ensure_ascii=False, allow_nan=False))

    def check(value):
        if isinstance(value, float):
            assert Decimal(str(value)) == Decimal(str(value)).quantize(Decimal("0.01"))
        elif isinstance(value, dict):
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    check(result)


def test_warm_public_calls_fit_response_budget(doc, optimum, monkeypatch):
    duel_vs_best(doc)

    def no_enumeration(*args, **kwargs):
        pytest.fail("Public duels must use the existing cache, never enumerate plans")

    monkeypatch.setattr("engine.optimize._valid_options", no_enumeration)
    monkeypatch.setattr("engine.optimize.generate_cache", no_enumeration)
    for invoke in (lambda: duel(doc, optimum), lambda: duel_vs_best(doc)):
        start = perf_counter()
        invoke()
        assert perf_counter() - start < 1.5
