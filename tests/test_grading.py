"""Independent scans, political counterfactuals, prefix traps and golden games."""

import copy
import json
import random
from itertools import combinations
from time import perf_counter

import pytest

import engine.grading as grading
from engine.approval import approval
from engine.model import load_dataset, measures_by_id
from engine.optimize import _options, optimize_info
from engine.score import diff2, evaluate, quick_score
from engine.validate import validate


def plan(*ids, district="Нура"):
    return [{"measure": mid, "district": district if measures_by_id()[mid]["type"] == "district" else None}
            for mid in ids]


@pytest.fixture
def doc():
    return copy.deepcopy(load_dataset()["reference"]["doc_example"]["decisions"])


@pytest.fixture
def optimum():
    return plan("M2", "M3", "M8", "M9", "M14")


def direct_ceiling(prefix):
    """Deliberately independent: option combinations + official validator/kernel."""
    chosen = {d["measure"] for d in prefix}
    options = [{"measure": mid, "district": district} for mid, district in _options() if mid not in chosen]
    best = None
    for rest in combinations(options, load_dataset()["decisions_required"] - len(prefix)):
        candidate = prefix + list(rest)
        if validate(candidate)[0]:
            score = quick_score(candidate)
            best = score if best is None else max(best, score)
    return round(best, 2) if best is not None else None


def test_optimum_is_perfect_and_flat(optimum):
    result = grading.grade({"decisions": optimum})
    assert result["score"] == 57.24
    assert result["accuracy"] == 100
    assert all(m["grade"] == "best" and m["loss"] == 0 and m["best_alternative"] is None
               for m in result["moves"])
    assert [e["ceiling"] for e in result["eval_bar"]] == [57.24] * 6
    assert all(e["drop"] == 0 and not e["dead_end"] for e in result["eval_bar"][1:])
    assert result["biggest_blunder"] is None


def test_documented_game(doc):
    before = copy.deepcopy(doc)
    result = grading.grade(doc)
    assert doc == before
    assert result["score"] == 56.54
    assert result["accuracy"] == 91.94
    assert len(result["moves"]) == 5
    assert [m["index"] for m in result["moves"]] == [1, 2, 3, 4, 5]
    assert all(m["loss"] >= 0 for m in result["moves"])
    move = result["moves"][-1]
    assert move["grade"] == "good" and move["loss"] == 0.22
    assert move["contribution"] == 0.17
    assert (move["best_alternative"]["measure"], move["best_alternative"]["district"],
            move["best_alternative"]["score"]) == ("M3", "Есиль", 56.76)
    assert move["best_alternative_approval"] == 53.53
    assert move["score_best_alternative"]["score"] == 57.21
    assert move["score_best_alternative"]["district"] == "Нура"
    assert result["biggest_blunder"] is None
    assert "0.22 у M5 в Сарыарке" in result["summary"]
    assert result["eval_bar"][0] == {"step": 0, "ceiling": 57.24}
    assert result["eval_bar"][-1]["ceiling"] == 56.54
    assert [e["ceiling"] for e in result["eval_bar"]] == [57.24, 57.21, 57.21, 57.21, 57.21, 56.54]
    assert round(sum(e["drop"] for e in result["eval_bar"][1:]), 2) == diff2(57.24, 56.54)
    json.dumps(result, ensure_ascii=False, allow_nan=False)


def check_all_swaps(doc, result):
    current = quick_score(doc)
    survives = approval(doc)["reelected"]
    for i, move in enumerate(result["moves"]):
        candidates = []
        for mid, district in _options():
            replacement = {"measure": mid, "district": district}
            candidate = doc[:i] + [replacement] + doc[i + 1:]
            if validate(candidate)[0]:
                candidates.append((quick_score(candidate), approval(candidate)["reelected"]))
        score_best = max(score for score, _ in candidates)
        best = score_best
        sacrifice = False
        if diff2(best, current) > 0 and survives and not any(
                survives for score, survives in candidates if score_best - score <= 1e-10):
            surviving_best = max(score for score, survives in candidates if survives)
            sacrifice = diff2(surviving_best, current) == 0
            if not sacrifice:
                best = surviving_best
        assert move["loss"] == diff2(best, current)
        expected_grade = "sacrifice" if sacrifice else next(
            label for threshold, label in grading.GRADE_THRESHOLDS if move["loss"] <= threshold)
        assert move["grade"] == expected_grade
        alt = move["best_alternative"]
        if alt is not None:
            candidate = doc[:i] + [alt] + doc[i + 1:]
            assert validate(candidate)[0]
            assert round(quick_score(candidate), 2) == alt["score"] == round(best, 2)
            assert move["best_alternative_approval"] == approval(candidate)["city"]
            assert alt["measure"] in move["comment"]
            assert f"«{alt['name']}»" in move["comment"]
            score_alt = move["score_best_alternative"]
            candidate = doc[:i] + [score_alt] + doc[i + 1:]
            assert validate(candidate)[0]
            assert round(quick_score(candidate), 2) == score_alt["score"] == round(score_best, 2)
        else:
            assert move["loss"] == 0


