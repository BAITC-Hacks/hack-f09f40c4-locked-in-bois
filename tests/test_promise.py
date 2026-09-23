"""Exact promise prices, independent enumeration, validation and latency."""

import copy
import json
import math
import random
import time
from itertools import combinations

import pytest

from engine.approval import _approval_values, approval
from engine.model import district_names, load_dataset, plan_cost
from engine.optimize import CACHE_PATH, _options, _plan, _valid_options
from engine.promise import (TABLE_PATH, _best_index, _join_approval, _load_table,
                            _mask, _record, _split_approval, label, price,
                            promise_catalog)
from engine.score import _keys, _state, diff2, evaluate, quick_score
from engine.validate import validate


def test_unconstrained_and_doc_example():
    doc = load_dataset()["reference"]["doc_example"]
    result = price([], doc)
    assert result["feasible"]
    assert result["count_feasible"] == result["total_valid"] == 694395
    assert result["best"]["score"] == result["unconstrained_best"] == 57.24
    assert result["price"] == 0
    assert result["promises"] == []
    assert result["your_plan"] == {"score": 56.54, "keeps_promises": True, "broken": []}
    assert validate(result["best"]["plan"]) == (True, None)
    assert result["best"]["cost"] == 98
    assert result["best"]["approval"] == 48.41
    assert "your_plan" not in price([])


def test_balanced_golden():
    promise = {"type": "min_approval", "value": 50}
    result = price([promise], price([])["best"]["plan"])
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    assert result["best"]["score"] == 57.00
    assert result["price"] == result["promises"][0]["price_alone"] == 0.24
    assert result["best"]["approval"] == 53.35
    assert _keys(result["best"]["plan"]) == _keys(cache["balanced"]["plan"])
    assert not result["your_plan"]["keeps_promises"]
    assert result["your_plan"]["broken"] == [label(promise)]


ENGINE_CASES = [
    [{"type": "include", "measure": "M7", "district": "Есиль"}],
    [{"type": "include", "measure": "M7"}],
    [{"type": "include", "measure": "M7", "district": None}],
    [{"type": "include", "measure": "M2", "district": None}],
    [{"type": "exclude", "measure": "M3"}],
    [{"type": "district_project", "district": "Алматы"}],
    [{"type": "district_project", "district": "Есиль"}, {"type": "exclude", "measure": "M3"}],
    [{"type": "min_districts", "value": 3}],
    [{"type": "min_districts", "value": 5}],
    [{"type": "max_cost", "value": 80}],
    [{"type": "min_approval", "value": 55}],
    [{"type": "no_critical"}],
    [{"type": "min_district_delta", "district": "Алматы", "value": 1.0}],
    [{"type": "include", "measure": "M7", "district": "Есиль"},
     {"type": "min_districts", "value": 3}, {"type": "min_approval", "value": 50},
     {"type": "max_cost", "value": 95}],
]


@pytest.mark.parametrize("promises", ENGINE_CASES)
def test_best_satisfies_promises_in_engine(promises):
    result = price(promises)
    assert result["feasible"]
    plan = result["best"]["plan"]
    assert validate(plan) == (True, None)
    evaluation = evaluate(plan)
    political = approval(plan)
    keys = _keys(plan)
    locations = dict(keys)
    got = {target for _, target in keys if target is not None}
    state = _state(keys)
    baseline = _state(())[2]
    for p in promises:
        kind = p["type"]
        if kind == "include":
            assert p["measure"] in locations
            if p.get("district") is not None:
                assert locations[p["measure"]] == p["district"]
        elif kind == "exclude":
            assert p["measure"] not in locations
        elif kind == "district_project":
            assert p["district"] in got
        elif kind == "min_districts":
            assert len(got) >= p["value"]
        elif kind == "max_cost":
            assert plan_cost(plan) <= p["value"]
        elif kind == "min_approval":
            assert political["city"] >= p["value"]
        elif kind == "no_critical":
            assert evaluation["n_crit"] == 0
            assert min(state[1]) >= load_dataset()["crit_threshold"]
        else:
            i = district_names().index(p["district"])
            assert state[2][i] - baseline[i] >= p["value"]
    assert result["best"]["score"] == evaluation["score"]
    assert result["best"]["approval"] == political["city"]
    assert result["price"] == diff2(result["unconstrained_best"], evaluation["score"])
    assert price(promises, plan)["your_plan"]["keeps_promises"]


