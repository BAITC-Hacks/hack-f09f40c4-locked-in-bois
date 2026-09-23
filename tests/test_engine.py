"""Golden organizer examples, rule precedence, optimizer and crisis contracts."""

import copy
import json
import os
import random
from itertools import islice

import pytest

from engine.approval import CONSTANTS, approval
from engine.model import (REPO_ROOT, district_names, indicator_codes, load_dataset,
                          load_events, measures_by_id, normalize_plan, plan_cost)
from engine.optimize import (CACHE_PATH, _options, _valid_options, best_single_swaps,
                             generate_cache, optimize_info, what_if)
from engine.score import _keys, _state, compute, evaluate, quick_score
from engine.shock import pick_event, resolve, shock, shocked_base
from engine.validate import validate


def make_plan(*ids, district="Нура"):
    measures = measures_by_id()
    return {"decisions": [{"measure": mid, "district": district if measures[mid]["type"] == "district" else None}
                           for mid in ids]}


@pytest.fixture
def doc():
    return {"decisions": copy.deepcopy(load_dataset()["reference"]["doc_example"]["decisions"])}


@pytest.fixture
def optimum():
    return make_plan("M2", "M3", "M8", "M9", "M14")


def test_dataset_helpers_and_repository_relative_paths(monkeypatch):
    monkeypatch.chdir(REPO_ROOT / "tests")
    assert load_dataset() is load_dataset()
    assert load_dataset(REPO_ROOT / "data/dataset.json") == load_dataset()
    assert indicator_codes() == [i["code"] for i in load_dataset()["indicators"]]
    assert district_names() == [d["name"] for d in load_dataset()["districts"]]
    assert len(_options()) == 54
    assert len(load_events()) == 3


def test_baseline():
    result = compute([])
    assert result["score"] == 52.56
    assert result["n_crit"] == 2
    assert result["baseline"] == result["score"]


def test_doc_example(doc):
    result = evaluate(doc)
    assert result["cost"] == 95
    assert result["score"] == 56.54
    # Subtract before rounding: 56.54307 - 52.55768 = 3.98539.
    assert result["delta"] == 3.98  # displayed values add up: 56.54 - 52.56
    assert result["synergies_triggered"] == [{"pair": ["M10", "M12"], "district": "Нура", "bonus": {"B1": 2}}]
    assert result["realized_share"]["M7"] == 0.625
    assert result["districts"]["Нура"]["D_before"] == 49.18
    assert result["districts"]["Нура"]["D_after"] == 52.96


def test_cheapest():
    result = evaluate(make_plan("M9", "M11", "M10", "M4", "M12"))
    assert result["cost"] == 61
    assert result["score"] == 55.67


def test_optimum(optimum):
    result = evaluate(optimum)
    assert result["cost"] == 98
    assert result["score"] == 57.24


def test_evaluate_shape_and_timeline(doc):
    result = evaluate(doc)
    assert set(result) == {"score", "baseline", "delta", "cost", "remaining", "d_avg", "d_min", "d_min_district",
                           "districts", "n_crit", "n_crit_before", "crit_cells", "resolved_crit_cells", "contributions",
                           "timeline", "synergies_triggered", "realized_share"}
    assert result["timeline"][0] == {"q": 0, "score": result["baseline"]}
    assert result["timeline"][8] == {"q": 8, "score": result["score"]}
    assert len(result["districts"]) == 5
    assert all(len(d["after"]) == len(d["before"]) == 10 for d in result["districts"].values())
    assert len(result["resolved_crit_cells"]) == 2


