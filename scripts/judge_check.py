"""Offline judge report: python scripts/judge_check.py [--full].

Golden constants below are independent verification fixtures, never inputs to
the score formula. All reported results are calculated by the existing engine.
The optional enumeration builds a comparison cache in memory without writing it.
"""

import argparse
from copy import deepcopy
from functools import lru_cache
import os
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
# Direct script execution puts scripts/, rather than the repository, on sys.path.
sys.path.insert(0, str(ROOT))

EXPECTED_DISTRICTS = {
    "Есиль": 62.99, "Алматы": 57.06, "Сарыарка": 54.65,
    "Байконур": 56.63, "Нура": 49.18,
}
EXPECTED_TOTAL = 694_395


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


class _Report:
    def __init__(self):
        self.failed = False

    def check(self, label, operation):
        start = perf_counter()
        try:
            detail = operation()
            status = "PASS"
        except Exception as exc:
            self.failed = True
            status = "FAIL"
            detail = f"Ошибка проверки: {exc}"
        print(f"{status} | {label}: {detail} | {perf_counter() - start:.2f} с", flush=True)


def _run_checks(report, full):
    from engine.model import load_dataset, measures_by_id, normalize_plan, plan_cost
    from engine.optimize import _load_cache, generate_cache, optimize_info, what_if
    from engine.score import compute, diff2, evaluate
    from engine.validate import validate

    data = load_dataset()
    original = deepcopy(data)
    doc = {"decisions": deepcopy(data["reference"]["doc_example"]["decisions"])}

    def fixture(ids):
        measures = measures_by_id()
        return {"decisions": [
            {"measure": mid, "district": "Нура" if measures[mid]["type"] == "district" else None}
            for mid in ids
        ]}

    @lru_cache(maxsize=1)
    def agent_module():
        from api import agent
        return agent

    def districts():
        actual = {name: row["D_before"] for name, row in compute([])["districts"].items()}
        _require(actual == EXPECTED_DISTRICTS, f"Индексы районов не совпали с условием: {actual}")
        return "; ".join(f"{name}: D={value:.2f}" for name, value in actual.items())

    def baseline():
        score = compute([])["score"]
        _require(score == data["reference"]["baseline_score"] == 52.56,
                 f"Исходный Score не совпал с условием: {score:.2f}")
        return f"Score {score:.2f}"

    def doc_example():
        result = evaluate(doc)
        reference = data["reference"]["doc_example"]
        _require(result["score"] == reference["score"] == 56.54
                 and result["cost"] == reference["cost"] == 95,
                 f"Пример не совпал с условием: Score {result['score']:.2f}, стоимость {result['cost']}")
        _require(result["delta"] == diff2(result["score"], result["baseline"]),
                 "Прирост не совпал с разностью отображаемых оценок")
        return (f"Score {result['score']:.2f}, стоимость {result['cost']}; "
                f"прирост {result['delta']:+.2f}")

    def cheapest():
        result = evaluate(fixture(("M9", "M11", "M10", "M4", "M12")))
        _require(result["score"] == 55.67 and result["cost"] == 61,
                 f"Дешёвый план не совпал с условием: {result['score']:.2f}, {result['cost']}")
        _require(result["cost"] == _load_cache()["min_cost"], "Минимальная стоимость не совпала с кэшем")
        return f"Score {result['score']:.2f}, стоимость {result['cost']}"

    def optimum():
        result = evaluate(fixture(("M2", "M3", "M8", "M9", "M14")))
        best = optimize_info(doc)["best"]
        recomputed = evaluate(best["plan"])
        _require(result["score"] == best["score"] == recomputed["score"] == 57.24,
                 f"Оптимальный Score не совпал с условием: {result['score']:.2f}; кэш: {best['score']:.2f}")
        _require(result["cost"] == best["cost"] == recomputed["cost"] == 98,
                 "Стоимость оптимума не совпала с условием")
        return f"Score {recomputed['score']:.2f}, стоимость {recomputed['cost']} (сверено с кэшем)"

    def cache_count():
        cache = _load_cache()
        total = optimize_info(doc)["total_valid"]
        _require(total == cache["total_valid"] == sum(n for _, n in cache["score_hist"]) == EXPECTED_TOTAL,
                 f"Число допустимых планов или гистограмма не совпали: {total}")
        return f"{total:,} допустимых планов; источник: data/optimizer_cache.json"

    def common_data():
        first, second = evaluate(doc), evaluate(deepcopy(doc))
        _require(load_dataset() == original and first == second,
                 "Исходные данные изменились или одинаковые планы дают разные результаты")
        _require(first["cost"] + first["remaining"] == data["budget"], "Бюджет не совпал с датасетом")
        _require(all(row["before"] == compute([])["districts"][name]["before"]
                     for name, row in first["districts"].items()), "Начальные показатели различаются")
        return f"единый бюджет {data['budget']}, единый исходный Score {first['baseline']:.2f}"

    def over_budget():
        over = fixture(("M3", "M13", "M7", "M5", "M2"))
        cost = plan_cost(over)
        ok, reason = validate(over)
        _require(cost > data["budget"] and not ok and reason and "бюджет" in reason.lower(),
                 "Перерасход не отклонён с объяснением бюджета")
        try:
            evaluate(over)
        except ValueError as exc:
            _require(str(exc) == reason, "Расчёт и валидатор вернули разные причины отказа")
        else:
            raise ValueError("Движок рассчитал Score плана с перерасходом")
        return f"расчёт отклонён; {reason}"

    def indicators():
        result = evaluate(doc)
        changed = [name for name, row in result["districts"].items() if row["before"] != row["after"]]
        _require(changed, "Решения не изменили показатели")
        # The first district project in the organizer's example supplies the witness.
        decision = next(d for d in normalize_plan(doc) if d["district"] is not None)
        district = next(d for d in data["districts"] if d["name"] == decision["district"])
        row = result["districts"][district["name"]]
        delta = diff2(row["D_after"], row["D_before"])
        _require(delta > 0, "Районный проект не улучшил индекс в примере")
        return (f"в {district['cases']['loc']} D: {row['D_before']:.2f} → "
                f"{row['D_after']:.2f}, изменение {delta:+.2f}")

    def analysis():
        agent = agent_module()
        result = agent.analyze(doc)
        for field in ("summary",):
            _require(isinstance(result.get(field), str) and result[field].strip(), f"Нет текста {field}")
        for field in ("strengths", "risks", "consequences", "tradeoffs"):
            _require(isinstance(result.get(field), list) and result[field]
                     and all(isinstance(s, str) and s.strip() for s in result[field]), f"Нет объяснений {field}")
        rec = result["recommendation"]
        _require(isinstance(rec["why"], str) and rec["why"].strip(), "Нет объяснения рекомендации")
        expected = evaluate(rec["plan"])["score"]
        _require(rec["expected_score"] == expected, "Оценка рекомендации не подтверждена движком")
        _require(expected >= evaluate(doc)["score"], "Рекомендация ухудшает пример из условия")
        _require(result["provider"] == "offline" and result["grounded"] is True,
                 "Анализ не автономен или числа не подтверждены")
        _require(result["guard"] == {"attempts": 0, "rejected": []} and result["tool_trace"] == [],
                 "В автономном режиме была попытка обращения к модели")
        _require(agent.guard(result, [agent._context(doc)]) == [], "Анализ содержит неподтверждённые числа")
        return ("автономный ИИ-анализ: итог, сильные стороны, риски, последствия, компромиссы, "
                f"рекомендация и служебные поля заполнены; Score рекомендации {expected:.2f}")

    def changed_score():
        current = evaluate(doc)["score"]
        swap = {"out": "M5", "in": {"measure": "M14", "district": None}}
        changed = what_if(doc, swap)
        _require(changed["valid"] and changed["score"] != current, "Допустимая замена не изменила Score")
        _require(changed["score"] == evaluate(changed["plan"])["score"]
                 and changed["delta_vs_current"] == diff2(changed["score"], current),
                 "Предварительная оценка замены не совпала с расчётом")
        return (f"после замены {swap['out']} на {swap['in']['measure']}: "
                f"{current:.2f} → {changed['score']:.2f}, изменение {changed['delta_vs_current']:+.2f}")

    def number_guard():
        agent = agent_module()
        result, info = evaluate(doc), optimize_info(doc)
        facts = [result, info]
        # Deliberately false test input; never presented as an engine result.
        invented = "987654321.12"
        _require(agent.guard({"text": f"Score {invented}"}, facts) == [invented],
                 "Защита пропустила заведомо выдуманное число")
        text = (f"Score {result['score']:.2f}, прирост {result['delta']:+.2f}, "
                f"стоимость {result['cost']}, оптимум {info['best']['score']:.2f}, планов {info['total_valid']}")
        _require(agent.guard({"text": text}, facts) == [], "Защита отклонила числа движка")
        return "выдуманное число отклонено; оценки, прирост, стоимость и число планов движка приняты"

    def timing():
        agent = agent_module()
        calls = (("расчёт", lambda: evaluate(doc)), ("оптимизация", lambda: optimize_info(doc)),
                 ("автономный анализ", lambda: agent.analyze(doc)))
        elapsed = []
        for label, call in calls:
            call()  # Warm caches before measuring the public call.
            start = perf_counter()
            call()
            elapsed.append((label, perf_counter() - start))
        details = "; ".join(f"{label}: {seconds:.2f} с" for label, seconds in elapsed)
        _require(all(seconds < 1.5 for _, seconds in elapsed), f"Превышен лимит отклика: {details}")
        return f"после прогрева кэшей, каждый вызов быстрее 1.50 с; {details}"

    def full_enumeration():
        rebuilt = generate_cache()
        cached = _load_cache()
        _require(rebuilt["total_valid"] == EXPECTED_TOTAL, "Полный перебор дал другое число планов")
        for field in ("total_valid", "top", "pareto", "best_by_max_cost", "score_hist", "balanced", "min_cost", "dataset_version"):
            _require(rebuilt[field] == cached[field], f"Полный перебор не совпал с кэшем: {field}")
        return (f"{rebuilt['total_valid']:,} допустимых планов, оптимум {rebuilt['top'][0]['score']:.2f}; "
                "результаты совпали с кэшем; файлы не изменялись")

    for label, operation in (
        ("Датасет организаторов — районные индексы", districts),
        ("Исходная оценка", baseline),
        ("Пример из условия", doc_example),
        ("Самый дешёвый план", cheapest),
        ("Глобальный оптимум", optimum),
        ("Число допустимых планов из кэша", cache_count),
        ("Критерий 1 — единые исходные данные и бюджет", common_data),
        ("Критерий 2 — запрет превышения бюджета", over_budget),
        ("Критерий 3 — решения меняют показатели", indicators),
        ("Критерий 4 — ИИ объясняет результат и компромиссы", analysis),
        ("Критерий 5 — замена решения меняет Score", changed_score),
        ("Защита чисел", number_guard),
        ("Время отклика", timing),
    ):
        report.check(label, operation)
    if full:
        print("Полный перебор: ожидайте завершения…", flush=True)
        report.check("Полный перебор в памяти", full_enumeration)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Автономная проверка кейса «Аким на 5 часов»")
    parser.add_argument("--full", action="store_true", help="повторить полный перебор в памяти (около 21 с)")
    args = parser.parse_args(argv)
    start = perf_counter()
    report = _Report()
    print("Проверка для жюри — без сервера, сети и ключей", flush=True)
    if not args.full:
        print("Быстрый режим: число планов и оптимум сверяются с кэшем; повторный перебор: --full", flush=True)
    try:
        with patch.dict(os.environ, {"LLM_PROVIDER": "offline"}):
            for name in ("OPENAI_API_KEY", "OPENAI_API_KEYS", "NVIDIA_API_KEY", "NVIDIA_API_KEYS"):
                os.environ.pop(name, None)
            # Even an accidental provider regression cannot make a network call.
            with patch("socket.socket.connect", side_effect=OSError("Сеть отключена проверкой жюри")), \
                 patch("socket.socket.connect_ex", side_effect=OSError("Сеть отключена проверкой жюри")):
                _run_checks(report, args.full)
    except Exception as exc:
        report.failed = True
        print(f"FAIL | Подготовка проверки: {exc}", flush=True)
    status = "FAIL" if report.failed else "PASS"
    print(f"{status} | Итог проверки; общее время: {perf_counter() - start:.2f} с", flush=True)
    return int(report.failed)


if __name__ == "__main__":
    raise SystemExit(main())