def test_every_move_against_independent_single_swap_search(doc):
    check_all_swaps(doc, grading.grade(doc))


def test_first_ceiling_against_direct_scan(doc):
    assert grading.prefix_ceiling(doc[:1])["ceiling"] == direct_ceiling(doc[:1])


def test_later_ceilings_against_direct_scans(doc):
    for k in range(2, 6):
        assert grading.prefix_ceiling(doc[:k])["ceiling"] == direct_ceiling(doc[:k])


def test_cache_has_all_options_and_legal_witnesses():
    cache, options = grading._load_cache()
    assert len(options) == len(_options()) == 54
    assert cache["total_valid"] == 694395
    assert grading.CACHE_PATH.stat().st_size < 1_000_000
    for key, record in options.items():
        witness = record["witness"]
        assert validate(witness)[0]
        assert {"measure": key[0], "district": key[1]} in witness["decisions"]
        assert record["ceiling"] == round(quick_score(witness), 2)


def test_m11_creates_a_critical_cell(doc):
    doc[-1] = {"measure": "M11", "district": "Алматы"}
    assert validate(doc)[0]
    move = grading.grade(doc)["moves"][-1]
    assert move["grade"] == "blunder" and move["symbol"] == "??"
    assert move["loss"] == 1.26
    assert move["best_alternative"]["district"] == "Есиль"
    assert move["best_alternative"]["score"] == 56.76
    assert "Критических ячеек после замены — 0 вместо 1" in move["comment"]
    assert grading.grade(doc)["biggest_blunder"]["measure"] == "M11"
    assert "критическая" in move["comment"] and "в Алматы" in move["comment"]
    assert "40.00 → 38.25" in move["comment"]
    assert {"kind": "critical_cell", "district": "Алматы", "indicator": "T1",
            "before": 40.0, "after": 38.25} in move["traps"]


def test_lag_is_computed_even_for_best_move(optimum):
    move = next(m for m in grading.grade(optimum)["moves"] if m["measure"] == "M3")
    assert move["grade"] == "best"
    assert {"kind": "lag", "realized_percent": 50.0} in move["traps"]
    assert "50.00%" in move["comment"]


def test_critical_warning_disappears_when_other_measures_prevent_it():
    decisions = plan("M2", "M7", "M8", "M11", "M12")
    decisions[3]["district"] = "Алматы"
    move = grading.grade(decisions)["moves"][3]
    assert not any(t["kind"] == "critical_cell" for t in move["traps"])
    assert "критическая" not in move["comment"]


def test_available_missed_synergy_is_legal_and_beneficial():
    decisions = plan("M1", "M8", "M9", "M10", "M12")
    result = grading.grade(decisions)
    found = []
    for i, move in enumerate(result["moves"]):
        for trap in move["traps"]:
            if trap["kind"] == "missed_synergy":
                candidate = decisions[:i] + [trap["replacement"]] + decisions[i + 1:]
                assert validate(candidate)[0]
                assert round(quick_score(candidate), 2) == trap["score"]
                assert trap["gain"] == diff2(trap["score"], result["score"]) > 0
                assert set(trap["pair"]) <= {d["measure"] for d in candidate}
                found.append(trap)
    assert found


def test_prefix_budget_still_allows_completion():
    prefix = plan("M3", "M13")
    entry = grading.prefix_ceiling(prefix)
    assert not entry["dead_end"]
    assert entry["ceiling"] == direct_ceiling(prefix)


def test_prefix_budget_dead_end_before_budget_is_spent():
    prefix = plan("M3", "M13", "M7")
    assert sum(measures_by_id()[d["measure"]]["cost"] for d in prefix) == 82
    entry = grading.prefix_ceiling(prefix)
    assert entry["dead_end"] and entry["ceiling"] is None
    assert "Бюджета" in entry["reason"] and "102.00" in entry["reason"] and "2.00" in entry["reason"]
    assert direct_ceiling(prefix) is None


@pytest.mark.parametrize("ids,fragment", [
    (("M3", "M5", "M7", "M13"), "бюджет"),
    (("M7", "M8", "M9"), "направления"),
    (("M1", "M3"), "Несовместимость"),
    (("M4", "M7"), "Несовместимость"),
    (("M9", "M9"), "Повтор"),
])
def test_structural_dead_ends(ids, fragment):
    entry = grading.prefix_ceiling(plan(*ids))
    assert entry["dead_end"] and entry["ceiling"] is None
    assert fragment in entry["reason"]