@pytest.mark.parametrize("ids,fragment", [
    (("M9", "M10", "M12", "M14"), "ровно 5"),
    (("M9", "M9", "M10", "M12", "M14"), "Повтор"),
    (("M3", "M5", "M7", "M8", "M13"), "Превышен бюджет"),
    (("M7", "M8", "M9", "M10", "M12"), "Больше 2 мер в направлении «Соцсфера»"),
    (("M1", "M3", "M9", "M10", "M12"), "M1 и M3"),
    (("M4", "M7", "M9", "M10", "M12"), "конфликт за участок"),
    (("M5", "M13", "M9", "M10", "M12"), "дублирование программы"),
])
def test_validation_rules(ids, fragment):
    ok, reason = validate(make_plan(*ids))
    assert ok is False
    assert reason and fragment in reason


@pytest.mark.parametrize("field,value,fragment", [
    ("measure", "M99", "Неизвестная мера"),
    ("district", "Неизвестный", "Неизвестный район"),
    ("district", None, "необходимо указать район"),
])
def test_unknown_ids_and_missing_district(doc, field, value, fragment):
    doc["decisions"][0][field] = value
    ok, reason = validate(doc)
    assert ok is False
    assert reason and fragment in reason


def test_city_must_have_no_district(doc):
    doc["decisions"][3]["district"] = "Нура"
    assert validate(doc) == (False, "Для городской меры M12 район не указывается")
    del doc["decisions"][3]["district"]
    assert validate(doc) == (True, None)
    assert normalize_plan(doc)[3]["district"] is None


def test_validation_precedence(doc):
    doc["decisions"][0]["measure"] = "M99"
    doc["decisions"][1]["measure"] = "M99"
    assert "Повтор" in validate(doc)[1]
    doc["decisions"].pop()
    assert "ровно 5" in validate(doc)[1]
    expensive = make_plan("M3", "M5", "M7", "M8", "M13")
    expensive["decisions"][0]["district"] = None
    assert "Превышен бюджет" in validate(expensive)[1]
    expensive["decisions"][0]["district"] = "Нет"
    assert "Неизвестный район" in validate(expensive)[1]


def test_local_conflict_allowed_in_different_districts():
    plan = make_plan("M4", "M7", "M9", "M10", "M12")
    plan["decisions"][0]["district"] = "Алматы"
    assert validate(plan) == (True, None)


def test_invalid_evaluation_raises():
    with pytest.raises(ValueError, match="ровно 5"):
        evaluate([])


def test_trap_and_strict_threshold():
    before = compute([])
    result = compute([{"measure": "M11", "district": "Алматы"}])
    assert result["n_crit"] == before["n_crit"] + 1
    assert {"district": "Алматы", "indicator": "T1", "value": 38.25} in result["crit_cells"]
    assert not any(c["district"] == "Алматы" for c in before["crit_cells"])


def test_clipping_occurs_once_and_plan_order_is_irrelevant():
    plan = [{"measure": "M2"}, {"measure": "M11", "district": "Алматы"}]
    base = {"Алматы": {"T1": 98}}
    assert compute(plan, base)["districts"]["Алматы"]["after"][0] == 99.25
    assert compute(plan, base) == compute(list(reversed(plan)), base)
    assert compute([plan[0]], base)["districts"]["Алматы"]["after"][0] == 100
    assert compute([], {"Алматы": {"T1": -5}})["districts"]["Алматы"]["after"][0] == 0


@pytest.mark.parametrize("pair,code,district,bonus", [
    (("M1", "M2"), "T1", "Алматы", 2),
    (("M10", "M12"), "B1", "Нура", 2),
    (("M5", "M6"), "E2", "Сарыарка", 2),
])
def test_fixed_synergies_and_activation(pair, code, district, bonus):
    plan = make_plan(*pair, district=district)
    data = load_dataset()
    measures = measures_by_id()
    lag = max(measures[mid]["lag"] for mid in pair)
    cell = district_names().index(district) * len(indicator_codes()) + indicator_codes().index(code)
    baseline = _state(())[1][cell]
    for q in (lag, lag + 1, data["horizon_quarters"]):
        effects = sum(measures[mid]["effects"].get(code, 0) * max(0, q - measures[mid]["lag"])
                      / data["horizon_quarters"] for mid in pair)
        actual = _state(_keys(plan), q=q)[1][cell]
        assert actual == baseline + effects + (bonus if q > lag else 0)
    assert compute(plan)["synergies_triggered"][0]["district"] == district


