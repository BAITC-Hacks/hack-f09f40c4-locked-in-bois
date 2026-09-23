"""Calendar semantics, official timeline agreement and threshold boundaries."""

import copy
import json
from time import perf_counter

import pytest

from engine.calendar import calendar
from engine.model import load_dataset, measures_by_id
from engine.score import diff2, evaluate
from engine.shock import load_events, shocked_base
from engine.validate import validate


def _plan(*ids, district="Нура"):
    return {"decisions": [
        {"measure": mid, "district": district if measures_by_id()[mid]["type"] == "district" else None}
        for mid in ids
    ]}


def _doc():
    return {"decisions": copy.deepcopy(load_dataset()["reference"]["doc_example"]["decisions"])}


def _cell(result, district, code):
    return next(c for c in result["cells"] if (c["district"], c["indicator"]) == (district, code))


@pytest.mark.parametrize("plan", [
    _doc(), _plan("M2", "M3", "M8", "M9", "M14"),
    _plan("M9", "M11", "M10", "M4", "M12"),
    _plan("M9", "M11", "M10", "M4", "M12", district="Алматы"),
])
@pytest.mark.parametrize("event_index", [None, 0, 1, 2])
def test_consistency_with_evaluate(plan, event_index):
    base = None if event_index is None else shocked_base(load_events()[event_index])
    result, official = calendar(plan, base), evaluate(plan, base)
    assert [{"q": row["q"], "score": row["score"]} for row in result["timeline"]] == official["timeline"]
    assert result["crit_cells"] == result["timeline"][-1]["crit_cells"] == official["crit_cells"]
    assert result["score"] == official["score"]
    assert result["n_crit"] == official["n_crit"]
    assert result["timeline"][0]["n_crit"] == official["n_crit_before"]
    assert result["waiting_quarter_cells"] == sum(r["n_crit"] for r in result["timeline"][1:])
    assert result["waiting_quarter_cells"] == sum(c["waiting_quarter_cells"] for c in result["cells"])
    for name, district in result["districts"].items():
        assert district["red_counts"] == [sum(c["district"] == name for c in q["crit_cells"])
                                           for q in result["timeline"]]
        assert district["waiting_quarter_cells"] == sum(district["red_counts"][1:])


@pytest.mark.parametrize("plan,score,clear_s1,clear_s2,waiting,thin", [
    (_doc(), 56.54, 4, 6, 8, []),
    (_plan("M2", "M3", "M8", "M9", "M14"), 57.24, 7, 5, 10, [("Нура", "S1", 40.62, 0.62)]),
])
def test_golden_calendars(plan, score, clear_s1, clear_s2, waiting, thin):
    result = calendar(plan)
    assert result["score"] == score
    assert _cell(result, "Нура", "S1")["cleared_q"] == clear_s1
    assert _cell(result, "Нура", "S2")["cleared_q"] == clear_s2
    assert result["waiting_quarter_cells"] == result["districts"]["Нура"]["waiting_quarter_cells"] == waiting
    assert all(d["waiting_quarter_cells"] == 0 for name, d in result["districts"].items() if name != "Нура")
    assert [(c["district"], c["indicator"], c["value"], c["margin"])
            for c in result["thin_margin_cells"]] == thin
    assert result["crit_cells"] == []
    assert result["waiting_quarters"] == list(range(1, 9))


def test_new_red_cells_and_never_cleared():
    result = calendar(_plan("M9", "M11", "M10", "M4", "M12", district="Алматы"))
    trap = _cell(result, "Алматы", "T1")
    assert trap["before"] == 40
    assert trap["after"] == 38.25
    assert trap["red_quarters"] == list(range(2, 9))
    assert trap["cleared_q"] is trap["first_cleared_q"] is None
    assert trap["waiting_quarter_cells"] == 7
    assert result["districts"]["Нура"]["waiting_quarter_cells"] == 16
    assert result["waiting_quarter_cells"] == 23
    assert result["thin_margin_cells"] == []


