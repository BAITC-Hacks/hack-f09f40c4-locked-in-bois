"""Chess-style review with exact, order-dependent reachable ceilings.

Build the small exhaustive first-option index with ``python -m engine.grading``.
Every record has a legal maximizing witness; the top-200 optimizer cache alone
cannot certify ceilings for all options. Runtime enumerates only the remaining
measure combinations/locations for prefixes of length >= 2, never whole games.

Loss is the displayed Score regret of a single replacement, holding four moves
fixed. Inclusive thresholds (Score points): 0 best, .10 excellent, .25 good,
.50 inaccuracy, 1.00 mistake, above 1.00 blunder. These distinguish small losses
within the engine's narrow attainable improvement range from a critical-cell
penalty. A sacrifice retains its numerical loss and does not inflate accuracy.

Accuracy = 100 * R / (R + sum(loss)), R = max(.01, global ceiling - baseline),
using diff2 for the reference difference. It is monotone in total local regret,
100 at the global optimum (also at any single-swap local optimum). Local losses
overlap: their sum is NOT the gap to the global optimum. Eval-bar drops, in
contrast, telescope exactly to that gap and depend on decision order.

Sacrifice requires political survival in the current plan, failure in EVERY
full-precision Score-maximizing alternative (ties within numerical epsilon),
and no surviving replacement with a higher displayed Score. Otherwise, when
the Score-best replacements lose reelection, loss and best_alternative refer
to the best surviving replacement; score_best_alternative preserves the
unrestricted optimum. Among ties prefer survival, then greatest approval.
Approval's ``reelected`` flag uses the unrounded threshold comparison.
"""

import hashlib
import json
import time
from collections import Counter
from functools import lru_cache
from itertools import combinations, product

from .approval import approval
from .model import REPO_ROOT, district_names, load_dataset, measures_by_id, normalize_plan
from .optimize import _options, _plan, _valid_options
from .score import _keys, _state, diff2, quick_score
from .validate import validate

CACHE_PATH = REPO_ROOT / "data/grading_cache.json"
CACHE_SCHEMA = 1
SCORE_EPSILON = 1e-10
GRADE_THRESHOLDS = ((0.0, "best"), (0.10, "excellent"), (0.25, "good"),
                    (0.50, "inaccuracy"), (1.00, "mistake"), (float("inf"), "blunder"))
GRADE_LABELS = {"best": ("!!", "Лучший ход"), "excellent": ("!", "Отличный ход"),
                "good": ("", "Хороший ход"), "inaccuracy": ("?!", "Неточность"),
                "mistake": ("?", "Ошибка"), "blunder": ("??", "Зевок"),
                "sacrifice": ("!?", "Жертва")}


def _fingerprint():
    digest = hashlib.sha256(json.dumps(load_dataset(), sort_keys=True,
                                      ensure_ascii=False).encode("utf-8"))
    for name in ("model.py", "score.py", "validate.py", "optimize.py"):
        # Git may normalize line endings when the cache is checked out elsewhere.
        digest.update((REPO_ROOT / "engine" / name).read_text(encoding="utf-8").encode("utf-8"))
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _load_cache():
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Нет кэша потолков: выполните python -m engine.grading") from exc
    if cache.get("schema") != CACHE_SCHEMA or cache.get("fingerprint") != _fingerprint():
        raise ValueError("Кэш потолков устарел: выполните python -m engine.grading")
    options = {(r["measure"], r["district"]): r for r in cache["options"]}
    if set(options) != set(_options()):
        raise ValueError("Неполный кэш потолков: выполните python -m engine.grading")
    return cache, options


def _decision(key):
    mid, district = key
    return {"measure": mid, "district": district, "name": measures_by_id()[mid]["name"]}


def _place(district):
    if district is None:
        return "по всему городу"
    cases = next(d["cases"] for d in load_dataset()["districts"] if d["name"] == district)
    return "в " + cases["loc"]


