"""Ordered validation with specific Russian explanations."""

from .model import district_names, load_dataset, measures_by_id, normalize_plan


def validate(plan) -> tuple[bool, str | None]:
    data = load_dataset()
    raw = plan.get("decisions") if isinstance(plan, dict) else plan
    if not isinstance(raw, list) or len(raw) != data["decisions_required"]:
        return False, f"Нужно ровно {data['decisions_required']} решений"
    try:
        decisions = normalize_plan(plan)
    except ValueError as exc:
        return False, str(exc)
    ids = [d["measure"] for d in decisions]
    for i, mid in enumerate(ids):
        if mid in ids[:i]:
            return False, f"Повтор меры: {mid}"
    measures = measures_by_id()
    names = district_names()
    for d in decisions:
        if not isinstance(d["measure"], str) or d["measure"] not in measures:
            return False, f"Неизвестная мера: {d['measure']}"
        if d["district"] is not None and d["district"] not in names:
            return False, f"Неизвестный район: {d['district']}"
    cost = sum(measures[mid]["cost"] for mid in ids)
    if cost > data["budget"]:
        return False, f"Превышен бюджет: {cost} > {data['budget']}"
    for d in decisions:
        kind = measures[d["measure"]]["type"]
        if kind == "district" and d["district"] is None:
            return False, f"Для районной меры {d['measure']} необходимо указать район"
        if kind == "city" and d["district"] is not None:
            return False, f"Для городской меры {d['measure']} район не указывается"
    for direction in data["directions"]:
        chosen = [mid for mid in ids if measures[mid]["direction"] == direction["id"]]
        if len(chosen) > data["max_per_direction"]:
            return False, (f"Больше {data['max_per_direction']} мер в направлении "
                           f"«{direction['name']}»: {', '.join(chosen)}")
    locations = {d["measure"]: d["district"] for d in decisions}
    for rule in data["incompatibilities"]:
        a, b = rule["pair"]
        if a in locations and b in locations:
            if rule["scope"] == "anywhere":
                return False, rule["reason"]
            if locations[a] == locations[b]:
                return False, f"{rule['reason']} Район: {locations[a]}"
    return True, None