def test_remove_one_displayed_values_add_up(doc):
    result = evaluate(doc)
    for i, item in enumerate(result["contributions"]):
        remaining = doc["decisions"][:i] + doc["decisions"][i + 1:]
        without = quick_score(remaining)
        marginal = quick_score(doc) - without
        assert item["score_without"] == round(without, 2)
        assert item["marginal"] == round(round(quick_score(doc), 2) - round(without, 2), 2)
        assert round(item["score_without"] + item["marginal"], 2) == result["score"]
        assert item["marginal_per_cost"] == round(marginal / item["cost"], 2)


def test_approval_calibration(doc, optimum):
    assert CONSTANTS == {"BASE": 50, "A": 4, "GOT": 10, "MISS": 12, "CRIT": 3, "THRESHOLD": 50}
    assert approval(optimum)["city"] == 48.41 < 50
    assert approval(doc)["city"] == 50.78 > 50
    assert approval(optimum)["reelected"] is False
    assert approval(doc)["reelected"] is True


def test_optimizer_cache_and_rank(doc, optimum):
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    assert cache["total_valid"] == 694395
    assert cache["top"][0]["score"] == 57.24
    assert len(cache["top"]) == 200
    assert cache["min_cost"] == 61
    assert CACHE_PATH.stat().st_size < 2_000_000
    assert sum(count for _, count in cache["score_hist"]) == cache["total_valid"]
    assert cache["score_hist"] == sorted(cache["score_hist"], reverse=True)
    for entry in cache["top"] + [cache["balanced"]]:
        assert validate(entry["plan"]) == (True, None)
        assert entry["score"] == round(quick_score(entry["plan"]), 2)
        assert entry["approval"] == approval(entry["plan"])["city"]
    assert cache["balanced"]["approval"] >= 50
    info = optimize_info(doc)
    score = evaluate(doc)["score"]
    assert info["rank"] == 1 + sum(count for value, count in cache["score_hist"] if value > score)
    assert info["percentile"] == round(100 * sum(count for value, count in cache["score_hist"] if value < score) / 694395, 2)
    assert optimize_info(optimum)["rank"] == 1
    assert optimize_info(optimum)["gap_to_best"] == 0
    for budget, entry in cache["best_by_max_cost"].items():
        assert entry["cost"] <= int(budget)
        assert entry["score"] == round(quick_score(entry["plan"]), 2)
        assert validate(entry["plan"])[0]
    frontier = cache["pareto"]
    assert all(a["cost"] < b["cost"] and a["score"] < b["score"] for a, b in zip(frontier, frontier[1:]))


def test_pruning_matches_validator():
    for keys, cost in islice(_valid_options(), 100):
        plan = [{"measure": mid, "district": district} for mid, district in keys]
        assert validate(plan) == (True, None)
        assert plan_cost(plan) == cost


def test_quick_path_against_independent_formula():
    rng = random.Random(123)
    data = load_dataset()
    measures = measures_by_id()
    for _ in range(200):
        keys = rng.sample(_options(), 5)
        if len({mid for mid, _ in keys}) != 5:
            continue
        values = {d["name"]: dict(d["values"]) for d in data["districts"]}
        for mid, target in keys:
            m = measures[mid]
            for district in ([target] if m["type"] == "district" else district_names()):
                for code, effect in m["effects"].items():
                    values[district][code] += effect * (data["horizon_quarters"] - m["lag"]) / data["horizon_quarters"]
        locations = dict(keys)
        for synergy in data["synergies"]:
            if all(mid in locations for mid in synergy["pair"]):
                for code, bonus in synergy["bonus"].items():
                    values[locations[synergy["district_of"]]][code] += bonus
        ds, crits = [], 0
        for district in data["districts"]:
            clipped = {k: min(100, max(0, v)) for k, v in values[district["name"]].items()}
            ds.append(sum(i["weight"] * clipped[i["code"]] for i in data["indicators"]))
            crits += sum(v < data["crit_threshold"] for v in clipped.values())
        sw = data["score_weights"]
        expected = sw["avg"] * sum(d["pop"] * value for d, value in zip(data["districts"], ds)) + sw["min"] * min(ds) - sw["crit_penalty"] * crits
        plan = [{"measure": mid, "district": target} for mid, target in keys]
        assert quick_score(plan) == pytest.approx(expected, abs=1e-12)
        assert compute(plan)["score"] == round(expected, 2)