def _describe(key):
    return f"{key[0]} «{measures_by_id()[key[0]]['name']}» {_place(key[1])}"


def _synergy_gain(keys, synergy, state):
    """Cancel only this bonus before clipping, preserving the other effects."""
    base = {d["name"]: dict(d["values"]) for d in load_dataset()["districts"]}
    target = dict(keys)[synergy["district_of"]]
    for code, value in synergy["bonus"].items():
        base[target][code] -= value
    return diff2(state[0], _state(keys, base)[0])


def _best_candidate(candidates):
    """Rows are (score/candidate/key, approval); prefer survival in epsilon ties."""
    best = max(row[0] for row, _ in candidates)
    return max((pair for pair in candidates if best - pair[0][0] <= SCORE_EPSILON),
               key=lambda pair: (pair[1]["reelected"], pair[1]["city"]))


def _prefix_reason(decisions):
    """The full validator cannot validate a partial plan; mirror its constraints."""
    data, measures = load_dataset(), measures_by_id()
    if len(decisions) > data["decisions_required"]:
        return f"Допустимо не более {data['decisions_required']} решений"
    ids = [d["measure"] for d in decisions]
    for i, mid in enumerate(ids):
        if mid in ids[:i]:
            return f"Повтор меры: {mid}"
        if not isinstance(mid, str) or mid not in measures:
            return f"Неизвестная мера: {mid}"
    for d in decisions:
        mid, target = d["measure"], d["district"]
        if target is not None and target not in district_names():
            return f"Неизвестный район: {target}"
        if measures[mid]["type"] == "district" and target is None:
            return f"Для районной меры {mid} необходимо указать район"
        if measures[mid]["type"] == "city" and target is not None:
            return f"Для городской меры {mid} район не указывается"
    cost = sum(measures[mid]["cost"] for mid in ids)
    if cost > data["budget"]:
        return f"Превышен бюджет: {cost:.2f} > {data['budget']:.2f}"
    counts = Counter(measures[mid]["direction"] for mid in ids)
    for direction in data["directions"]:
        if counts[direction["id"]] > data["max_per_direction"]:
            return (f"Лимит направления «{direction['name']}»: "
                    f"допустимо не более {data['max_per_direction']} мер")
    locations = {d["measure"]: d["district"] for d in decisions}
    for rule in data["incompatibilities"]:
        a, b = rule["pair"]
        if a in locations and b in locations and (
                rule["scope"] == "anywhere" or locations[a] == locations[b]):
            return "Несовместимость: " + rule["reason"]
    return None


def _completion_keys(prefix, *, ignore_budget=False):
    """Exact enumeration, pruning measure sets before expanding their locations.

    Prefix keys must already pass _prefix_reason. The score kernel receives its
    canonical sorted order so floating-point accumulation matches quick_score.
    """
    data, measures = load_dataset(), measures_by_id()
    fixed = dict(prefix)
    remaining = [m for m in data["measures"] if m["id"] not in fixed]
    count = data["decisions_required"] - len(prefix)
    fixed_measures = [measures[mid] for mid in fixed]
    names = district_names()
    for added in combinations(remaining, count):
        selected = fixed_measures + list(added)
        cost = sum(m["cost"] for m in selected)
        if not ignore_budget and cost > data["budget"]:
            continue
        if max(Counter(m["direction"] for m in selected).values(), default=0) > data["max_per_direction"]:
            continue
        ids = {m["id"] for m in selected}
        rules = [r for r in data["incompatibilities"] if set(r["pair"]) <= ids]
        if any(r["scope"] == "anywhere" for r in rules):
            continue
        targets = [names if m["type"] == "district" else [None] for m in added]
        for locations in product(*targets):
            result = dict(fixed)
            result.update((m["id"], target) for m, target in zip(added, locations))
            if any(result[r["pair"][0]] == result[r["pair"][1]] for r in rules):
                continue
            yield tuple(sorted(result.items())), cost


