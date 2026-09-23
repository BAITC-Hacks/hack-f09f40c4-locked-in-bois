"""Auditable Russian receipts for the official Score; no separate score formula.

Only ``lines`` with ``additive=True`` enter the total. Subtotals are informational.
Contributions telescope via diff2(full_precision_prefix, previous_prefix), so
their decimal sums equal the displayed totals. ``rounding_adjustment`` exposes
the difference from independently rounding a term, rather than hiding a cent.
Effect rows use the same convention for each cell, in the kernel's key order.
"""

from . import score as scoring
from .model import measures_by_id, normalize_plan, plan_cost
from .validate import validate


def _term(value, before, after):
    amount = scoring.diff2(after, before)
    return {"amount": amount, "rounded_term": round(value, 2),
            "rounding_adjustment": scoring.diff2(amount, value)}


def receipt(plan) -> dict:
    """Return a validated, JSON-ready receipt, with all numeric values at 2dp.

    ``decisions`` preserves input order; ``application_order`` records the
    kernel order used by effect before/after rows. Synergies appear once under
    their district-owning decision. Clip notes describe the final cell, never
    a hypothetical clipping of an individual measure. Ratios are rounded for
    display; ``share_formula`` preserves the exact fraction for hand checking.
    """
    ok, reason = validate(plan)
    if not ok:
        raise ValueError(reason)
    decisions = normalize_plan(plan)
    keys = scoring._keys(decisions)
    data, codes, names, width, base, weights, pops, vectors = scoring._prepared()
    score, values, ds, crits, avg = scoring._state(keys)
    measures = measures_by_id()
    horizon = data["horizon_quarters"]
    sw = data["score_weights"]
    districts = {d["name"]: d for d in data["districts"]}
    indicators = {i["code"]: i for i in data["indicators"]}
    lines, district_rows = [], []
    avg_prefix = 0.0
    for di, name in enumerate(names):
        previous = avg_prefix
        avg_prefix += pops[di] * ds[di]
        term = _term(sw["avg"] * pops[di] * ds[di],
                     sw["avg"] * previous, sw["avg"] * avg_prefix)
        row = {"kind": "district", "label": f"Вклад района {districts[name]['cases']['gen']}",
               "district": name, "D_after": ds[di], "pop": pops[di],
               "avg_contribution": scoring.diff2(avg_prefix, previous),
               "formula": f"{sw['avg']:.2f} × pop × D_after",
               "additive": True, **term}
        lines.append(row)
        cells, prefix = [], 0.0
        for ki, code in enumerate(codes):
            cell = di * width + ki
            old = prefix
            prefix += weights[ki] * values[cell]
            cells.append({"indicator": code, "label": indicators[code]["name"],
                          "before": base[cell], "after": values[cell], "weight": weights[ki],
                          **_term(weights[ki] * values[cell], old, prefix)})
        district_rows.append({**row, "cells": cells})
    lines.append({"kind": "average", "label": "Средний индекс города D_avg = Σ pop × D_after",
                  "value": avg, "amount": sw["avg"] * avg, "additive": False})
    minimum = min(ds)
    weakest = names[ds.index(minimum)]
    before_penalties = sw["avg"] * avg + sw["min"] * minimum
    lines.append({"kind": "minimum", "label": f"Минимальный индекс — у {districts[weakest]['cases']['gen']}",
                  "district": weakest, "D_after": minimum,
                  "formula": f"{sw['min']:.2f} × min(D_after)", "additive": True,
                  **_term(sw["min"] * minimum, sw["avg"] * avg, before_penalties)})
    penalties = []
    for di, name in enumerate(names):
        for ki, code in enumerate(codes):
            value = values[di * width + ki]
            if value < data["crit_threshold"]:
                count = len(penalties)
                before = before_penalties - sw["crit_penalty"] * count
                after = before_penalties - sw["crit_penalty"] * (count + 1)
                penalties.append({"kind": "critical", "label": f"Штраф: {code} в {districts[name]['cases']['loc']}",
                                  "district": name, "indicator": code, "value": value,
                                  "threshold": data["crit_threshold"], "additive": True,
                                  **_term(-sw["crit_penalty"], before, after)})
    lines.extend(penalties)
    lines.append({"kind": "total", "label": "Итого Score", "amount": score, "additive": False})

    # Reuse the exact prepared effects, replaying their accumulation only to
    # explain it. All final values, district indices and Score come from _state.
    running = list(base)
    moves, touched = {}, {}

    def effect_line(cell, effect):
        di, ki = divmod(cell, width)
        before = running[cell]
        running[cell] += effect
        term = _term(effect, before, running[cell])
        return {"district": names[di], "indicator": codes[ki],
                "label": indicators[codes[ki]]["name"],
                "before": before, "after": running[cell],
                "applied_value": term.pop("amount"), **term}

    for mid, target in keys:
        m = measures[mid]
        place = "по всему городу" if target is None else f"в {districts[target]['cases']['loc']}"
        move = {"measure": mid, "district": target, "label": f"{mid} «{m['name']}» {place}",
                "lag": m["lag"], "realized_share": (horizon - m["lag"]) / horizon,
                "share_formula": f"({horizon:.2f} − {m['lag']:.2f}) / {horizon:.2f}",
                "effects": [], "synergy_lines": [], "clip_notes": []}
        touched[mid] = set()
        for cell, effect in vectors[mid, target]:
            row = effect_line(cell, effect)
            row.update(full_effect=m["effects"][row["indicator"]], lag=move["lag"],
                       realized_share=move["realized_share"], share_formula=move["share_formula"])
            move["effects"].append(row)
            touched[mid].add(cell)
        moves[mid] = move
    locations = dict(keys)
    synergy_lines = []
    for synergy in data["synergies"]:
        if all(mid in locations for mid in synergy["pair"]):
            owner = synergy["district_of"]
            offset = names.index(locations[owner]) * width
            for code, bonus in synergy["bonus"].items():
                cell = offset + codes.index(code)
                row = effect_line(cell, bonus)
                row.update(pair=list(synergy["pair"]), full_bonus=bonus,
                           label=f"Бонус синергии {' + '.join(synergy['pair'])}: {code}",
                           note="Фиксированный бонус без уменьшения на лаг; учтён один раз.")
                synergy_lines.append(row)
                moves[owner]["synergy_lines"].append(row)
                for mid in synergy["pair"]:
                    touched[mid].add(cell)
    clip_notes = []
    for cell, (raw, final) in enumerate(zip(running, values)):
        if raw != final:
            di, ki = divmod(cell, width)
            note = {"district": names[di], "indicator": codes[ki], "before": raw, "after": final,
                    "adjustment": scoring.diff2(final, raw),
                    "label": "Ограничение после суммы всех эффектов и бонусов"}
            clip_notes.append(note)
            for mid in moves:
                if cell in touched[mid]:
                    moves[mid]["clip_notes"].append(note)
    return scoring._rounded({
        "label": "Чек расчёта Score", "score": score, "cost": plan_cost(decisions),
        "horizon_quarters": horizon, "score_weights": dict(sw),
        "d_avg": avg, "d_min": minimum, "d_min_district": weakest, "n_crit": sum(crits),
        "lines": lines, "districts": district_rows, "critical_penalties": penalties,
        "decisions": [moves[d["measure"]] for d in decisions],
        "application_order": [mid for mid, _ in keys],
        "synergy_lines": synergy_lines, "clip_notes": clip_notes,
        "rounding_note": "Строки — разности округлённых накопленных итогов (diff2). "
                         "Поправка округления уже включена в строку; повторно её не прибавляйте. "
                         "Произведения и доли вычисляются до округления.",
        "clip_note": "Ограничение применяется один раз после всех эффектов и бонусов. "
                     + ("Изменённые ячейки перечислены ниже." if clip_notes else "Ограничение не изменило значения."),
    })


