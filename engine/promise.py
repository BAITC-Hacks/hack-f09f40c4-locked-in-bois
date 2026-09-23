"""Exact prices of promises over every legal plan.

Build the portable, losslessly compressed column table with
``python -m engine.promise``. Queries never enumerate or rescore the search
space. Numeric constraints use full precision (including D_after - D_before),
just like the optimizer; only public output is rounded. Equal scores retain
the optimizer's enumeration order. No political-risk terms enter Score.
"""

import hashlib
import json
import lzma
import math
import sys
import time
from array import array
from functools import lru_cache
from itertools import combinations

from .approval import CONSTANTS, _approval_values
from .model import REPO_ROOT, district_names, load_dataset, measures_by_id, plan_cost
from .optimize import _plan, _valid_options
from .score import _keys, _state, diff2
from .validate import validate

TABLE_PATH = REPO_ROOT / "data/promise_table.xz"
_VERSION = 2
_APPROVAL_SCALE = 1_000_000  # Lossless codec predictor, NOT calculation precision.
_FIELDS = {
    "include": ({"type", "measure"}, {"district"}),
    "exclude": ({"type", "measure"}, set()),
    "district_project": ({"type", "district"}, set()),
    "min_districts": ({"type", "value"}, set()),
    "max_cost": ({"type", "value"}, set()),
    "min_approval": ({"type", "value"}, set()),
    "no_critical": ({"type"}, set()),
    "min_district_delta": ({"type", "district", "value"}, set()),
}


def _checked(promise):
    if not isinstance(promise, dict):
        raise ValueError("Обещание должно быть объектом")
    kind = promise.get("type")
    if not isinstance(kind, str) or kind not in _FIELDS:
        raise ValueError(f"Неизвестный тип обещания: {kind}")
    required, optional = _FIELDS[kind]
    if required - promise.keys():
        raise ValueError("В обещании отсутствуют поля: " + ", ".join(sorted(required - promise.keys())))
    if promise.keys() - required - optional:
        raise ValueError("Неизвестные поля обещания: " + ", ".join(map(str, promise.keys() - required - optional)))
    mid, district, value = (promise.get(k) for k in ("measure", "district", "value"))
    if "measure" in required:
        if not isinstance(mid, str) or mid not in measures_by_id():
            raise ValueError(f"Неизвестная мера: {mid}")
    if "district" in required or district is not None:
        if not isinstance(district, str) or district not in district_names():
            raise ValueError(f"Неизвестный район: {district}")
    if kind == "include" and measures_by_id()[mid]["type"] == "city" and district is not None:
        raise ValueError(f"Для городской меры {mid} район должен быть null")
    if "value" in required:
        if type(value) not in (int, float):
            raise ValueError("Значение обещания должно быть конечным числом")
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError("Значение обещания должно быть конечным числом")
        if kind == "min_districts" and (value < 0 or int(value) != value):
            raise ValueError("Число районов должно быть целым и неотрицательным")
        if kind == "max_cost" and value < 0:
            raise ValueError("Предельный бюджет должен быть неотрицательным")
        if kind == "min_approval" and not 0 <= value <= 100:
            raise ValueError("Рейтинг должен быть в диапазоне от 0 до 100")
    return kind, mid, district, value


def _number(value):
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _label(key):
    kind, mid, district, value = key
    cases = next((d["cases"] for d in load_dataset()["districts"] if d["name"] == district), {})
    if kind == "include":
        name = measures_by_id()[mid]["name"].split(" (")[0]
        return name + (f" в {cases['loc']}" if district else "")
    if kind == "exclude":
        name = measures_by_id()[mid]["name"]
        return "Без ЛРТ" if "ЛРТ" in name else f"Без меры «{name}»"
    if kind == "district_project":
        return f"Проект в {cases['loc']}"
    if kind == "min_districts":
        if value == len(district_names()):
            return "Каждому району — свой проект"
        return f"Охват районов проектами: не менее {_number(value)}"
    if kind == "max_cost":
        return f"Бюджет — не больше {_number(value)}"
    if kind == "min_approval":
        return f"Рейтинг ≥ {_number(value)}"
    if kind == "no_critical":
        return "Без красных зон"
    return f"Рост D района {cases['gen']} — не менее {_number(value)}"