def test_what_if_and_swaps(doc):
    swaps = best_single_swaps(doc, k=3)
    assert len(swaps) == 3
    assert [s["score"] for s in swaps] == sorted((s["score"] for s in swaps), reverse=True)
    for swap in swaps:
        result = what_if(doc, swap)
        assert result["valid"] is True
        assert result["score"] == swap["score"]
        assert result["delta_vs_current"] == swap["gain"]
    invalid = what_if(doc, {"out": "M7", "in": {"measure": "M5", "district": "Нура"}})
    assert invalid["valid"] is False and invalid["reason"]
    assert invalid["score"] is invalid["delta_vs_current"] is None
    assert best_single_swaps(doc, k=0) == []


@pytest.mark.parametrize("seed", range(3))
def test_shock_and_resolve_each_event(doc, seed):
    original = copy.deepcopy(doc)
    event = pick_event(seed)
    assert event == pick_event(seed + len(load_events()))
    base = shocked_base(event)
    crisis = shock(doc, seed)
    assert crisis["score_before"] == 56.54
    assert crisis["new_baseline_score"] == compute(doc, base)["score"]
    result = resolve(doc, event["id"], crisis["best_swaps"][0])
    assert validate(result["new_plan"]) == (True, None)
    assert result["crisis_cost"] >= 0
    assert result["baseline"] == compute([], base)["score"]
    assert result["timeline"][0]["score"] == result["baseline"]
    assert result["timeline"][8]["score"] == result["score"]
    assert result["swap_was_optimal"] is True
    assert result["score"] == crisis["best_swaps"][0]["score"]
    assert doc == original


def test_resolve_rejects_invalid_noop_and_unknown_event(doc):
    event = pick_event(0)
    for swap, fragment in [
        ({"out": "M7", "in": doc["decisions"][0]}, "ровно одно"),
        ({"out": "M99", "in": doc["decisions"][0]}, "отсутствует"),
        ({"out": "M10", "in": {"measure": "M3", "district": "Нура"}}, "Превышен бюджет"),
    ]:
        with pytest.raises(ValueError, match=fragment):
            resolve(doc, event["id"], swap)
    with pytest.raises(ValueError, match="Неизвестное событие"):
        resolve(doc, "unknown", {})


def test_all_plan_apis_accept_bare_lists_without_mutation(doc):
    original = copy.deepcopy(doc)
    bare = doc["decisions"]
    for function in (normalize_plan, plan_cost, validate, compute, evaluate, quick_score, approval,
                     optimize_info, best_single_swaps):
        assert function(doc) == function(bare)
    assert shock(doc, 0) == shock(bare, 0)
    swap = best_single_swaps(doc)[0]
    assert what_if(doc, swap) == what_if(bare, swap)
    assert resolve(doc, pick_event(0)["id"], swap) == resolve(bare, pick_event(0)["id"], swap)
    assert doc == original


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("RUN_SLOW") != "1", reason="Set RUN_SLOW=1 for full enumeration")
def test_full_enumeration():
    cache = generate_cache()
    assert cache["total_valid"] == 694395
    assert cache["top"][0]["score"] == 57.24
