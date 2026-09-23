"""The displayed receipt is an exact decimal ledger of the scoring kernel."""

from copy import deepcopy
from decimal import Decimal
import json

import pytest

from engine import score as scoring
from engine.model import load_dataset, measures_by_id
from engine.receipt import receipt, receipt_markdown


def _plan(*ids, district="Нура"):
    measures = measures_by_id()
    return {"decisions": [{"measure": mid,
                           "district": district if measures[mid]["type"] == "district" else None}
                          for mid in ids]}


def _doc():
    return {"decisions": deepcopy(load_dataset()["reference"]["doc_example"]["decisions"])}


def _decimal(value):
    return Decimal(str(value))


def _sum(values):
    return sum(map(_decimal, values), Decimal(0))


@pytest.mark.parametrize("plan,expected,cost", [
    (_doc(), 56.54, 95),
    (_plan("M2", "M3", "M8", "M9", "M14"), 57.24, 98),
    (_plan("M9", "M11", "M10", "M4", "M12"), 55.67, 61),
])
def test_golden_receipts_add_exactly_in_decimal(plan, expected, cost):
    result = receipt(plan)
    official = scoring.evaluate(plan)
    assert result["score"] == official["score"] == expected
    assert result["cost"] == cost
    assert _sum(row["amount"] for row in result["lines"] if row["additive"]) == _decimal(expected)
    assert result["lines"][-1]["amount"] == expected
    assert _sum(row["avg_contribution"] for row in result["districts"]) == _decimal(result["d_avg"])
    for district in result["districts"]:
        assert district["D_after"] == official["districts"][district["district"]]["D_after"]
        assert _sum(cell["amount"] for cell in district["cells"]) == _decimal(district["D_after"])
    # Check the exact displayed lines, not an approximately equal float sum.
    for row in result["lines"]:
        if row["additive"]:
            assert _decimal(row["rounded_term"]) + _decimal(row["rounding_adjustment"]) == _decimal(row["amount"])


def test_contributions_use_unrounded_district_indices_and_diff2():
    plan = _doc()
    result = receipt(plan)
    _, _, ds, _, avg = scoring._state(scoring._keys(plan))
    data = load_dataset()
    coefficient = data["score_weights"]["avg"]
    prefix = 0.0
    for row, district, value in zip(result["districts"], data["districts"], ds):
        previous = prefix
        prefix += district["pop"] * value
        assert row["amount"] == scoring.diff2(coefficient * prefix, coefficient * previous)
        assert row["pop"] == district["pop"]
    average = next(row for row in result["lines"] if row["kind"] == "average")
    assert average["value"] == round(avg, 2)
    assert average["additive"] is False
    assert _sum(row["amount"] for row in result["districts"]) == _decimal(average["amount"])


def test_one_cent_rounding_difference_is_visible_and_already_included():
    result = receipt(_plan("M1", "M2", "M4", "M5", "M8", district="Есиль"))
    terms = [row for row in result["lines"] if row["additive"]]
    minimum = next(row for row in terms if row["kind"] == "minimum")
    assert minimum["rounded_term"] == 14.90
    assert minimum["rounding_adjustment"] == 0.01
    assert minimum["amount"] == 14.91
    assert _sum(row["rounded_term"] for row in terms) == Decimal("53.94")
    assert _sum(row["amount"] for row in terms) == _decimal(result["score"]) == Decimal("53.95")