@lru_cache(maxsize=512)
def _ceiling(keys):
    if len(keys) <= 1:
        cache, options = _load_cache()
        return cache["ceiling"] if not keys else options[keys[0]]["ceiling"]
    best = max((_state(candidate)[0] for candidate, _ in _completion_keys(keys)), default=None)
    return round(best, 2) if best is not None else None


def _no_completion_reason(keys):
    # An exact lower bound with all non-budget rules enforced: this explains
    # budget traps even when the prefix itself has not overspent yet.
    minimum = min((cost for _, cost in _completion_keys(keys, ignore_budget=True)), default=None)
    if minimum is not None:
        budget = load_dataset()["budget"]
        return (f"Бюджета не хватит для завершения: самый дешёвый совместимый план "
                f"стоит {minimum:.2f} при бюджете {budget:.2f}; "
                f"не хватает {diff2(minimum, budget):.2f}.")
    return ("Нет допустимого завершения: оставшиеся меры не позволяют одновременно "
            "соблюсти лимиты направлений и правила несовместимости без повторов.")


def prefix_ceiling(prefix) -> dict:
    """Review even an impossible partial plan; unlike grade, no full plan needed.

    Valid full plans cannot have dead-end prefixes: the plan itself witnesses a
    completion. Missing ceilings/drops are None, never a fictitious zero Score.
    """
    decisions = normalize_plan(prefix)
    reason = _prefix_reason(decisions)
    keys = _keys(decisions) if reason is None else ()
    ceiling = _ceiling(keys) if reason is None else None
    result = {"step": len(decisions), "ceiling": ceiling, "dead_end": ceiling is None}
    if ceiling is None:
        result["reason"] = reason or _no_completion_reason(keys)
    return result


def _traps(decisions, index, state, without, candidates, current):
    data = load_dataset()
    measure = measures_by_id()[decisions[index]["measure"]]
    codes = [i["code"] for i in data["indicators"]]
    traps, comments = [], []
    for di, district in enumerate(data["districts"]):
        for ki, code in enumerate(codes):
            cell = di * len(codes) + ki
            before, after = without[1][cell], state[1][cell]
            if before >= data["crit_threshold"] > after:
                traps.append({"kind": "critical_cell", "district": district["name"],
                              "indicator": code, "before": round(before, 2), "after": round(after, 2)})
                comments.append(f"Новая критическая ячейка {code} в {district['cases']['loc']}: "
                                f"{before:.2f} → {after:.2f}, ниже {data['crit_threshold']:.2f}.")
            elif before < data["crit_threshold"] <= after:
                traps.append({"kind": "critical_rescue", "district": district["name"],
                              "indicator": code, "before": round(before, 2), "after": round(after, 2)})
                comments.append(f"Снят критический штраф {_place(district['name'])}: "
                                f"{code} растёт с {before:.2f} до {after:.2f}.")
    horizon = data["horizon_quarters"]
    share = max(0, horizon - measure["lag"]) / horizon
    if share <= 0.5:
        traps.append({"kind": "lag", "realized_percent": round(share * 100, 2)})
        comments.append(f"К концу горизонта реализовано лишь {share * 100:.2f}% эффекта "
                        f"из-за задержки {measure['lag']:.2f} квартала.")
    ids = {d["measure"] for d in decisions}
    for synergy in data["synergies"]:
        if measure["id"] in synergy["pair"] and set(synergy["pair"]) <= ids:
            gain = _synergy_gain(_keys(decisions), synergy, state)
            traps.append({"kind": "synergy", "pair": list(synergy["pair"]), "gain": gain})
            comments.append(f"Работает связка {' + '.join(synergy['pair'])}: "
                            f"сам бонус добавляет {gain:.2f} к Score.")
    for score, candidate, replacement in sorted(candidates, key=lambda row: row[0], reverse=True):
        if diff2(score, current) <= 0:
            break
        candidate_ids = {d["measure"] for d in candidate}
        missed = [s for s in data["synergies"]
                  if not set(s["pair"]) <= ids and set(s["pair"]) <= candidate_ids]
        if missed:
            synergy = missed[0]
            gain = diff2(score, current)
            traps.append({"kind": "missed_synergy", "pair": list(synergy["pair"]),
                          "replacement": _decision(replacement), "score": round(score, 2), "gain": gain})
            comments.append(f"Упущена доступная синергия {' + '.join(synergy['pair'])}: "
                            f"допустимая замена на {replacement[0]} даёт Score {score:.2f} "
                            f"(+{gain:.2f} за всю замену).")
            break
    return traps, comments