def test_valid_plan_has_no_dead_prefix_and_order_changes_attribution(doc):
    first = grading.grade(doc)
    reversed_game = grading.grade(list(reversed(doc)))
    assert all(not e["dead_end"] for e in reversed_game["eval_bar"][1:])
    assert first["accuracy"] == reversed_game["accuracy"]
    assert first["eval_bar"][1]["ceiling"] != reversed_game["eval_bar"][1]["ceiling"]
    for result in (first, reversed_game):
        ceilings = [e["ceiling"] for e in result["eval_bar"]]
        assert ceilings == sorted(ceilings, reverse=True)


def test_sacrifices_buy_actual_political_survival(doc):
    found = []
    for decisions in (doc, optimize_info(doc)["balanced"]["plan"]["decisions"]):
        result = grading.grade(decisions)
        sacrifices = [m for m in result["moves"] if m["grade"] == "sacrifice"]
        assert approval(decisions)["reelected"]
        for move in sacrifices:
            found.append(move)
            i = move["index"] - 1
            candidate = decisions[:i] + [move["best_alternative"]] + decisions[i + 1:]
            assert not approval(candidate)["reelected"]
            assert move["loss"] > 0 and move["symbol"] == "!?"
            for mid, district in _options():
                candidate = decisions[:i] + [{"measure": mid, "district": district}] + decisions[i + 1:]
                if validate(candidate)[0] and approval(candidate)["reelected"]:
                    assert diff2(quick_score(candidate), result["score"]) <= 0
            if result["biggest_blunder"]:
                assert result["biggest_blunder"]["grade"] != "sacrifice"
        assert result["accuracy"] < 100
    assert found  # The balanced optimum still contains real sacrifices.


@pytest.mark.parametrize("epsilon", [0.0, -5e-11, 5e-11])
def test_surviving_score_tie_prevents_false_sacrifice(doc, monkeypatch, epsilon):
    original_score = quick_score(doc)
    required = {"M7", "M8", "M10", "M12"}

    def tied_score(candidate):
        ids = {d["measure"] for d in candidate}
        if required <= ids and ids & {"M2", "M6"}:
            return original_score + 1 + (epsilon if "M6" in ids else 0)
        return quick_score(candidate)

    def political(candidate):
        survives = "M2" not in {d["measure"] for d in candidate}
        return {"city": 55.0 if survives else 45.0, "threshold": 50, "reelected": survives}

    monkeypatch.setattr(grading, "quick_score", tied_score)
    monkeypatch.setattr(grading, "approval", political)
    move = grading.grade(doc)["moves"][-1]
    assert move["loss"] > 0
    assert move["grade"] != "sacrifice"
    assert move["best_alternative"]["measure"] == "M6"


def test_accuracy_is_monotone_in_total_local_loss(doc, optimum):
    trap = copy.deepcopy(doc)
    trap[-1] = {"measure": "M11", "district": "Алматы"}
    results = [grading.grade(p) for p in (doc, optimum, trap)]
    by_loss = sorted(results, key=lambda r: sum(m["loss"] for m in r["moves"]))
    assert [r["accuracy"] for r in by_loss] == sorted((r["accuracy"] for r in results), reverse=True)
    assert all(0 <= r["accuracy"] <= 100 for r in results)


@pytest.mark.parametrize("invalid", [None, {}, [], plan("M9"), {"decisions": [None] * 5},
                                    plan("M9") * 5, plan("M3", "M13", "M5", "M2", "M7")])
def test_invalid_plan_uses_validator_reason(invalid):
    _, reason = validate(invalid)
    with pytest.raises(ValueError) as exc:
        grading.grade(invalid)
    assert str(exc.value) == reason


def test_response_does_not_share_mutable_cached_objects(doc):
    original = grading.grade(doc)
    changed = grading.grade(doc)
    changed["moves"][0]["comment"] = "changed"
    changed["eval_bar"][1]["ceiling"] = -100
    assert grading.grade(doc) == original


def test_performance_after_loading_cache_but_without_prefix_memoization(doc):
    grading._load_cache()
    grading._ceiling.cache_clear()
    start = perf_counter()
    grading.grade(doc)
    assert perf_counter() - start < 1.5


def test_random_legal_plans_against_brute_force():
    rng = random.Random(76139)
    options = [{"measure": mid, "district": district} for mid, district in _options()]
    checked = 0
    while checked < 8:
        decisions = rng.sample(options, 5)
        if not validate(decisions)[0]:
            continue
        result = grading.grade(decisions)
        check_all_swaps(decisions, result)
        bar = result["eval_bar"]
        assert all(not step["dead_end"] for step in bar[1:])
        ceilings = [step["ceiling"] for step in bar]
        assert ceilings == sorted(ceilings, reverse=True)
        for step in range(2, 6):
            assert ceilings[step] == direct_ceiling(decisions[:step])
        assert round(sum(step["drop"] for step in bar[1:]), 2) == diff2(ceilings[0], ceilings[-1])
        checked += 1