def receipt_markdown(plan) -> str:
    """Render the same receipt as a Russian Markdown document."""
    result = receipt(plan)
    out = [f"# {result['label']}", "", f"Стоимость плана: {result['cost']:.2f}.", "",
           result["rounding_note"], "", "## Районы и средний индекс", "",
           "| Район | D_after | Доля населения | pop × D_after | Вклад в Score | Поправка округления |",
           "|---|---:|---:|---:|---:|---:|"]
    for row in result["districts"]:
        out.append(f"| {row['district']} | {row['D_after']:.2f} | {row['pop']:.2f} | "
                   f"{row['avg_contribution']:.2f} | {row['amount']:.2f} | {row['rounding_adjustment']:+.2f} |")
    out.extend(["", f"D_avg = Σ pop × D_after = **{result['d_avg']:.2f}**.", "",
                "## Строки итогового счёта", "",
                "| Строка | Расчёт | Баллы | Поправка округления |", "|---|---|---:|---:|"])
    for row in result["lines"]:
        if row["kind"] == "average":
            continue
        formula = row.get("formula", "")
        if row["kind"] == "minimum":
            formula += f"; min(D_after) = {row['D_after']:.2f}"
        if row["kind"] == "critical":
            formula = f"{row['value']:.2f} < {row['threshold']:.2f}"
        out.append(f"| {row['label']} | {formula} | {row['amount']:.2f} | "
                   f"{row.get('rounding_adjustment', 0):+.2f} |")
    if not result["critical_penalties"]:
        out.extend(["", "Критических ячеек нет; штрафы отсутствуют."])
    out.extend(["", "## Применённые эффекты", "",
                "Порядок накопления в движке: " + " → ".join(result["application_order"]) + ".",
                "Значения «до» и «после» в эффектах показаны до итогового ограничения."])
    for move in result["decisions"]:
        out.extend(["", f"### {move['label']}", "",
                    "| Район | Показатель | Полный эффект | Лаг | Реализованная доля | Применено | До | После | Поправка округления |",
                    "|---|---|---:|---:|---|---:|---:|---:|---:|"])
        for row in move["effects"]:
            out.append(f"| {row['district']} | {row['indicator']} — {row['label']} | {row['full_effect']:+.2f} | "
                       f"{row['lag']:.2f} | {row['share_formula']} ≈ {row['realized_share']:.2f} | "
                       f"{row['applied_value']:+.2f} | {row['before']:.2f} | {row['after']:.2f} | "
                       f"{row['rounding_adjustment']:+.2f} |")
        for row in move["synergy_lines"]:
            out.extend(["", f"{row['label']}, {row['district']}: {row['before']:.2f} "
                        f"+ {row['applied_value']:.2f} = {row['after']:.2f}. {row['note']}"])
        for note in move["clip_notes"]:
            out.extend(["", f"{note['label']}: {note['district']}, {note['indicator']}, "
                        f"{note['before']:.2f} → {note['after']:.2f} "
                        f"(поправка {note['adjustment']:+.2f})."])
    out.extend(["", result["clip_note"], "", "## Проверка районных индексов", "",
                "| Район | Показатель | Значение после ограничений | Вес | Вклад в D_after | Поправка округления |",
                "|---|---|---:|---:|---:|---:|"])
    for district in result["districts"]:
        for cell in district["cells"]:
            out.append(f"| {district['district']} | {cell['indicator']} — {cell['label']} | "
                       f"{cell['after']:.2f} | {cell['weight']:.2f} | {cell['amount']:.2f} | "
                       f"{cell['rounding_adjustment']:+.2f} |")
    return "\n".join(out) + "\n"
