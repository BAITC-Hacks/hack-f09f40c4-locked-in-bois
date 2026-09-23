"""Equity accounting, rounding, exact coverage and cache isolation."""

import importlib
import json
import time
from copy import deepcopy

import pytest

from engine.fairness import CACHE_PATH, _weighted_gini, fairness, price_of_spread
from engine.model import district_names, load_dataset, measures_by_id, plan_cost
from engine.optimize import optimize_info
from engine.score import _keys, _state, diff2, evaluate, quick_score
from engine.validate import validate

module = importlib.import_module("engine.fairness")


@pytest.fixture
def doc():
    return deepcopy(load_dataset()["reference"]["doc_example"])


@pytest.fixture
def optimum(doc):
    return deepcopy(optimize_info(doc)["best"]["plan"])


def _lorenz_gini(values, weights):
    # Independent sorted Lorenz-curve integral, not pairwise differences.
    population = sum(weights)
    total = sum(v * p for v, p in zip(values, weights))
    cumulative, area = 0.0, 0.0
    for value, weight in sorted(zip(values, weights)):
        next_cumulative = cumulative + value * weight / total
        area += (cumulative + next_cumulative) * weight / population
        cumulative = next_cumulative
    return 1 - area


def test_weighted_gini_known_distribution_and_invariance():
    assert _weighted_gini([0, 100], [.75, .25]) == pytest.approx(.75)
    assert _weighted_gini([0, 100], [3, 1]) == pytest.approx(.75)
    assert _weighted_gini([0, 200], [.75, .25]) == pytest.approx(.75)
    assert _weighted_gini([12, 12], [.2, .8]) == 0
    assert _weighted_gini([0, 0], [.2, .8]) == 0


def test_doc_metrics_use_engine_and_full_precision_gini(doc):
    report = fairness(doc)
    scored = evaluate(doc)
    before = _state(())[2]
    after = _state(_keys(doc))[2]
    weights = [d["pop"] for d in load_dataset()["districts"]]
    assert report["spread"] == {"before": 13.81, "after": 10.47, "delta": -3.34}
    assert report["districts_with_project"] == 2
    assert report["cost"] == 95
    assert report["most_gain"] == {"districts": ["Нура"], "delta_D": 3.78}
    assert report["least_gain"] == {"districts": ["Есиль", "Алматы", "Байконур"], "delta_D": .44}
    for key, values in (("before", before), ("after", after)):
        independent = _lorenz_gini(values, weights)
        assert report["gini"][key] == round(independent, 2)
        assert report["gini_percent"][key] == round(100 * independent, 2)
    for row in report["money_vs_people"]:
        expected = scored["districts"][row["district"]]
        assert row["D_before"] == expected["D_before"]
        assert row["D_after"] == expected["D_after"]
        assert row["delta_D"] == diff2(expected["D_after"], expected["D_before"])


def test_doc_money_vs_people(doc):
    report = fairness(doc)
    rows = {row["district"]: row for row in report["money_vs_people"]}
    assert rows["Нура"]["district_cost"] == 56
    assert rows["Нура"]["city_cost"] == 2.24
    assert rows["Нура"]["allocated"] == 58.24
    assert rows["Нура"]["budget_share_pct"] == 61.31
    assert rows["Нура"]["population_share_pct"] == 16
    assert rows["Нура"]["share_gap_pp"] == 45.31
    assert rows["Сарыарка"]["allocated"] == 27.8
    assert rows["Есиль"]["allocated"] == 3.78
    assert rows["Алматы"]["allocated"] == 3.36
    assert rows["Байконур"]["allocated"] == 1.82
    assert sum(row["allocated"] for row in rows.values()) == pytest.approx(report["cost"])
    assert sum(row["population_share_pct"] for row in rows.values()) == pytest.approx(100)


def test_optimum_and_tied_gainers(optimum):
    report = fairness(optimum)
    assert round(quick_score(optimum), 2) == 57.24
    assert report["spread"] == {"before": 13.81, "after": 10.02, "delta": -3.79}
    assert report["districts_with_project"] == 1
    nura = next(r for r in report["money_vs_people"] if r["district"] == "Нура")
    assert nura["allocated"] == 66.08
    assert nura["budget_share_pct"] == 67.43
    assert report["most_gain"]["districts"] == ["Нура"]
    assert report["least_gain"] == {"districts": ["Алматы", "Байконур"], "delta_D": 1.11}
    assert "в Нуре" in report["verdict"]
    assert "Score" not in report["verdict"]