def test_order_does_not_change_score_or_move_losses_but_changes_eval_bar(doc):
    first, reverse = grading.grade(doc), grading.grade(list(reversed(doc)))
    assert first["score"] == reverse["score"]
    assert first["accuracy"] == reverse["accuracy"]
    assert [(m["grade"], m["loss"]) for m in first["moves"]] == list(reversed([
        (m["grade"], m["loss"]) for m in reverse["moves"]]))
    assert first["eval_bar"][1]["ceiling"] != reverse["eval_bar"][1]["ceiling"]
    for step in range(1, 6):
        decision = reverse["eval_bar"][step]["decision"]
        assert {key: decision[key] for key in ("measure", "district")} == doc[-step]


def test_active_and_missed_synergies(doc):
    moves = grading.grade(doc)["moves"]
    for index in (2, 3):
        active = next(t for t in moves[index]["traps"] if t["kind"] == "synergy")
        assert active["pair"] == ["M10", "M12"] and active["gain"] == 0.07
        assert "сам бонус добавляет 0.07" in moves[index]["comment"]
    assert not any(t["kind"] == "available_synergy" for m in moves for t in m["traps"])
    incomplete = plan("M9", "M11", "M10", "M4", "M14")
    result = grading.grade(incomplete)
    missed = []
    for i, move in enumerate(result["moves"]):
        for trap in move["traps"]:
            if trap["kind"] == "missed_synergy":
                candidate = incomplete[:i] + [trap["replacement"]] + incomplete[i + 1:]
                assert validate(candidate)[0]
                assert round(quick_score(candidate), 2) == trap["score"]
                assert diff2(quick_score(candidate), result["score"]) == trap["gain"] > 0
                missed.append(trap)
    assert missed


def test_critical_rescue_and_alternative_explanations(doc):
    moves = grading.grade(doc)["moves"]
    assert {"kind": "critical_rescue", "district": "Нура", "indicator": "S1",
            "before": 38.0, "after": 48.0} in moves[0]["traps"]
    assert "Снят критический штраф в Нуре: S1 растёт с 38.00 до 48.00" in moves[0]["comment"]
    for index, score in ((2, 56.63), (3, 56.64)):
        comment = moves[index]["comment"]
        assert f"M14 «{measures_by_id()['M14']['name']}» по всему городу" in comment
        assert f"Score {score:.2f} вместо 56.54" in comment
        assert "поднимает средний индекс города с" in comment
    assert "поднимает средний индекс города с" in moves[-1]["comment"]
    assert "в Есиле" in moves[-1]["comment"]
    doc[-1]["district"] = "Нура"
    assert "поднимает индекс самого слабого района с" in grading.grade(doc)["moves"][-1]["comment"]


def test_valid_prefixes_cannot_be_dead_ends(doc, optimum):
    for decisions in (doc, optimum):
        bar = grading.grade(decisions)["eval_bar"]
        assert all(not step["dead_end"] for step in bar[1:])
        assert all(a["ceiling"] >= b["ceiling"] for a, b in zip(bar, bar[1:]))
        assert round(sum(step["drop"] for step in bar[1:]), 2) == diff2(bar[0]["ceiling"], bar[-1]["ceiling"])


def test_contributions_rounding_and_dataset_unchanged(doc):
    dataset = copy.deepcopy(load_dataset())
    result = grading.grade(doc)
    assert [m["contribution"] for m in result["moves"]] == [
        c["marginal"] for c in evaluate(doc)["contributions"]]

    def rounded(value):
        if isinstance(value, float):
            assert value == round(value, 2)
        elif isinstance(value, dict):
            for child in value.values():
                rounded(child)
        elif isinstance(value, list):
            for child in value:
                rounded(child)

    rounded(result)
    for move in result["moves"]:
        for trap in move["traps"]:
            if "pair" in trap:
                trap["pair"].append("changed")
    assert load_dataset() == dataset


def test_grade_api(doc, monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app

    with TestClient(app) as client:
        for body in ({"decisions": doc}, {"plan": {"decisions": doc}}, {"plan": doc}):
            response = client.post("/api/grade", json=body)
            assert response.status_code == 200
            assert response.json() == grading.grade(doc)
        for invalid in (None, [], {}, {"decisions": [None] * 5}):
            response = client.post("/api/grade", json={"plan": invalid})
            assert response.status_code == 422
            assert response.json()["detail"] == validate(invalid)[1]

        def unavailable(_):
            raise ValueError("Нет кэша потолков")

        monkeypatch.setattr(grading, "grade", unavailable)
        response = client.post("/api/grade", json={"decisions": doc})
        assert response.status_code == 422
        assert response.json()["detail"] == "Нет кэша потолков"