def test_temporary_red_cell_clears_exactly_at_threshold():
    result = calendar(_plan("M2", "M11", "M9", "M10", "M12", district="Алматы"))
    trap = _cell(result, "Алматы", "T1")
    assert trap["values"][:4] == [40, 40, 39.75, 40]
    assert trap["red_quarters"] == [2]
    assert trap["cleared_q"] == trap["first_cleared_q"] == 3
    assert trap["waiting_quarter_cells"] == 1


def test_lag_and_synergy_activation_use_official_kernel():
    # Both lags are one: the fixed synergy starts at q=2, not q=1.
    result = calendar(_doc(), {"Нура": {"B1": 37}})
    cell = _cell(result, "Нура", "B1")
    assert cell["values"][:4] == [37, 37, 40.5, 42]
    assert cell["cleared_q"] == 2
    assert _cell(result, "Нура", "S1")["values"][:5] == [38, 38, 38, 38, 40]


@pytest.mark.parametrize("final,cleared,thin", [
    (39.999, False, False), (40, True, True), (40.999, True, True), (41, True, False),
])
def test_full_precision_red_and_thin_boundaries(final, cleared, thin):
    # M7 adds exactly ten points by q=8; classifications precede rounding.
    result = calendar(_doc(), {"Нура": {"S1": final - 10}})
    cell = _cell(result, "Нура", "S1")
    assert (cell["cleared_q"] is not None) is cleared
    assert cell["thin_margin"] is thin
    assert any(c["indicator"] == "S1" for c in result["thin_margin_cells"]) is thin
    assert cell["after"] == round(final, 2)
    assert cell["margin"] == diff2(final, result["threshold"])
    assert cell["delta"] == diff2(final, final - 10)


def test_no_red_cells_means_no_clearances_or_thin_flags():
    data = load_dataset()
    base = {d["name"]: {i["code"]: 80 for i in data["indicators"]} for d in data["districts"]}
    result = calendar(_doc(), base)
    assert result["cells"] == result["thin_margin_cells"] == result["crit_cells"] == []
    assert result["waiting_quarter_cells"] == 0


def test_clearance_is_sustained_to_horizon(monkeypatch):
    # Exercise a relapse explicitly so a future nonmonotone measure cannot
    # turn a temporary crossing into a promise of permanent clearance.
    import importlib
    module = importlib.import_module("engine.calendar")
    original = module._state
    series = [39, 40, 39, 40, 40, 40, 40, 40, 40]
    def state(keys, base_values=None, q=None):
        score, values, ds, crits, avg = original(keys, base_values, q)
        crits[0] += int(series[q] < 40) - int(values[0] < 40)
        values[0] = series[q]
        return score, values, ds, crits, avg
    monkeypatch.setattr(module, "_state", state)
    cell = _cell(calendar(_doc()), "Есиль", "T1")
    assert cell["first_cleared_q"] == 1
    assert cell["cleared_q"] == 3
    assert cell["red_quarters"] == [0, 2]
    series[-1] = 39
    assert _cell(calendar(_doc()), "Есиль", "T1")["cleared_q"] is None


@pytest.mark.parametrize("plan", [None, {}, [], [None] * 5, _plan("M7", "M7", "M10", "M12", "M5")])
def test_invalid_plan_reuses_russian_validator(plan):
    ok, reason = validate(plan)
    assert not ok
    with pytest.raises(ValueError) as exc:
        calendar(plan)
    assert str(exc.value) == reason


def test_input_forms_purity_json_rounding_and_russian_labels():
    plan = _doc()
    base = {"Нура": {"S1": 37.1234}}
    original = copy.deepcopy((plan, base, load_dataset()))
    result = calendar(plan, base)
    assert calendar(plan["decisions"], base) == result
    assert calendar(list(reversed(plan["decisions"])), base) == result
    assert (plan, base, load_dataset()) == original
    assert json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False)) == result
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
    assert result["title"] == "Календарь обещаний"
    assert result["districts"]["Нура"]["label"] == "Накопленное время в красной зоне в Нуре"
    result["cells"][0]["values"][0] = -999
    assert calendar(plan, base)["cells"][0]["values"][0] != -999


def test_warm_request_latency():
    plan = _doc()
    calendar(plan)
    start = perf_counter()
    calendar(plan)
    assert perf_counter() - start < 1.5
