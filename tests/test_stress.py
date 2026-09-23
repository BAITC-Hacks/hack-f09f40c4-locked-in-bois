"""Stress-test contracts, engine parity, cached maximin search and latency."""

from copy import deepcopy
from collections import Counter
from itertools import combinations, product
import heapq
import os
import re
import time

import pytest

from engine import stress
from engine.approval import approval
from engine.model import district_names, load_dataset, measures_by_id, plan_cost
from engine.optimize import _replace, best_single_swaps, optimize_info
from engine.score import _keys, _state, diff2
from engine.shock import load_events, resolve, shock, shocked_base
from engine.validate import validate


@pytest.fixture
def doc():
    return {"decisions": deepcopy(load_dataset()["reference"]["doc_example"]["decisions"])}


def test_doc_contract(doc):
    result = stress.stress_test(doc)
    assert result["score"] == 56.54
    assert [s["event_id"] for s in result["scenarios"]] == [e["id"] for e in load_events()] + ["all"]
    assert len(result["scenarios"]) == 4
    assert all(s["loss"] >= 0 for s in result["scenarios"])
    assert all(s["insurance"]["recovered"] >= 0 for s in result["scenarios"])
    assert re.search("[А-Яа-яЁё]", result["verdict"])
    singles = result["scenarios"][:-1]
    worst = min(singles, key=lambda s: s["score"])
    assert result["worst_event"] == worst["event_id"]
    assert result["worst_score"] == worst["score"]
    assert result["max_loss"] == diff2(result["score"], worst["score"])
    assert result["worst_score_all"] == min(s["score"] for s in result["scenarios"])
    assert result["max_loss_all"] == diff2(result["score"], result["worst_score_all"])


@pytest.mark.parametrize("seed", range(3))
def test_matches_shock_and_actual_insurance(doc, seed):
    result = stress.stress_test(doc)["scenarios"][seed]
    existing = shock(doc, seed)
    event = load_events()[seed]
    base = shocked_base(event)
    assert result["score"] == existing["new_baseline_score"]
    if event["id"] == "heating_almaty":
        assert result["score"] == 55.34
    assert result["effects"] == event["effects"]
    swap = result["insurance"]
    assert swap["out"] == existing["best_swaps"][0]["out"]
    assert swap["in"] == existing["best_swaps"][0]["in"]
    changed = _replace(doc, swap)
    assert validate(changed) == (True, None)
    assert swap["score"] == round(_state(_keys(changed), base)[0], 2)
    assert swap["recovered"] == diff2(swap["score"], result["score"])


def test_combined_effects_are_additive_even_on_same_cell(doc):
    events = load_events()
    bases = [shocked_base(event) for event in events]
    combined = stress._merged_base(bases)
    expected = {d["name"]: dict(d["values"]) for d in load_dataset()["districts"]}
    for event in events:
        for code, effect in event["effects"].items():
            expected[event["district"]][code] += effect
    assert combined == expected
    result = stress.stress_test(doc)["scenarios"][-1]
    assert result["score"] == round(_state(_keys(doc), expected)[0], 2)
    assert result["district"] is None
    assert result["title"] == "Чёрная зима: все три кризиса сразу"
    best = best_single_swaps(doc, expected, k=1)[0]
    assert result["insurance"] == {k: best[k] for k in ("out", "in", "score")} | {
        "recovered": diff2(best["score"], result["score"])}
    twice = stress._merged_base([bases[0], bases[0]])
    event = events[0]
    original = next(d["values"] for d in load_dataset()["districts"] if d["name"] == event["district"])
    for code, effect in event["effects"].items():
        assert twice[event["district"]][code] == original[code] + 2 * effect
    assert bases == [shocked_base(event) for event in events]


def test_new_critical_cells_compare_with_normal_plan(doc):
    result = stress.stress_test(doc)
    assert result["scenarios"][0]["new_crit_cells"] == [
        {"district": "Алматы", "indicator": "C1", "value": 38}]
    assert result["scenarios"][1]["new_crit_cells"] == [
        {"district": "Сарыарка", "indicator": "E2", "value": 38.75}]
    # Exactly 40 is not critical, including after the school crisis.
    assert result["scenarios"][2]["new_crit_cells"] == []
    assert result["scenarios"][3]["new_crit_cells"] == (
        result["scenarios"][0]["new_crit_cells"] + result["scenarios"][1]["new_crit_cells"])
    optimum = stress.robust_info()["optimum_under_events"]["plan"]
    assert stress.stress_test(optimum)["scenarios"][0]["new_crit_cells"] == []


