"""Unpruned rules census; run with RUN_SLOW=1 and pytest -s for its table."""

import os
import random
import re
from collections import Counter
from itertools import combinations
from math import comb
from time import perf_counter

import pytest

from engine.model import district_names, load_dataset, measures_by_id, plan_cost
from engine.validate import validate


def _universe():
    """Build every legal option, without filtering any five-option plans."""
    data = load_dataset()
    options = tuple(
        (measure["id"], district)
        for measure in data["measures"]
        for district in (
            district_names() if measure["type"] == "district" else [None]
        )
    )
    # Golden problem dimensions: rule evaluation below uses dataset values.
    assert len(options) == len(set(options)) == 54
    assert data["decisions_required"] == 5
    assert data["budget"] == 100
    assert data["max_per_direction"] == 2
    assert {
        (frozenset(rule["pair"]), rule["scope"])
        for rule in data["incompatibilities"]
    } == {
        (frozenset(("M1", "M3")), "anywhere"),
        (frozenset(("M4", "M7")), "same_district"),
        (frozenset(("M5", "M13")), "same_district"),
    }
    assert comb(len(options), data["decisions_required"]) == 3_162_510
    return options, data["decisions_required"]


def _census(selections):
    """Check both acceptance and rejection independently of validator pruning."""
    data, measures = load_dataset(), measures_by_id()
    russian = re.compile(r"[А-Яа-яЁё]")
    invalid_reasons = Counter()
    total = valid = 0
    for selected in selections:
        plan = {
            "decisions": [
                {"measure": mid, "district": district}
                for mid, district in selected
            ]
        }
        ok, reason = validate(plan)
        total += 1

        ids = [mid for mid, _ in selected]
        locations = dict(selected)
        cost = sum(measures[mid]["cost"] for mid in ids)
        direction_counts = Counter(measures[mid]["direction"] for mid in ids)
        conflicts = [
            rule["pair"]
            for rule in data["incompatibilities"]
            if all(mid in locations for mid in rule["pair"])
            and (
                rule["scope"] == "anywhere"
                or locations[rule["pair"][0]] == locations[rule["pair"][1]]
            )
        ]
        expected = (
            len(set(ids)) == data["decisions_required"]
            and cost <= data["budget"]
            and max(direction_counts.values()) <= data["max_per_direction"]
            and not conflicts
        )
        assert ok is bool(expected), (plan, reason, expected)
        if ok:
            valid += 1
            assert reason is None, (plan, reason)
            assert plan_cost(plan) == cost <= data["budget"], plan
            assert max(direction_counts.values()) <= data["max_per_direction"], plan
            assert not conflicts, (plan, conflicts)
        else:
            assert isinstance(reason, str) and reason.strip(), (plan, reason)
            assert russian.search(reason), (plan, reason)
            # Keep the exact first-failing reason, including measure/district.
            invalid_reasons[reason] += 1

    assert total == valid + sum(invalid_reasons.values())
    return total, valid, invalid_reasons


def _print_report(label, result, elapsed):
    total, valid, reasons = result
    print(f"\n{label}: проверено {total:,}; допустимо {valid:,}; "
          f"недопустимо {sum(reasons.values()):,}; время {elapsed:.2f} с")
    print("Количество | Первая причина отказа")
    for reason, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0])):
        print(f"{count:10,d} | {reason}")


def test_rules_deterministic_sample():
    """20,000 distinct combinations spread over the entire option universe."""
    started = perf_counter()
    options, size = _universe()
    rng = random.Random(20260923)
    sampled = set()
    while len(sampled) < 20_000:
        sampled.add(tuple(sorted(rng.sample(range(len(options)), size))))
    result = _census(
        tuple(options[index] for index in indices)
        for indices in sorted(sampled)
    )
    _print_report("Детерминированная выборка", result, perf_counter() - started)
    total, valid, reasons = result
    assert total == 20_000
    assert 0 < valid < total
    assert reasons


@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get("RUN_SLOW") != "1",
    reason="Set RUN_SLOW=1 for full enumeration",
)
def test_rules_exhaustive_census():
    started = perf_counter()
    options, size = _universe()
    # No optimizer/cache shortcuts: validate is called for every combination.
    result = _census(combinations(options, size))
    _print_report("Полный перебор", result, perf_counter() - started)
    total, valid, reasons = result
    assert total == comb(len(options), size) == 3_162_510
    assert valid == 694_395
    assert sum(reasons.values()) == total - valid