def label(promise) -> str:
    """Validate a promise and give it a Russian label using dataset cases."""
    return _label(_checked(promise))


def _fingerprint():
    digest = hashlib.sha256(json.dumps(
        [load_dataset(), CONSTANTS], ensure_ascii=False, sort_keys=True,
    ).encode("utf-8"))
    for name in ("score.py", "approval.py", "optimize.py", "validate.py", "model.py"):
        # Normalize newlines so Windows/Linux checkouts share the same table.
        digest.update((REPO_ROOT / "engine" / name).read_text(encoding="utf-8").encode("utf-8"))
    return digest.hexdigest()


def _schema():
    return ([("measure:" + mid, "B") for mid in measures_by_id()]
            + [("score", "d"), ("cost", "d"), ("approval", "d"),
               ("n_crit", "H"), ("districts", "Q"), ("n_districts", "B")]
            + [("delta:" + name, "d") for name in district_names()])


def _split_approval(column):
    """Decimal predictor plus an exact signed IEEE-754 bit correction.

    The residual retains even floating-point summation noise; no thresholds
    or ties change. Byte shuffling makes both integer streams compress well.
    """
    units = array("q", (round(v * _APPROVAL_SCALE) for v in column))
    predicted = array("q")
    predicted.frombytes(array("d", (v / _APPROVAL_SCALE for v in units)).tobytes())
    bits = array("q")
    bits.frombytes(column.tobytes())
    return units, array("q", (a - b for a, b in zip(bits, predicted)))


def _join_approval(units, residual):
    bits = array("q")
    bits.frombytes(array("d", (v / _APPROVAL_SCALE for v in units)).tobytes())
    for i, correction in enumerate(residual):
        bits[i] += correction
    result = array("d")
    result.frombytes(bits.tobytes())
    return result


@lru_cache(maxsize=1)
def _load_table():
    try:
        raw = lzma.decompress(TABLE_PATH.read_bytes())
    except FileNotFoundError as exc:
        raise ValueError("Нет таблицы обещаний: выполните python -m engine.promise") from exc
    header, separator, payload = raw.partition(b"\n")
    metadata = json.loads(header)
    if not separator or metadata.get("version") != _VERSION or metadata.get("fingerprint") != _fingerprint():
        raise ValueError("Таблица обещаний устарела: выполните python -m engine.promise")
    count = metadata["count"]
    columns, offset = {}, 0
    def read_column(code):
        nonlocal offset
        column = array(code)
        length = column.itemsize * count
        raw_column = bytearray(length)
        for byte in range(column.itemsize):
            raw_column[byte::column.itemsize] = payload[offset + byte * count:offset + (byte + 1) * count]
        column.frombytes(raw_column)
        if sys.byteorder != "little":
            column.byteswap()
        offset += length
        return column

    for name, code in _schema():
        columns[name] = (_join_approval(read_column("q"), read_column("q"))
                         if name == "approval" else read_column(code))
    if offset != len(payload):
        raise ValueError("Повреждена таблица обещаний: выполните python -m engine.promise")
    return metadata, columns


def _column_test(key):
    """Use the identical predicate for table filtering and a submitted plan."""
    kind, mid, district, value = key
    if kind == "include":
        target = district_names().index(district) + 1 if district else None
        return "measure:" + mid, (lambda x: x == target) if target else (lambda x: x != 0)
    if kind == "exclude":
        return "measure:" + mid, lambda x: x == 0
    if kind == "district_project":
        bit = 1 << district_names().index(district)
        return "districts", lambda x: bool(x & bit)
    if kind == "no_critical":
        return "n_crit", lambda x: x == 0
    if kind == "max_cost":
        return "cost", lambda x: x <= value
    column = {"min_districts": "n_districts", "min_approval": "approval",
              "min_district_delta": "delta:" + str(district)}[kind]
    return column, lambda x: x >= value