def test_differences_and_numeric_precision(doc, optimum):
    for plan in (doc, optimum):
        report = fairness(plan)
        for field in ("spread", "gini", "gini_percent"):
            row = report[field]
            assert row["delta"] == diff2(row["after"], row["before"])
        for row in report["money_vs_people"]:
            assert row["share_gap_pp"] == diff2(row["budget_share_pct"], row["population_share_pct"])
        def check(value):
            if isinstance(value, float):
                assert value == round(value, 2)
            elif isinstance(value, dict):
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        check(report)
        json.dumps(report, allow_nan=False)


def test_no_city_cost_and_all_districts_covered():
    plan = {"decisions": [{"measure": mid, "district": name}
                           for mid, name in zip(("M3", "M4", "M8", "M9", "M10"), district_names())]}
    assert validate(plan) == (True, None)
    report = fairness(plan)
    assert report["districts_with_project"] == len(district_names())
    for row, decision in zip(report["money_vs_people"], plan["decisions"]):
        assert row["city_cost"] == 0
        assert row["district_projects"] == 1
        assert row["allocated"] == measures_by_id()[decision["measure"]]["cost"]


def test_no_mutation_and_both_plan_forms(doc):
    saved = deepcopy(doc)
    report = fairness(doc)
    assert fairness(doc["decisions"]) == report
    assert fairness(list(reversed(doc["decisions"]))) == report
    assert doc == saved
    report["money_vs_people"][0]["allocated"] = -1
    assert fairness(doc)["money_vs_people"][0]["allocated"] >= 0


@pytest.mark.parametrize("plan", [None, {}, [], {"decisions": []},
                                  {"decisions": [{"measure": "missing"}] * 5}])
def test_invalid_plans_share_validator_reason(plan):
    valid, reason = validate(plan)
    assert not valid
    with pytest.raises(ValueError) as exc:
        fairness(plan)
    assert str(exc.value) == reason


def test_cached_exact_coverage_frontier():
    result = price_of_spread()
    assert result["coverage_mode"] == "exact"
    assert result["total_valid"] == 694395
    assert result["unconstrained_best"] == 57.24
    assert [r["district_count"] for r in result["rows"]] == [1, 2, 3, 4, 5]
    assert [r["best"]["score"] for r in result["rows"]] == [57.24, 57.00, 56.59, 56.10, 54.82]
    assert sum(r["count_feasible"] for r in result["rows"]) == result["total_valid"]
    for row in result["rows"]:
        best = row["best"]
        assert validate(best["plan"]) == (True, None)
        coverage = {d["district"] for d in best["plan"]["decisions"] if d["district"] is not None}
        assert len(coverage) == row["district_count"]
        assert best["score"] == round(quick_score(best["plan"]), 2)
        assert best["cost"] == plan_cost(best["plan"])
        assert row["price"] == diff2(result["unconstrained_best"], best["score"])
    assert CACHE_PATH.stat().st_size < 200_000


def test_builder_uses_unrounded_score_and_exact_coverage(monkeypatch):
    a = (("M9", "Нура"),)
    b = (("M8", "Нура"),)
    c = (("M8", "Нура"), ("M9", "Есиль"))
    scores = {a: 50.001, b: 50.004, c: 49.999}
    monkeypatch.setattr(module, "_valid_options", lambda: iter(((a, 10), (b, 20), (c, 30))))
    monkeypatch.setattr(module, "_state", lambda keys: (scores[keys],))
    cache = module._build_cache()
    assert cache["total_valid"] == 3
    assert cache["rows"][0]["count_feasible"] == 2
    assert _keys(cache["rows"][0]["best"]["plan"]) == b
    assert _keys(cache["rows"][1]["best"]["plan"]) == c
    assert cache["rows"][2]["best"] is None
    assert cache["rows"][2]["price"] is None


def test_cache_isolation_and_no_runtime_enumeration(monkeypatch, doc):
    expected = price_of_spread()
    changed = price_of_spread()
    changed["rows"][0]["best"]["plan"]["decisions"].clear()
    assert price_of_spread() == expected
    def forbidden():
        raise AssertionError("Runtime must not enumerate plans")
    monkeypatch.setattr(module, "_valid_options", forbidden)
    fairness(doc)
    started = time.perf_counter()
    fairness(doc)
    price_of_spread()
    assert time.perf_counter() - started < 1.5


@pytest.mark.parametrize("content", [None, "invalid JSON", '{"schema": -1}'])
def test_missing_invalid_or_stale_cache_reports_build_command(monkeypatch, tmp_path, content):
    path = tmp_path / "fairness_cache.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(module, "CACHE_PATH", path)
    module._load_cache.cache_clear()
    try:
        with pytest.raises(ValueError, match="python -m engine.fairness"):
            price_of_spread()
    finally:
        module._load_cache.cache_clear()