def grade(plan) -> dict:
    """Validate first, then return an independent, JSON-serializable review."""
    ok, reason = validate(plan)
    if not ok:
        raise ValueError(reason)
    decisions = normalize_plan(plan)
    state = _state(_keys(decisions))
    current = round(state[0], 2)
    political = approval(decisions)
    moves = []
    for index, decision in enumerate(decisions):
        candidates = []
        occupied = {d["measure"] for j, d in enumerate(decisions) if j != index}
        for key in _options():
            if key[0] in occupied:
                continue
            replacement = {"measure": key[0], "district": key[1]}
            candidate = decisions[:index] + [replacement] + decisions[index + 1:]
            if validate(candidate)[0]:
                candidates.append((quick_score(candidate), candidate, key))
        best = max(row[0] for row in candidates)
        loss = diff2(best, current)
        quality = next(label for threshold, label in GRADE_THRESHOLDS if loss <= threshold)
        alternative, alternative_approval = None, None
        score_alternative, score_approval = None, None
        survival_comparison = False
        if loss > 0:
            assessed = [(row, approval(row[1])) for row in candidates]
            winner, alternative_approval = _best_candidate(assessed)
            score_alternative = {**_decision(winner[2]), "score": round(winner[0], 2)}
            score_approval = alternative_approval["city"]
            if political["reelected"] and not alternative_approval["reelected"]:
                # The current move itself witnesses a surviving candidate.
                surviving = [pair for pair in assessed if pair[1]["reelected"]]
                best_surviving = max(row[0] for row, _ in surviving)
                if diff2(best_surviving, current) == 0:
                    quality = "sacrifice"
                else:
                    winner, alternative_approval = _best_candidate(surviving)
                    loss = diff2(best_surviving, current)
                    quality = next(label for threshold, label in GRADE_THRESHOLDS if loss <= threshold)
                    survival_comparison = True
            alternative = {**_decision(winner[2]), "score": round(winner[0], 2)}
        without = _state(_keys(decisions[:index] + decisions[index + 1:]))
        contribution = diff2(state[0], without[0])
        traps, trap_comments = _traps(decisions, index, state, without, candidates, current)
        comments = []
        if quality == "best":
            comments.append("Это уже лучший ход по Score при остальных фиксированных решениях.")
        else:
            comments.append(f"При остальных решениях сильнее {_describe(winner[2])}: "
                            f"Score {alternative['score']:.2f} вместо {current:.2f}; потеря — {loss:.2f}.")
            alternative_state = _state(_keys(winner[1]))
            if diff2(min(alternative_state[2]), min(state[2])) > 0:
                comments.append("Замена поднимает индекс самого слабого района "
                                f"с {min(state[2]):.2f} до {min(alternative_state[2]):.2f}.")
            elif diff2(alternative_state[4], state[4]) > 0:
                comments.append("Замена поднимает средний индекс города "
                                f"с {state[4]:.2f} до {alternative_state[4]:.2f}.")
            if sum(alternative_state[3]) < sum(state[3]):
                comments.append(f"Критических ячеек после замены — {sum(alternative_state[3])} "
                                f"вместо {sum(state[3])}.")
            if quality == "sacrifice":
                comments.append(f"Потеря сохраняет переизбрание: рейтинг {political['city']:.2f}, "
                                f"у замены {alternative_approval['city']:.2f}, "
                                f"порог {political['threshold']:.2f}; более сильной замены с переизбранием нет.")
            elif survival_comparison:
                comments.append(f"Замена сохраняет переизбрание: рейтинг {alternative_approval['city']:.2f}.")
        comments.extend(trap_comments)
        symbol, label = GRADE_LABELS[quality]
        moves.append({"index": index + 1, **_decision((decision["measure"], decision["district"])),
                      "grade": quality, "symbol": symbol, "label": label, "loss": loss,
                      "best_alternative": alternative, "comment": " ".join(comments), "traps": traps,
                      "contribution": contribution, "score_best_alternative": score_alternative,
                      "score_best_alternative_approval": score_approval,
                      "approval": political["city"],
                      "best_alternative_approval": alternative_approval["city"] if alternative_approval else None})
    global_ceiling = _ceiling(())
    bar = [{"step": 0, "ceiling": global_ceiling}]
    for step in range(1, len(decisions) + 1):
        entry = prefix_ceiling(decisions[:step])
        previous = bar[-1]["ceiling"]
        entry.update({"decision": _decision((decisions[step - 1]["measure"], decisions[step - 1]["district"])),
                      "drop": diff2(previous, entry["ceiling"])
                      if previous is not None and entry["ceiling"] is not None else None})
        bar.append(entry)
    total_loss = round(sum(m["loss"] for m in moves), 2)
    reference = max(0.01, diff2(global_ceiling, _state(())[0]))
    accuracy = round(100 * reference / (reference + total_loss), 2)
    # An excellent move or a political sacrifice should not be called a blunder.
    worst = max((m for m in moves if m["grade"] in {"inaccuracy", "mistake", "blunder"}),
                key=lambda move: move["loss"], default=None)
    costliest = max((m for m in moves if m["loss"] > 0 and m["grade"] != "sacrifice"),
                   key=lambda move: move["loss"], default=None)
    gap = diff2(global_ceiling, current)
    summary = (f"Score {current:.2f} из достижимых {global_ceiling:.2f}: "
               f"потеря потолка {gap:.2f}; точность {accuracy:.2f}%.")
    if gap and costliest:
        summary += (f" Наибольшая цена одиночного выбора — {costliest['loss']:.2f} у "
                    f"{costliest['measure']} {_place(costliest['district'])}.")
    elif not gap:
        summary += " Глобальный максимум сохранён на каждом шаге партии."
    return {"score": current, "accuracy": accuracy, "moves": moves, "eval_bar": bar,
            "biggest_blunder": dict(worst) if worst else None,
            "summary": summary}


def _build_cache():
    best_by_option = {key: (float("-inf"), None) for key in _options()}
    best_score, best_keys, total = float("-inf"), None, 0
    for keys, _ in _valid_options():
        score = _state(keys)[0]
        total += 1
        if score > best_score:
            best_score, best_keys = score, keys
        for option in keys:
            if score > best_by_option[option][0]:
                best_by_option[option] = score, keys
    rows = [{"measure": key[0], "district": key[1],
             "ceiling": round(score, 2) if witness is not None else None,
             "witness": _plan(witness) if witness is not None else None}
            for key, (score, witness) in best_by_option.items()]
    return {"schema": CACHE_SCHEMA, "fingerprint": _fingerprint(), "total_valid": total,
            "ceiling": round(best_score, 2), "witness": _plan(best_keys), "options": rows}


if __name__ == "__main__":
    start = time.perf_counter()
    cache = _build_cache()
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Elapsed: {time.perf_counter() - start:.2f}s; total_valid: {cache['total_valid']}; "
          f"ceiling: {cache['ceiling']:.2f}; cache bytes: {CACHE_PATH.stat().st_size}")