def _assert_effect_ledger(plan):
    result = receipt(plan)
    _, codes, names, width, base, _, _, vectors = scoring._prepared()
    final = scoring._state(scoring._keys(plan))[1]
    displayed = {(name, code): _decimal(round(base[di * width + ki], 2))
                 for di, name in enumerate(names) for ki, code in enumerate(codes)}
    moves = {move["measure"]: move for move in result["decisions"]}
    for mid in result["application_order"]:
        move = moves[mid]
        vector = vectors[mid, move["district"]]
        assert len(move["effects"]) == len(vector)
        for row, (cell, effect) in zip(move["effects"], vector):
            assert (row["district"], row["indicator"]) == (names[cell // width], codes[cell % width])
            assert row["rounded_term"] == round(effect, 2)
            assert row["applied_value"] == scoring.diff2(row["after"], row["before"])
            key = row["district"], row["indicator"]
            assert displayed[key] == _decimal(row["before"])
            displayed[key] += _decimal(row["applied_value"])
            assert displayed[key] == _decimal(row["after"])
    for row in result["synergy_lines"]:
        key = row["district"], row["indicator"]
        assert displayed[key] == _decimal(row["before"])
        displayed[key] += _decimal(row["applied_value"])
        assert displayed[key] == _decimal(row["after"])
    for row in result["clip_notes"]:
        key = row["district"], row["indicator"]
        displayed[key] += _decimal(row["adjustment"])
        assert displayed[key] == _decimal(row["after"])
    for di, name in enumerate(names):
        for ki, code in enumerate(codes):
            assert displayed[name, code] == _decimal(round(final[di * width + ki], 2))


@pytest.mark.parametrize("plan", [
    _doc(), _plan("M2", "M3", "M8", "M9", "M14"),
    _plan("M9", "M11", "M10", "M4", "M12"),
])
def test_effects_reconstruct_every_final_cell_exactly(plan):
    _assert_effect_ledger(plan)


def test_lag_city_targets_and_rounded_share_do_not_change_applied_effect():
    result = receipt(_doc())
    moves = {row["measure"]: row for row in result["decisions"]}
    school = moves["M7"]["effects"][0]
    assert school["full_effect"] == 16
    assert school["lag"] == 3
    assert school["realized_share"] == 0.62
    assert school["share_formula"] == "(8.00 − 3.00) / 8.00"
    assert school["applied_value"] == 10
    city = moves["M12"]["effects"]
    assert {row["district"] for row in city} == {d["name"] for d in load_dataset()["districts"]}
    assert all(row["rounded_term"] == 4.38 for row in city)
    assert result["clip_notes"] == []


@pytest.mark.parametrize("plan,pair,code,district", [
    (_plan("M1", "M2", "M9", "M10", "M14", district="Алматы"), ["M1", "M2"], "T1", "Алматы"),
    (_doc(), ["M10", "M12"], "B1", "Нура"),
    (_plan("M5", "M6", "M9", "M10", "M14", district="Сарыарка"), ["M5", "M6"], "E2", "Сарыарка"),
])
def test_fixed_synergies_are_counted_once_in_the_correct_district(plan, pair, code, district):
    result = receipt(plan)
    assert len(result["synergy_lines"]) == 1
    row = result["synergy_lines"][0]
    assert (row["pair"], row["indicator"], row["district"]) == (pair, code, district)
    assert row["full_bonus"] == row["applied_value"] == 2
    assert sum(len(move["synergy_lines"]) for move in result["decisions"]) == 1
    _assert_effect_ledger(plan)


def test_each_critical_cell_has_a_separate_negative_line_and_threshold_is_strict():
    plan = _plan("M9", "M11", "M10", "M4", "M12")
    plan["decisions"][1]["district"] = "Алматы"
    result = receipt(plan)
    official = scoring.evaluate(plan)
    assert result["n_crit"] == len(result["critical_penalties"]) == 2
    assert [{k: row[k] for k in ("district", "indicator", "value")}
            for row in result["critical_penalties"]] == official["crit_cells"]
    assert all(row["amount"] == -1 for row in result["critical_penalties"])
    assert any(row["value"] == 38.25 and row["indicator"] == "T1" for row in result["critical_penalties"])
    assert not any(row["value"] == row["threshold"] for row in result["critical_penalties"])
    assert _sum(row["amount"] for row in result["lines"] if row["additive"]) == _decimal(result["score"])


def test_clip_notes_follow_one_final_clip_not_per_measure_clipping(monkeypatch):
    prepared = scoring._prepared()
    data, codes, names, width, base, weights, pops, vectors = prepared
    base = list(base)
    base[names.index("Алматы") * width + codes.index("T1")] = 98
    base[names.index("Нура") * width + codes.index("S2")] = 99
    base[names.index("Есиль") * width + codes.index("C2")] = -5
    monkeypatch.setattr(scoring, "_prepared", lambda: (data, codes, names, width, tuple(base), weights, pops, vectors))
    plan = _plan("M2", "M11", "M8", "M9", "M14")
    plan["decisions"][1]["district"] = "Алматы"
    result = receipt(plan)
    notes = {(row["district"], row["indicator"]): row for row in result["clip_notes"]}
    assert notes["Нура", "S2"]["after"] == 100
    assert notes["Есиль", "C2"]["after"] == 0
    assert ("Алматы", "T1") not in notes
    almaty = next(row for row in result["districts"] if row["district"] == "Алматы")
    assert next(row for row in almaty["cells"] if row["indicator"] == "T1")["after"] == 99.25
    assert any(move["clip_notes"] for move in result["decisions"])
    _assert_effect_ledger(plan)


def test_json_rounding_input_forms_order_and_no_mutation():
    plan = _doc()
    before = deepcopy(plan)
    dataset = deepcopy(load_dataset())
    result = receipt(plan)
    assert result == receipt(plan["decisions"])
    reversed_result = receipt(list(reversed(plan["decisions"])))
    assert reversed_result["lines"] == result["lines"]
    assert reversed_result["decisions"] == list(reversed(result["decisions"]))
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result

    def check(value):
        if isinstance(value, float):
            assert value == round(value, 2)
        elif isinstance(value, dict):
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    check(result)
    assert plan == before
    assert load_dataset() == dataset
    result["decisions"][0]["effects"][0]["applied_value"] = -999
    assert receipt(plan)["decisions"][0]["effects"][0]["applied_value"] == 10


@pytest.mark.parametrize("function", [receipt, receipt_markdown])
def test_invalid_plan_uses_engine_russian_reason(function):
    with pytest.raises(ValueError, match="Нужно ровно 5 решений"):
        function([])


def test_markdown_has_checkable_rows_and_russian_labels():
    plan = _doc()
    text = receipt_markdown(plan)
    assert text == receipt_markdown(plan["decisions"])
    assert "Итого Score" in text and "56.54" in text
    assert "D_avg" in text and "Минимальный индекс" in text
    assert "Доля населения" in text and "Полный эффект" in text
    assert "Бонус синергии M10 + M12" in text
    assert "в Нуре" in text and "в Сарыарке" in text
    assert "Поправка округления" in text
    assert "Ограничение не изменило значения" in text
    # Sum the rendered score table, excluding the final total row.
    table = text.split("## Строки итогового счёта\n", 1)[1].split("## Применённые эффекты", 1)[0]
    rows = [line.split("|") for line in table.splitlines() if line.startswith("| ")]
    amounts = [Decimal(row[3].strip()) for row in rows[1:] if row[1].strip() != "Итого Score"]
    assert sum(amounts) == Decimal("56.54")
