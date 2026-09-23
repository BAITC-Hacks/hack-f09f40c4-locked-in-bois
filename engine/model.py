"""Dataset access and the shared plan representation."""

import json
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=None)
def load_dataset(path=None) -> dict:
    """Load UTF-8 JSON once per path; callers should treat it as read-only."""
    return json.loads(Path(path or REPO_ROOT / "data/dataset.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_events() -> list[dict]:
    return json.loads((REPO_ROOT / "data/events.json").read_text(encoding="utf-8"))["events"]


@lru_cache(maxsize=1)
def measures_by_id() -> dict:
    return {m["id"]: m for m in load_dataset()["measures"]}


def indicator_codes() -> list[str]:
    return [i["code"] for i in load_dataset()["indicators"]]


def district_names() -> list[str]:
    return [d["name"] for d in load_dataset()["districts"]]


def normalize_plan(plan) -> list[dict]:
    """Copy either public plan form, making absent districts explicit."""
    decisions = plan.get("decisions") if isinstance(plan, dict) else plan
    if not isinstance(decisions, list):
        raise ValueError("План должен содержать список решений")
    if any(not isinstance(d, dict) for d in decisions):
        raise ValueError("Каждое решение должно быть объектом")
    return [{"measure": d.get("measure"), "district": d.get("district")} for d in decisions]


def plan_cost(plan) -> int:
    measures = measures_by_id()
    return sum(measures[d["measure"]]["cost"] for d in normalize_plan(plan))
