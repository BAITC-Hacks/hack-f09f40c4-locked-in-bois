"""Календарь обещаний: красные ячейки и ожидание по кварталам.

``calendar(plan, base_values=None)`` accepts the same plans and optional baseline
as evaluate. All threshold decisions use the full-precision score kernel.
Timeline includes q=0; waiting_quarter_cells counts red observations at q=1..H,
without population weighting. This is a discrete indicator-quarter count, not
person-hours or an integral of the interpolated trajectory.

Cells includes every cell red at least once, including plan-induced problems.
first_cleared_q is its first non-red observation after becoming red; cleared_q
is the first quarter after its LAST red observation, so clearance persists to H.
None means no such clearance within the horizon, not a forecast beyond it.
thin_margin_cells contains previously red cells ending at threshold <= value
< threshold + 1, including a zero safety margin exactly on the red line.
"""

from .model import load_dataset
from .score import _keys, _rounded, _state, diff2
from .validate import validate


def calendar(plan, base_values=None) -> dict:
    """Return a Russian-labelled calendar without enumeration or disk writes."""
    ok, reason = validate(plan)
    if not ok:
        raise ValueError(reason)
    data = load_dataset()
    horizon = data["horizon_quarters"]
    threshold = data["crit_threshold"]
    indicators = data["indicators"]
    width = len(indicators)
    keys = _keys(plan)
    states = [_state(keys, base_values, q=q) for q in range(horizon + 1)]
    timeline = []
    for q, (score, values, _, crits, _) in enumerate(states):
        critical = [
            {"district": district["name"], "indicator": indicator["code"],
             "value": values[di * width + ki]}
            for di, district in enumerate(data["districts"])
            for ki, indicator in enumerate(indicators)
            if values[di * width + ki] < threshold
        ]
        timeline.append({"q": q, "label": "Исходное состояние" if q == 0 else f"Квартал {q}",
                         "score": score, "n_crit": sum(crits), "crit_cells": critical})

    cells, thin_margin_cells, districts = [], [], {}
    for di, district in enumerate(data["districts"]):
        name = district["name"]
        counts = [state[3][di] for state in states]
        districts[name] = {
            "label": f"Накопленное время в красной зоне в {district['cases']['loc']}",
            "waiting_quarter_cells": sum(counts[1:]),
            "red_counts": counts,
            "n_crit_before": counts[0], "n_crit": counts[-1],
        }
        for ki, indicator in enumerate(indicators):
            values = [state[1][di * width + ki] for state in states]
            red_quarters = [q for q, value in enumerate(values) if value < threshold]
            if not red_quarters:
                continue
            first_red = red_quarters[0]
            first_cleared = next((q for q in range(first_red + 1, horizon + 1)
                                  if values[q] >= threshold), None)
            cleared = red_quarters[-1] + 1 if red_quarters[-1] < horizon else None
            thin = cleared is not None and values[-1] < threshold + 1
            cell = {
                "district": name, "indicator": indicator["code"],
                "label": f"{indicator['name']} в {district['cases']['loc']}",
                "before": values[0], "after": values[-1],
                "delta": diff2(values[-1], values[0]),
                "margin": diff2(values[-1], threshold),
                "values": values, "red_quarters": red_quarters,
                "first_red_q": first_red, "first_cleared_q": first_cleared,
                "cleared_q": cleared,
                "clearance_label": (f"Выход из красной зоны: квартал {cleared}"
                                    if cleared is not None else "Выход из красной зоны не достигнут к концу плана"),
                "waiting_quarter_cells": sum(q > 0 for q in red_quarters),
                "thin_margin": thin,
            }
            cells.append(cell)
            if thin:
                thin_margin_cells.append({
                    "district": name, "indicator": indicator["code"],
                    "label": cell["label"], "value": values[-1],
                    "margin": cell["margin"], "cleared_q": cleared,
                })

    return _rounded({
        "title": "Календарь обещаний", "threshold": threshold,
        "horizon_quarters": horizon, "score": states[-1][0],
        "timeline": timeline, "cells": cells, "districts": districts,
        "n_crit": timeline[-1]["n_crit"], "crit_cells": timeline[-1]["crit_cells"],
        "waiting_quarter_cells": sum(d["waiting_quarter_cells"] for d in districts.values()),
        "waiting_quarters": list(range(1, horizon + 1)),
        "waiting_label": "Накопленное время в красной зоне",
        "waiting_unit": "ячейка·квартал",
        "waiting_note": (f"Сумма красных ячеек на отметках кварталов 1–{horizon}; "
                         "исходное состояние не учитывается. Без взвешивания по населению."),
        "thin_margin_cells": thin_margin_cells,
        "thin_margin_label": "Выход из красной зоны с запасом менее одного балла",
    })
