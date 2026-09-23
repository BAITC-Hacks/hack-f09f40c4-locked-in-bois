"""README claims use the same numeric provenance guard as AI responses.

This checks provenance, not whether a supported number is used in the right
sentence. Cached histograms are deliberately excluded: their arbitrary counts
must not accidentally authorize an incorrect score or cost.
"""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import re

import pytest

from api.agent import guard
from api.main import app
from engine.approval import CONSTANTS, approval
from engine.calendar import calendar
from engine.duel import duel_vs_best
from engine.fairness import fairness
from engine.grading import grade
from engine.model import district_names, load_dataset, load_events, plan_cost
from engine.optimize import _replace, optimize_info
from engine.promise import price, promise_catalog
from engine.receipt import receipt
from engine.score import compute, diff2, evaluate
from engine.stress import robust_info, stress_test
from engine.validate import validate


ROOT = Path(__file__).resolve().parents[1]

# Documentation metadata, never scores, costs, rankings or engine deltas.
# Keep explicit even when a value also happens to occur in engine output.
NON_ENGINE_NUMBERS = {
    "Версии Python": ("3.10", "3.12"),
    "Порт localhost и фрагмент IPv4 для guard": (8000, 127.0),
    "Коды HTTP": (401, 403, 404, 410, 422),
    "Год газетного выпуска": (2028,),
    "Секунды: запуск/таймаут, перебор, ИИ, газета": (60, 21, 30, 18),
    "Заявленная нижняя граница числа тестов": (160,),
}

GROUPED = re.compile(r"(?<![\w.])\d{1,3}(?:[ ,\u00a0\u202f]\d{3})+(?!\d)")
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
INLINE_CODE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
API_PATH = re.compile(r"/api/[A-Za-z0-9_/-]+")
FILE_PATH = re.compile(
    r"(?<![\w.])(?:[\w.-]+[/\\])*[\w.-]+"
    r"\.(?:py|md|json|xz|db|png|txt|html|cjs|exe)\b"
    r"|(?<![\w.])\.(?:env(?:\.example)?|gitignore)\b"
    r"|(?<![\w./\\])(?:[\w.-]+[/\\])+[\w.-]+"
    r"|(?<![\w./])(?:[\w.-]+/)+(?=\s|$)"
)


def _plan(*decisions):
    return {"decisions": [{"measure": mid, "district": district}
                          for mid, district in decisions]}


def _swap(plan, out, mid, district=None):
    return _replace(plan, {"out": out, "in": {"measure": mid, "district": district}})


def _numeric_text(text):
    """Normalize written decimals/grouping, preserving the guard's policy.

    The AI guard understands space grouping but treats commas as decimal marks;
    README also uses English 694,395. Leading-dot weights need an explicit zero.
    Small integers and measure identifiers are left for guard itself to handle.
    """
    text = GROUPED.sub(lambda m: re.sub(r"[ ,\u00a0\u202f]", "", m[0]), text)
    text = re.sub(r"(?<![\w.])\.(\d+)", r"0.\1", text)
    return text.replace("−", "-")