def _random_constraints(seed):
    rng = random.Random(seed)
    mid, district = rng.choice(_options())
    include = {"type": "include", "measure": mid}
    if rng.choice((True, False)):
        include["district"] = district
    pool = [
        {"type": "exclude", "measure": rng.choice(load_dataset()["measures"])["id"]},
        {"type": "district_project", "district": rng.choice(district_names())},
        {"type": "min_districts", "value": rng.randint(1, len(district_names()))},
        {"type": "max_cost", "value": rng.randint(75, load_dataset()["budget"])},
        {"type": "min_approval", "value": rng.uniform(40, 60)},
        {"type": "no_critical"},
        {"type": "min_district_delta", "district": rng.choice(district_names()),
         "value": rng.uniform(0.5, 2)},
    ]
    return [include, *rng.sample(pool, 3)]


RANDOM_SEEDS = (7, 41, 947)


@pytest.fixture(scope="module")
def direct_engine_results():
    """One full engine sweep; no promise table, masks or predicate helpers.

    Reuse the optimizer's legal-plan iterator, independently of promise.py.
    The separate combinations test also checks that iterator's pruning.
    """
    cases = ENGINE_CASES + [_random_constraints(seed) for seed in RANDOM_SEEDS]
    results = [{"score": -math.inf, "keys": None, "count": 0} for _ in cases]
    baseline = _state(())[2]
    names = district_names()

    def holds(p, locations, got, cost, state, city):
        kind = p["type"]
        if kind == "include":
            return (p["measure"] in locations and
                    (p.get("district") is None or locations[p["measure"]] == p["district"]))
        if kind == "exclude":
            return p["measure"] not in locations
        if kind == "district_project":
            return p["district"] in got
        if kind == "min_districts":
            return len(got) >= p["value"]
        if kind == "max_cost":
            return cost <= p["value"]
        if kind == "min_approval":
            return city >= p["value"]
        if kind == "no_critical":
            return not any(state[3])
        assert kind == "min_district_delta"
        i = names.index(p["district"])
        return state[2][i] - baseline[i] >= p["value"]

    total = 0
    for keys, cost in _valid_options():
        total += 1
        state = _state(keys)
        city = _approval_values(keys, state, baseline)[0]
        locations = dict(keys)
        got = set(locations.values()) - {None}
        for promises, result in zip(cases, results):
            if all(holds(p, locations, got, cost, state, city) for p in promises):
                result["count"] += 1
                if state[0] > result["score"]:
                    result.update(score=state[0], keys=keys)
    assert total == 694395
    return results


def _assert_exact_best(promises, expected):
    result = price(promises)
    assert result["count_feasible"] == expected["count"]
    assert result["feasible"] == (expected["keys"] is not None)
    if result["feasible"]:
        # Full precision and the winning decisions, not only a rounded score.
        assert _keys(result["best"]["plan"]) == expected["keys"]
        assert quick_score(result["best"]["plan"]) == expected["score"]
        assert result["best"]["score"] == round(expected["score"], 2)
    else:
        assert result["best"] is result["price"] is None


@pytest.mark.parametrize("index", range(len(ENGINE_CASES)))
def test_each_promise_type_exact_against_direct_engine(index, direct_engine_results):
    _assert_exact_best(ENGINE_CASES[index], direct_engine_results[index])


@pytest.mark.parametrize("seed", RANDOM_SEEDS)
def test_three_random_constraint_cross_checks(seed, direct_engine_results):
    index = len(ENGINE_CASES) + RANDOM_SEEDS.index(seed)
    assert direct_engine_results[index]["count"] > 0
    _assert_exact_best(_random_constraints(seed), direct_engine_results[index])