def test_cache_plans_recompute_and_dominate_optimum(doc):
    cache = stress.robust_info()
    assert cache["total_valid"] == 694395
    assert cache["dataset_version"] == load_dataset()["version"]
    assert cache["generation_seconds"] > 0
    assert stress.CACHE_PATH.stat().st_size < 1_000_000
    assert len(cache["top_robust"]) == 10
    optimum = cache["optimum_under_events"]
    assert optimum["score"] == 57.24
    assert optimum["plan"] == optimize_info(doc)["best"]["plan"]
    assert cache["crisis_proof"]["worst_score"] >= optimum["worst_score"]
    assert cache["crisis_proof_all"]["all_score"] >= optimum["all_score"]
    assert cache["crisis_proof"] == cache["top_robust"][0]
    for record in cache["top_robust"] + [cache["crisis_proof_all"], optimum]:
        plan = record["plan"]
        assert validate(plan) == (True, None)
        keys = _keys(plan)
        scores = {event["id"]: _state(keys, base)[0] for event, base in stress._scenario_bases()}
        assert record["event_scores"] == {key: round(value, 2) for key, value in scores.items()}
        worst = min(value for key, value in scores.items() if key != "all")
        assert record["worst_score"] == round(worst, 2)
        assert record["worst_score_all"] == round(min(scores.values()), 2)
        assert record["score"] == round(_state(keys)[0], 2)
        assert record["max_loss"] == diff2(record["score"], worst)
        assert record["cost"] == plan_cost(plan)
        assert record["approval"] == approval(plan)["city"]


def test_rank_histogram_and_percentile(doc):
    cache = stress.robust_info()
    hist = cache["worst_hist"]
    assert hist == sorted(hist, reverse=True)
    assert sum(count for _, count in hist) == cache["total_valid"]
    for plan in (doc, cache["crisis_proof"]["plan"]):
        result = stress.stress_test(plan)
        assert result["robust_total"] == cache["total_valid"]
        assert result["robust_rank"] == 1 + sum(n for score, n in hist if score > result["worst_score"])
        assert result["robust_percentile"] == round(
            100 * sum(n for score, n in hist if score < result["worst_score"]) / cache["total_valid"], 2)
    assert stress.stress_test(cache["crisis_proof"]["plan"])["robust_rank"] == 1


def test_optimum_insurance_does_not_claim_unavailable_recovery():
    plan = stress.robust_info()["optimum_under_events"]["plan"]
    result = stress.stress_test(plan)
    smog = next(s for s in result["scenarios"] if s["event_id"] == "smog_saryarka")
    assert smog["score"] == 56.08
    assert smog["loss"] == 1.16
    # A real one-decision swap is compulsory in best_single_swaps; no no-op
    # may be invented, nor may its negative gain be silently clamped to zero.
    assert smog["insurance"]["score"] == 56.02
    assert smog["insurance"]["recovered"] == -0.06


def test_generate_cache_search_order_and_tie_break(monkeypatch, doc):
    plans = [doc] + [entry["plan"] for entry in stress.robust_info()["top_robust"]]
    # Recompute the expected ordering independently from rounded cache records.
    bases = [shocked_base(event) for event in load_events()]
    expected = sorted(plans, key=lambda plan: (
        min(_state(_keys(plan), base)[0] for base in bases), _state(_keys(plan))[0]), reverse=True)
    monkeypatch.setattr(stress, "_valid_options", lambda: iter((_keys(plan), plan_cost(plan)) for plan in plans))
    generated = stress.generate_cache()
    assert generated["total_valid"] == len(plans)
    assert [_keys(entry["plan"]) for entry in generated["top_robust"]] == [_keys(plan) for plan in expected[:10]]
    assert sum(n for _, n in generated["worst_hist"]) == len(plans)


@pytest.mark.parametrize("plan", [None, [], {"decisions": []}])
def test_invalid_plan_validates_before_cache(plan, monkeypatch):
    monkeypatch.setattr(stress, "_load_cache", lambda: pytest.fail("Cache accessed before validation"))
    with pytest.raises(ValueError, match=re.escape(validate(plan)[1])):
        stress.stress_test(plan)


