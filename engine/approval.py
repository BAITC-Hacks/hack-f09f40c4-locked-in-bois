"""Illustrative political-risk layer; never changes the official score."""

from .model import normalize_plan, measures_by_id
from .score import _keys, _prepared, _rounded, _state, diff2
from .validate import validate

CONSTANTS = {"BASE": 50, "A": 4, "GOT": 10, "MISS": 12, "CRIT": 3, "THRESHOLD": 50}


def _approval_values(keys, state, before_ds):
    _, _, names, _, _, _, pops, _ = _prepared()
    c = CONSTANTS
    got = {target for _, target in keys if target is not None}
    values = [max(0, min(100, c["BASE"] + c["A"] * (d - before)
                         + (c["GOT"] if name in got else -c["MISS"]) - c["CRIT"] * crit))
              for name, d, before, crit in zip(names, state[2], before_ds, state[3])]
    return sum(p * value for p, value in zip(pops, values)), values


def approval(plan, base_values=None) -> dict:
    ok, reason = validate(plan)
    if not ok:
        raise ValueError(reason)
    decisions = normalize_plan(plan)
    keys = _keys(decisions)
    state = _state(keys, base_values)
    before_ds = _state((), base_values)[2]
    city, values = _approval_values(keys, state, before_ds)
    names = _prepared()[2]
    got = {d["district"] for d in decisions if measures_by_id()[d["measure"]]["type"] == "district"}
    return _rounded({"city": city, "threshold": CONSTANTS["THRESHOLD"],
                     "reelected": city >= CONSTANTS["THRESHOLD"],
                     "districts": {name: {"approval": values[i], "delta_D": diff2(state[2][i], before_ds[i]),
                                           "got_district_measure": name in got, "crit_cells": state[3][i]}
                                   for i, name in enumerate(names)}})