def test_brute_force_constrained_combinations():
    """Independent of the optimizer's pruning and the precomputed table."""
    promises = [{"type": "include", "measure": "M3", "district": "Нура"},
                {"type": "exclude", "measure": "M8"}]
    options = [key for key in _options() if key[0] not in {"M3", "M8"}]
    best, count = -math.inf, 0
    for rest in combinations(options, load_dataset()["decisions_required"] - 1):
        plan = _plan((("M3", "Нура"),) + rest)
        if validate(plan)[0]:
            count += 1
            best = max(best, quick_score(plan))
    result = price(promises)
    assert result["count_feasible"] == count
    assert quick_score(result["best"]["plan"]) == best
    assert result["best"]["score"] == round(best, 2)


def test_infeasible_pair_and_singleton():
    promises = [{"type": "include", "measure": "M1"},
                {"type": "include", "measure": "M3"},
                {"type": "max_cost", "value": load_dataset()["budget"]}]
    result = price(promises)
    assert not result["feasible"] and result["count_feasible"] == 0
    assert result["best"] is result["price"] is None
    assert all(p["feasible_alone"] for p in result["promises"])
    assert "несовместимы" in result["verdict"]
    assert "M1" in result["verdict"] and "M3" in result["verdict"]
    assert label(promises[2]) not in result["verdict"]
    p = {"type": "min_districts", "value": len(district_names()) + 1}
    result = price([p, promises[2]])
    assert not result["feasible"]
    assert not result["promises"][0]["feasible_alone"]
    assert result["promises"][0]["price_alone"] is None
    assert label(p) in result["verdict"]
    assert label(promises[2]) not in result["verdict"]


def test_higher_order_conflict_does_not_blame_unrelated_promise():
    promises = [{"type": "include", "measure": mid} for mid in ("M7", "M8", "M9")]
    for pair in combinations(promises, 2):
        assert price(list(pair))["feasible"]
    irrelevant = {"type": "max_cost", "value": load_dataset()["budget"]}
    result = price(promises + [irrelevant])
    assert not result["feasible"]
    assert all(label(p) in result["verdict"] for p in promises)
    assert label(irrelevant) not in result["verdict"]


def test_school_elsewhere_and_no_critical_are_incompatible():
    promises = [{"type": "include", "measure": "M7", "district": "Есиль"},
                {"type": "no_critical"}]
    result = price(promises)
    assert not result["feasible"]
    assert all(row["feasible_alone"] for row in result["promises"])
    assert all(label(p) in result["verdict"] for p in promises)


@pytest.mark.parametrize("promise", [
    {"type": "max_cost", "value": 0},
    {"type": "min_approval", "value": 100},
    {"type": "min_district_delta", "district": "Нура", "value": 100},
])
def test_impossible_threshold_explanation(promise):
    irrelevant = {"type": "max_cost", "value": load_dataset()["budget"]}
    result = price([irrelevant, promise])
    assert not result["feasible"]
    assert result["best"] is result["price"] is None
    assert result["count_feasible"] == 0
    assert result["promises"][1]["price_alone"] is None
    assert not result["promises"][1]["feasible_alone"]
    assert "невыполнимо обещание" in result["verdict"]
    assert label(promise) in result["verdict"]
    assert label(irrelevant) not in result["verdict"]


def test_your_plan_and_no_mutation():
    doc = copy.deepcopy(load_dataset()["reference"]["doc_example"])
    promises = [{"type": "include", "measure": "M7", "district": "Есиль"},
                {"type": "exclude", "measure": "M3"},
                {"type": "district_project", "district": "Алматы"},
                {"type": "min_district_delta", "district": "Алматы", "value": 1}]
    original = copy.deepcopy((doc, promises))
    result = price(promises, doc)
    assert (doc, promises) == original
    assert result["your_plan"] == {"score": 56.54, "keeps_promises": False,
                                   "broken": [label(promises[i]) for i in (0, 2, 3)]}
    result["best"]["plan"]["decisions"].clear()
    assert validate(price(promises)["best"]["plan"])[0]
    with pytest.raises(ValueError, match="ровно"):
        price([], {"decisions": []})