def test_public_results_do_not_mutate_shared_data(doc):
    original = deepcopy(doc)
    result = stress.stress_test(doc)
    assert result == stress.stress_test(doc["decisions"])
    result["crisis_proof_plan"]["score"] = -1
    result["scenarios"][0]["effects"].clear()
    info = stress.robust_info()
    info["top_robust"].clear()
    assert len(stress.robust_info()["top_robust"]) == 10
    assert stress.stress_test(doc)["crisis_proof_plan"]["score"] > 0
    assert load_events()[0]["effects"]
    assert doc == original


def test_response_time_after_loading_cache(doc):
    stress.robust_info()
    start = time.perf_counter()
    stress.stress_test(doc)
    assert time.perf_counter() - start < 1.5


def _independent_bases():
    """Construct from event effects, without stress's merge/scenario helpers."""
    original = {d["name"]: dict(d["values"]) for d in load_dataset()["districts"]}
    combined = deepcopy(original)
    bases = []
    for event in load_events():
        base = deepcopy(original)
        for code, effect in event["effects"].items():
            base[event["district"]][code] += effect
            combined[event["district"]][code] += effect
        bases.append(base)
    return bases + [combined]


def _independent_plans():
    """Different traversal from optimizer's combinations of district options."""
    data = load_dataset()
    for selected in combinations(data["measures"], data["decisions_required"]):
        if sum(m["cost"] for m in selected) > data["budget"]:
            continue
        if max(Counter(m["direction"] for m in selected).values()) > data["max_per_direction"]:
            continue
        ids = {m["id"] for m in selected}
        if any(r["scope"] == "anywhere" and set(r["pair"]) <= ids
               for r in data["incompatibilities"]):
            continue
        for targets in product(*(district_names() if m["type"] == "district" else [None]
                                 for m in selected)):
            plan = {"decisions": [{"measure": m["id"], "district": target}
                                  for m, target in zip(selected, targets)]}
            if validate(plan)[0]:
                yield plan


@pytest.mark.skipif(os.environ.get("STRESS_EXHAUSTIVE") != "1",
                    reason="Set STRESS_EXHAUSTIVE=1 for the independent full cache audit")
def test_independent_exhaustive_cache_certificate():
    """Read-only audit: recompute all 694,395 plans, including the histogram."""
    cache = stress.robust_info()
    bases = _independent_bases()

    def objectives(plan):
        keys = _keys(plan)
        normal = _state(keys)[0]
        scores = [_state(keys, base)[0] for base in bases]
        return (min(scores[:-1]), normal), (min(scores), normal)

    certified = objectives(cache["crisis_proof"]["plan"])[0]
    certified_all = objectives(cache["crisis_proof_all"]["plan"])[1]
    assert round(certified[0], 2) == cache["crisis_proof"]["worst_score"]
    assert round(certified_all[0], 2) == cache["crisis_proof_all"]["worst_score_all"]
    histogram, top = Counter(), []
    total, best_normal = 0, float("-inf")
    start = time.perf_counter()
    for plan in _independent_plans():
        single, combined = objectives(plan)
        assert single <= certified, plan
        assert combined <= certified_all, plan
        histogram[round(single[0], 2)] += 1
        best_normal = max(best_normal, single[1])
        total += 1
        heapq.heappush(top, single)
        if len(top) > len(cache["top_robust"]):
            heapq.heappop(top)
    assert total == cache["total_valid"] == 694395
    assert [[score, histogram[score]] for score in sorted(histogram, reverse=True)] == cache["worst_hist"]
    assert sorted(top, reverse=True) == [objectives(r["plan"])[0] for r in cache["top_robust"]]
    assert best_normal == _state(_keys(cache["optimum_under_events"]["plan"]))[0]
    print(f"Independent audit: {total} plans in {time.perf_counter() - start:.2f}s; "
          f"maximin={certified[0]:.8f}; combined={certified_all[0]:.8f}")


