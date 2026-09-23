"""Stress-test contracts, engine parity, cached maximin search and latency."""

from copy import deepcopy
import re
import time

import pytest

from engine import stress
from engine.approval import approval
from engine.model import load_dataset, plan_cost
from engine.optimize import _replace, best_single_swaps, optimize_info
from engine.score import _keys, _state, diff2
from engine.shock import load_events, shock, shocked_base
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