@lru_cache(maxsize=128)
def _mask(key):
    column, predicate = _column_test(key)
    # Byte-spaced bits let CPython construct a bitmap directly in C. Boolean
    # intersections/counts are bigint operations; no Python scan per conjunction.
    return int.from_bytes(bytes(map(predicate, _load_table()[1][column])), "little")


def _intersection(masks):
    if not masks:
        return None  # All rows, without allocating a full bitmap.
    result = masks[0]
    for mask in masks[1:]:
        result &= mask
    return result


@lru_cache(maxsize=128)
def _best_index(mask):
    scores = _load_table()[1]["score"]
    if mask is None:
        return max(range(len(scores)), key=scores.__getitem__)
    if not mask:
        return None
    flags = mask.to_bytes(len(scores), "little")
    return max((i for i, flag in enumerate(flags) if flag), key=scores.__getitem__)


def _record(index):
    columns = _load_table()[1]
    names = district_names()
    keys = []
    for mid in measures_by_id():
        target = columns["measure:" + mid][index]
        if target:
            keys.append((mid, names[target - 1] if target <= len(names) else None))
    return {"plan": _plan(sorted(keys)), "score": round(columns["score"][index], 2),
            "cost": round(columns["cost"][index], 2), "approval": round(columns["approval"][index], 2)}


def _conflict(masks):
    """Smallest singleton/pair first, then an inclusion-minimal larger conflict.

    A larger conflict need not have an infeasible pair (e.g. three transport
    promises). Deletion filtering prevents falsely blaming an unrelated promise.
    """
    for i, mask in enumerate(masks):
        if not mask:
            return [i]
    for i, j in combinations(range(len(masks)), 2):
        if not masks[i] & masks[j]:
            return [i, j]
    indices = list(range(len(masks)))
    for i in indices[:]:
        remaining = [j for j in indices if j != i]
        if _intersection([masks[j] for j in remaining]) == 0:
            indices = remaining
    return indices


def _plan_values(plan):
    ok, reason = validate(plan)
    if not ok:
        raise ValueError(reason)
    keys = _keys(plan)
    state = _state(keys)
    baseline = _state(())[2]
    names = district_names()
    values = {"measure:" + mid: 0 for mid in measures_by_id()}
    coverage = 0
    for mid, district in keys:
        values["measure:" + mid] = names.index(district) + 1 if district else len(names) + 1
        if district is not None:
            coverage |= 1 << names.index(district)
    values.update(score=state[0], cost=plan_cost(plan),
                  approval=_approval_values(keys, state, baseline)[0],
                  n_crit=sum(state[3]), districts=coverage, n_districts=coverage.bit_count())
    values.update({"delta:" + name: after - before for name, after, before in zip(names, state[2], baseline)})
    return values


def price(promises: list, plan: dict | None = None) -> dict:
    """Find the exact best legal plan satisfying every promise, and each alone."""
    if not isinstance(promises, list):
        raise ValueError("Обещания должны быть списком")
    keys = [_checked(p) for p in promises]
    own = _plan_values(plan) if plan is not None else None
    metadata, columns = _load_table()
    unconstrained = round(columns["score"][_best_index(None)], 2)
    masks = [_mask(key) for key in keys]
    rows = []
    for promise, key, mask in zip(promises, keys, masks):
        index = _best_index(mask)
        rows.append({**{k: round(v, 2) if isinstance(v, float) else v for k, v in promise.items()},
                     "label": _label(key), "feasible_alone": index is not None,
                     "price_alone": diff2(unconstrained, columns["score"][index]) if index is not None else None})
    joint = _intersection(masks)
    index = _best_index(joint)
    best = _record(index) if index is not None else None
    result = {"promises": rows, "feasible": best is not None,
              "count_feasible": metadata["count"] if joint is None else joint.bit_count(),
              "total_valid": metadata["count"], "best": best,
              "unconstrained_best": unconstrained,
              "price": diff2(unconstrained, best["score"]) if best else None}
    if best:
        n = len(promises)
        if n == 0:
            intro = "Обещания не заданы."
        elif n == 1:
            intro = "Обещание выполнимо."
        elif n % 10 == 1 and n % 100 != 11:
            intro = f"Все обещания ({n}) выполнимы."
        else:
            noun = "обещания" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "обещаний"
            intro = f"Все {n} {noun} выполнимы."
        result["verdict"] = (f"{intro} Цена в баллах Score — {result['price']:.2f}: "
                             f"лучший план даёт {best['score']:.2f} вместо {unconstrained:.2f}.")
    else:
        indices = _conflict(masks)
        names = [f"«{rows[i]['label']}»" + (f" ({keys[i][1]})" if keys[i][1] else "") for i in indices]
        result["verdict"] = "Нет допустимого плана: " + (
            "невыполнимо обещание " if len(indices) == 1 else "несовместимы обещания "
        ) + "; ".join(names) + "."
    if own is not None:
        broken = []
        for key, row in zip(keys, rows):
            column, predicate = _column_test(key)
            if not predicate(own[column]):
                broken.append(row["label"])
        result["your_plan"] = {"score": round(own["score"], 2), "keeps_promises": not broken, "broken": broken}
    return result