@pytest.mark.parametrize("record", ["doc", "optimum_under_events", "crisis_proof", "cheapest"])
def test_insurance_exhausts_legal_replacements(record, doc):
    if record == "doc":
        plan = doc
    elif record == "cheapest":
        plan = optimize_info(doc)["pareto"][0]["plan"]
    else:
        plan = stress.robust_info()[record]["plan"]
    data, measures = load_dataset(), measures_by_id()
    result = stress.stress_test(plan)
    for scenario, base in zip(result["scenarios"], _independent_bases()):
        swap = scenario["insurance"]
        changed = _replace(plan, swap)
        assert validate(changed) == (True, None)
        decisions = changed["decisions"]
        assert sum(a != b for a, b in zip(plan["decisions"], decisions)) == 1
        assert plan_cost(changed) <= data["budget"]
        assert max(Counter(measures[d["measure"]]["direction"] for d in decisions).values()) <= data["max_per_direction"]
        locations = {d["measure"]: d["district"] for d in decisions}
        assert len(locations) == data["decisions_required"]
        for rule in data["incompatibilities"]:
            a, b = rule["pair"]
            assert not (a in locations and b in locations and
                        (rule["scope"] == "anywhere" or locations[a] == locations[b]))
        actual = _state(_keys(changed), base)[0]
        # Independent loop; do not compare best_single_swaps with itself.
        for index, old in enumerate(plan["decisions"]):
            for m in data["measures"]:
                for target in district_names() if m["type"] == "district" else [None]:
                    replacement = {"measure": m["id"], "district": target}
                    if replacement == old:
                        continue
                    candidate = deepcopy(plan)
                    candidate["decisions"][index] = replacement
                    if validate(candidate)[0]:
                        assert _state(_keys(candidate), base)[0] <= actual
        assert scenario["score"] == round(_state(_keys(plan), base)[0], 2)
        assert swap["score"] == round(actual, 2)
        assert swap["recovered"] == diff2(swap["score"], scenario["score"])
        assert scenario["loss"] == diff2(result["score"], scenario["score"])
        if scenario["event_id"] != "all":
            resolved = resolve(plan, scenario["event_id"], swap)
            assert resolved["swap_was_optimal"]
            assert resolved["score"] == swap["score"]
            assert resolved["recovered"] == swap["recovered"]


@pytest.mark.parametrize("worst,expected_rank,expected_percentile", [
    (10.001, 1, 60.0), (9.001, 5, 30.0), (8.001, 8, 0.0),
])
def test_rank_counts_ties_and_endpoints(monkeypatch, doc, worst, expected_rank, expected_percentile):
    real_state = stress._state
    monkeypatch.setattr(stress, "_state", lambda keys, base=None: (
        worst if base is not None else worst + 1, *real_state(keys, base)[1:]))
    monkeypatch.setattr(stress, "best_single_swaps", lambda *args, **kwargs: [])
    monkeypatch.setattr(stress, "_load_cache", lambda: {
        "worst_hist": [[10.0, 4], [9.0, 3], [8.0, 3]], "total_valid": 10, "crisis_proof": {},
    })
    result = stress.stress_test(doc)
    assert result["robust_rank"] == expected_rank
    assert result["robust_percentile"] == expected_percentile
    assert all(s["insurance"] is None for s in result["scenarios"])


@pytest.mark.parametrize("scores,expected", [
    ([(20, 10), (21, 10)], [1, 0]),  # Equal worst: prefer normal Score.
    ([(21, 10.001), (20, 10.004)], [1, 0]),  # Do not select on rounded scores.
    ([(20, 10), (20, 10)], [0, 1]),  # Exact tie: stable enumeration order.
    ([(20 + i, 10) for i in range(12)], list(range(11, 1, -1))),  # Heap eviction.
])
def test_exact_maximin_tie_breaks(monkeypatch, scores, expected):
    keys = [((f"candidate_{i}", None),) for i in range(len(scores))]
    bases = [object() for _ in range(4)]
    monkeypatch.setattr(stress, "_valid_options", lambda: iter((key, 0) for key in keys))
    monkeypatch.setattr(stress, "_scenario_bases", lambda: tuple(({}, base) for base in bases))
    monkeypatch.setattr(stress, "_state", lambda key, base=None: (
        scores[keys.index(key)][base is not None],))
    monkeypatch.setattr(stress, "_record", lambda key: {"index": keys.index(key)})
    result = stress.generate_cache()
    assert result["top_robust"] == [{"index": i} for i in expected]
    assert result["crisis_proof"] == result["crisis_proof_all"] == {"index": expected[0]}