@pytest.fixture(scope="module")
def readme():
    return (ROOT / "README.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def readme_facts():
    data = load_dataset()
    doc = {"decisions": deepcopy(data["reference"]["doc_example"]["decisions"])}
    optimizer = optimize_info(doc)
    optimum = optimizer["best"]["plan"]
    balanced = optimizer["balanced"]["plan"]
    cheapest = _plan(("M9", "Нура"), ("M11", "Нура"), ("M10", "Нура"),
                     ("M4", "Нура"), ("M12", None))
    swapped = _swap(doc, "M5", "M14")
    trap = _swap(doc, "M10", "M11", "Алматы")
    facts = [data, load_events(), CONSTANTS, compute([]), optimizer,
             {"pareto_points": len(optimizer["pareto"])}]
    results = {}
    for name, plan in (("doc", doc), ("optimum", optimum), ("balanced", balanced),
                       ("cheapest", cheapest), ("swap", swapped), ("trap", trap)):
        results[name] = evaluate(plan)
        facts.extend([results[name], approval(plan)])
    # Cost is meaningful even for invalid plans; evaluate() must reject them.
    over_budget_plans = [
        _swap(doc, "M12", "M3", "Нура"),
        _swap(swapped, "M12", "M3", "Нура"),
        # The separate over-budget example linked to tests/test_criteria.py.
        _plan(("M3", "Нура"), ("M13", "Алматы"), ("M7", "Есиль"),
              ("M5", "Сарыарка"), ("M2", None)),
    ]
    for plan in over_budget_plans:
        valid, reason = validate(plan)
        assert not valid, "Ожидался недопустимый план из примеров превышения бюджета"
        facts.append({"cost": plan_cost(plan), "reason": reason})
    for row in optimizer["pareto"]:
        facts.extend([evaluate(row["plan"]), approval(row["plan"])])
    promises = ([{"type": "min_approval", "value": CONSTANTS["THRESHOLD"]}],
                [{"type": "min_districts", "value": len(district_names())}])
    stresses = {}
    for name, plan in (("doc", doc), ("optimum", optimum)):
        stresses[name] = stress_test(plan)
        facts.extend([stresses[name], grade(plan)])
        facts.extend([receipt(plan), duel_vs_best(plan), fairness(plan), calendar(plan)])
        facts.extend(price(promise, plan=plan) for promise in promises)
    facts.append(promise_catalog())
    robust = robust_info()["crisis_proof"]
    facts.extend([robust, evaluate(robust["plan"]), approval(robust["plan"])])
    t1 = [indicator["code"] for indicator in data["indicators"]].index("T1")
    facts.append({
        "robust_worst_gain": diff2(robust["worst_score"], stresses["optimum"]["worst_score"]),
        "robust_normal_loss": diff2(results["optimum"]["score"], robust["score"]),
        "balanced_loss": diff2(results["optimum"]["score"], results["balanced"]["score"]),
        "trap_T1_loss": diff2(results["trap"]["districts"]["Алматы"]["before"][t1],
                              results["trap"]["districts"]["Алматы"]["after"][t1]),
    })
    political = approval(optimum)
    facts.append({"unserved_percent": round(sum(
        district["pop"] for district in data["districts"]
        if not political["districts"][district["name"]]["got_district_measure"]
    ) * 100, 2)})
    return facts


def test_readme_numbers_are_grounded(readme, readme_facts):
    print("Явный список чисел README вне движка:")
    for reason, values in NON_ENGINE_NUMBERS.items():
        print(f"  {reason}: {', '.join(map(str, values))}")
    text = _numeric_text(readme)
    extracted = [token for token in NUMBER.findall(text)
                 if "." in token or abs(Decimal(token)) >= 10]
    assert extracted, "В README не найдены числа для проверки"
    exceptions = [float(value) for values in NON_ENGINE_NUMBERS.values() for value in values]
    facts = [*readme_facts, exceptions]
    offenders = guard({"readme": text}, facts)
    locations = [f"README.md:{line_no}: {line.strip()}"
                 for line_no, line in enumerate(text.splitlines(), 1)
                 if set(NUMBER.findall(line)) & set(offenders)]
    assert not offenders, (
        "README содержит числа, не подтверждённые движком: " + ", ".join(offenders)
        + "\n" + "\n".join(locations)
        + "\nИсправьте документацию отдельно; не добавляйте эти числа в исключения."
    )


def test_readme_backtick_file_paths_exist(readme):
    paths = {match[0].replace("\\", "/") for span in INLINE_CODE.findall(readme)
             for match in FILE_PATH.finditer(API_PATH.sub("", span))}
    assert paths, "В README не найдены ссылки на файлы"
    missing = []
    for name in sorted(paths):
        if name in {"doc.json", ".env", "data/leaderboard.db"} or name.startswith(".venv/"):
            continue
        candidates = [ROOT / name]
        # README abbreviates engine modules and screenshot filenames in prose.
        if "/" not in name:
            if name.endswith(".py"):
                candidates.append(ROOT / "engine" / name)
            elif name.endswith(".png"):
                candidates.append(ROOT / "docs" / "screenshots" / name)
            elif name == "dataset.json":
                candidates.append(ROOT / "data" / name)
        if not any(path.exists() for path in candidates):
            missing.append(name)
    assert not missing, "Пути в README не существуют: " + ", ".join(missing)


def test_readme_api_paths_are_registered(readme):
    mentioned = set(API_PATH.findall(readme))
    registered = {route.path for route in app.routes}
    assert mentioned, "В README не найдены пути /api/..."
    missing = sorted(mentioned - registered)
    assert not missing, "Маршруты README не зарегистрированы в FastAPI: " + ", ".join(missing)