@pytest.mark.parametrize("promise", [
    None, [], "include", {}, {"type": []}, {"type": "unknown"},
    {"type": "include"}, {"type": "include", "measure": []},
    {"type": "include", "measure": "M99"},
    {"type": "include", "measure": "M2", "district": "Есиль"},
    {"type": "include", "measure": "M7", "district": "Нет"},
    {"type": "district_project", "district": None},
    {"type": "district_project", "district": []},
    {"type": "exclude", "measure": "M3", "district": None},
    {"type": "no_critical", "value": 0},
    {"type": "max_cost", "value": -1},
    {"type": "max_cost", "value": "80"},
    {"type": "max_cost", "value": True},
    {"type": "max_cost", "value": float("inf")},
    {"type": "max_cost", "value": float("nan")},
    {"type": "max_cost", "value": 10 ** 400},
    {"type": "min_districts", "value": 1.5},
    {"type": "min_districts", "value": -1},
    {"type": "min_approval", "value": 101},
    {"type": "min_approval", "value": -1},
    {"type": "min_district_delta", "district": "Алматы"},
])
def test_bad_promises_have_russian_value_error(promise):
    for function, argument in ((price, [promise]), (label, promise)):
        with pytest.raises(ValueError, match="[А-Яа-я]"):
            function(argument)


@pytest.mark.parametrize("value", [None, {}, (), "обещание"])
def test_promises_must_be_list(value):
    with pytest.raises(ValueError, match="списком"):
        price(value)


def test_labels_and_precomputed_catalog():
    assert label({"type": "include", "measure": "M7", "district": "Есиль"}) == "Школа + детсад в Есиле"
    assert label({"type": "exclude", "measure": "M3"}) == "Без ЛРТ"
    for d in load_dataset()["districts"]:
        assert label({"type": "district_project", "district": d["name"]}) == f"Проект в {d['cases']['loc']}"
        assert d["cases"]["gen"] in label({"type": "min_district_delta", "district": d["name"], "value": 1})
    catalog = promise_catalog()
    assert len(catalog) == 8
    for entry in catalog:
        row = price(entry["promises"])["promises"][0]
        assert all(entry[key] == value for key, value in row.items())
    catalog[0]["label"] = "изменено"
    assert promise_catalog()[0]["label"] != "изменено"


def test_catalog_has_ready_to_submit_promises_and_ui_prices():
    for entry in promise_catalog():
        assert {"label", "promises", "price", "feasible", "best_score"} <= entry.keys()
        result = price(entry["promises"])
        assert entry["feasible"] == result["feasible"]
        assert entry["price"] == result["price"]
        assert entry["price"] is not None
        assert entry["best_score"] == result["best"]["score"]
        assert entry["label"] == result["promises"][0]["label"]
    catalog = promise_catalog()
    catalog[0]["promises"][0]["value"] = -1
    assert promise_catalog()[0]["promises"][0]["value"] >= 0


def test_malformed_identifiers_do_not_format_untrusted_objects():
    # Even a finite integer can fail Python's decimal string conversion.
    huge = 10 ** 5000
    for promise in ({"type": huge}, {"type": {"nested": huge}},
                    {"type": "include", "measure": huge},
                    {"type": "include", "measure": [huge]},
                    {"type": "district_project", "district": huge},
                    {"type": "district_project", "district": {"nested": huge}}):
        for function, argument in ((price, [promise]), (label, promise)):
            with pytest.raises(ValueError, match="[А-Яа-я]"):
                function(argument)