def test_displayed_differences_round_operands_first(monkeypatch, doc):
    real_state = stress._state
    monkeypatch.setattr(stress, "_state", lambda keys, base=None: (
        54.004 if base is not None else 56.006, *real_state(keys, base)[1:]))
    monkeypatch.setattr(stress, "best_single_swaps", lambda *args, **kwargs: [
        {"out": "M5", "in": {"measure": "M14", "district": None}, "score": 55.01},
    ])
    result = stress.stress_test(doc)
    assert result["score"] == 56.01
    assert result["max_loss"] == result["max_loss_all"] == 2.01
    for scenario in result["scenarios"]:
        assert scenario["loss"] == 2.01
        assert scenario["insurance"]["recovered"] == 1.01
    assert "оценка 54.00, снижение на 2.01" in result["verdict"]
    record = stress._record(_keys(doc))
    assert record["max_loss"] == record["max_loss_all"] == 2.01


def test_combined_losses_are_recomputed_not_added(doc):
    result = stress.stress_test(doc)
    assert round(sum(s["loss"] for s in result["scenarios"][:-1]), 2) == 2.51
    assert result["scenarios"][-1]["loss"] == 2.52
    assert result["scenarios"][-1]["score"] == 54.02


def test_overlapping_crises_clip_only_after_merging_and_measures(monkeypatch, doc):
    cache = stress.robust_info()
    monkeypatch.setattr(stress, "_load_cache", lambda: cache)
    events = deepcopy(load_events())
    target = doc["decisions"][-1]["district"]
    for event in events:
        event.update(district=target, effects={"C1": -30})
    monkeypatch.setattr(stress, "load_events", lambda: events)
    scenarios = stress._scenario_bases.__wrapped__()
    monkeypatch.setattr(stress, "_scenario_bases", lambda: scenarios)
    original = {d["name"]: dict(d["values"]) for d in load_dataset()["districts"]}
    original[target]["C1"] -= 90
    assert scenarios[-1][1] == original
    assert scenarios[-1][0]["effects"] == {target: {"C1": -90}}
    result = stress.stress_test(doc)["scenarios"][-1]
    expected = _state(_keys(doc), original)
    assert result["score"] == round(expected[0], 2)
    assert {"district": target, "indicator": "C1", "value": 0.0} in result["new_crit_cells"]


@pytest.mark.parametrize("index", range(3))
def test_verdict_preserves_russian_district_cases(monkeypatch, doc, index):
    scenarios = stress._scenario_bases()
    real_state = stress._state
    monkeypatch.setattr(stress, "_state", lambda keys, base=None: (
        50.0 if base is scenarios[index][1] else 55.0, *real_state(keys, base)[1:]))
    monkeypatch.setattr(stress, "best_single_swaps", lambda *args, **kwargs: [])
    result = stress.stress_test(doc)
    event = load_events()[index]
    cases = next(d["cases"] for d in load_dataset()["districts"] if d["name"] == event["district"])
    assert f"в {cases['loc']}»" in result["verdict"]
    assert result["verdict"] == (
        f"Худший отдельный кризис — «{event['title']}»: "
        "оценка 50.00, снижение на 5.00; «чёрная зима» — 55.00.")


@pytest.mark.parametrize("replacement", [
    None, {}, {"measure": []}, {"measure": "unknown"},
    {"measure": "M7", "district": []}, {"measure": "M7", "district": "unknown"},
    {"measure": "M7"}, {"measure": "M14", "district": "Нура"},
    {"measure": "M8", "district": "Нура"},  # Duplicate.
    {"measure": "M13", "district": "Нура"},  # Budget.
    {"measure": "M9", "district": "Нура"},  # Three social measures.
    {"measure": "M4", "district": "Нура"},  # Local incompatibility with M7.
])
def test_invalid_full_plans_reject_before_scoring(monkeypatch, doc, replacement):
    doc["decisions"][2] = replacement
    ok, reason = validate(doc)
    assert not ok
    monkeypatch.setattr(stress, "_state", lambda *args: pytest.fail("Scored invalid plan"))
    with pytest.raises(ValueError, match=re.escape(reason)):
        stress.stress_test(doc)


@pytest.mark.parametrize("ids,conflict", [
    (["M1", "M3", "M9", "M11", "M12"], "M1 и M3"),
    (["M5", "M13", "M9", "M11", "M12"], "M5 и M13"),
])
def test_other_incompatibilities_reject_before_cache(monkeypatch, ids, conflict):
    measures = measures_by_id()
    plan = {"decisions": [{"measure": mid,
                            "district": "Нура" if measures[mid]["type"] == "district" else None}
                           for mid in ids]}
    monkeypatch.setattr(stress, "_load_cache", lambda: pytest.fail("Cache accessed before validation"))
    with pytest.raises(ValueError, match=conflict):
        stress.stress_test(plan)