def _examples():
    # These are UI examples, not constants used by the optimizer or formula.
    return [{"type": "min_districts", "value": len(district_names())},
            {"type": "min_approval", "value": CONSTANTS["THRESHOLD"]},
            {"type": "include", "measure": "M7", "district": "Есиль"},
            {"type": "exclude", "measure": "M3"},
            {"type": "max_cost", "value": 80},
            {"type": "no_critical"},
            {"type": "district_project", "district": "Алматы"},
            {"type": "district_project", "district": "Есиль"}]


def promise_catalog() -> list:
    """Ready-made promises and their precomputed standalone prices."""
    return [dict(promise) for promise in _load_table()[0]["catalog"]]


def main():
    start = time.perf_counter()
    columns = {name: array(code) for name, code in _schema()}
    names = district_names()
    targets = {name: i + 1 for i, name in enumerate(names)}
    targets[None] = len(names) + 1
    baseline = _state(())[2]
    examples = _examples()
    predicates = [_column_test(_checked(p)) for p in examples]
    alone = [None] * len(examples)
    optimum = None
    count = 0
    for keys, cost in _valid_options():
        state = _state(keys)
        coverage = 0
        row = {"measure:" + mid: 0 for mid in measures_by_id()}
        for mid, district in keys:
            row["measure:" + mid] = targets[district]
            if district is not None:
                coverage |= 1 << (targets[district] - 1)
        row.update(score=state[0], cost=cost, approval=_approval_values(keys, state, baseline)[0],
                   n_crit=sum(state[3]), districts=coverage, n_districts=coverage.bit_count())
        row.update({"delta:" + name: after - before for name, after, before in zip(names, state[2], baseline)})
        for name, column in columns.items():
            column.append(row[name])
        if optimum is None or state[0] > optimum:
            optimum = state[0]
        for i, (column, predicate) in enumerate(predicates):
            if predicate(row[column]) and (alone[i] is None or state[0] > alone[i]):
                alone[i] = state[0]
        count += 1
    catalog = [{**p, "label": label(p), "feasible_alone": score is not None,
                "price_alone": diff2(optimum, score) if score is not None else None}
               for p, score in zip(examples, alone)]
    metadata = {"version": _VERSION, "fingerprint": _fingerprint(), "count": count, "catalog": catalog}
    with lzma.open(TABLE_PATH, "wb", preset=6) as stream:
        stream.write(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
        for name, original in columns.items():
            parts = _split_approval(original) if name == "approval" else (original,)
            for column in parts:
                if sys.byteorder != "little":
                    column.byteswap()
                raw = column.tobytes()
                stream.write(b"".join(raw[byte::column.itemsize] for byte in range(column.itemsize)))
    _load_table.cache_clear()
    _mask.cache_clear()
    _best_index.cache_clear()
    print(f"Время построения: {time.perf_counter() - start:.2f} с; "
          f"допустимых планов: {count}; размер файла: {TABLE_PATH.stat().st_size} байт")


if __name__ == "__main__":
    main()