def test_table_lossless_against_engine_and_size():
    metadata, columns = _load_table()
    assert TABLE_PATH.stat().st_size < 3_000_000
    baseline = _state(())[2]
    indices = random.Random(947).sample(range(metadata["count"]), 100)
    for index in indices:
        plan = _record(index)["plan"]
        assert validate(plan)[0]
        keys = _keys(plan)
        state = _state(keys)
        assert columns["score"][index] == state[0]
        assert columns["cost"][index] == plan_cost(plan)
        assert columns["approval"][index] == _approval_values(keys, state, baseline)[0]
        assert columns["n_crit"][index] == sum(state[3])
        got = {target for _, target in keys if target is not None}
        assert columns["n_districts"][index] == len(got)
        for i, name in enumerate(district_names()):
            assert bool(columns["districts"][index] & (1 << i)) == (name in got)
            assert columns["delta:" + name][index] == state[2][i] - baseline[i]
    assert _join_approval(*_split_approval(columns["approval"])) == columns["approval"]


def test_precision_boundaries_agree_for_table_and_submitted_plan():
    plan = price([])["best"]["plan"]
    keys = _keys(plan)
    state, baseline = _state(keys), _state(())[2]
    city = _approval_values(keys, state, baseline)[0]
    boundaries = [("min_approval", city, {})]
    boundaries.extend(("min_district_delta", after - before, {"district": name})
                      for name, after, before in zip(district_names(), state[2], baseline))
    for kind, value, extra in boundaries:
        exact = {"type": kind, "value": value, **extra}
        above = {**exact, "value": math.nextafter(value, math.inf)}
        result = price([exact], plan)
        assert result["your_plan"]["keeps_promises"]
        assert result["price"] == 0
        assert result["promises"][0]["value"] == value
        assert str(value) in result["promises"][0]["label"]
        rejected = price([above], plan)
        assert not rejected["your_plan"]["keeps_promises"]
        assert rejected["promises"][0]["value"] == above["value"]
        assert str(above["value"]) in rejected["your_plan"]["broken"][0]


def test_budget_threshold_is_not_rounded_into_a_different_promise():
    promise = {"type": "max_cost", "value": 60.999}
    result = price([promise])
    assert not result["feasible"]
    assert result["count_feasible"] == 0
    assert result["promises"][0]["value"] == 60.999
    assert result["promises"][0]["label"] == "Бюджет — не больше 60.999"
    assert "невыполнимо обещание «Бюджет — не больше 60.999»" in result["verdict"]
    rounded = price([{**promise, "value": 61}])
    assert rounded["feasible"]
    assert rounded["count_feasible"] == 625
    assert rounded["best"]["score"] == 55.67
    assert rounded["price"] == diff2(57.24, 55.67)
    assert promise == {"type": "max_cost", "value": 60.999}


def test_district_delta_uses_full_precision_despite_rounded_display():
    plan = price([])["best"]["plan"]
    state, baseline = _state(_keys(plan)), _state(())[2]
    displayed = evaluate(plan)["districts"]
    for i, name in enumerate(district_names()):
        delta = state[2][i] - baseline[i]
        shown = diff2(displayed[name]["D_after"], displayed[name]["D_before"])
        promise = {"type": "min_district_delta", "district": name, "value": shown}
        result = price([promise], plan)
        assert result["your_plan"]["keeps_promises"] == (delta >= shown)
        assert result["price"] == diff2(result["unconstrained_best"], result["best"]["score"])


def test_query_after_load_and_cold_load_latency():
    _load_table()
    _mask.cache_clear()
    _best_index.cache_clear()
    start = time.perf_counter()
    result = price([{"type": "min_approval", "value": 50},
                    {"type": "district_project", "district": "Есиль"},
                    {"type": "max_cost", "value": 97}])
    assert time.perf_counter() - start < 1.5
    assert result["feasible"]
    _load_table.cache_clear()
    _mask.cache_clear()
    _best_index.cache_clear()
    start = time.perf_counter()
    assert price([{"type": "min_approval", "value": 50}])["price"] == 0.24
    assert time.perf_counter() - start < 5
    _load_table.cache_clear()
    _mask.cache_clear()
    _best_index.cache_clear()
    start = time.perf_counter()
    assert all(entry["price"] is not None for entry in promise_catalog())
    assert time.perf_counter() - start < 5
