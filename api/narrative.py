"""Deterministic council and newspaper templates; numbers arrive from engine facts."""

import hashlib
import json


DEPUTIES = {"Есиль": "Депутат от Есиля", "Алматы": "Депутат от Алматы",
            "Сарыарка": "Депутат от Сарыарки", "Байконур": "Депутат от Байконура",
            "Нура": "Депутат от Нуры"}
CONCERNS = {
    "Есиль": "Жители ждут свободных мостов и мест в школах.",
    "Алматы": "Старые сети и пробки остаются нашей повесткой.",
    "Сарыарка": "Зимой люди хотят дышать чистым воздухом у своего дома.",
    "Байконур": "Мы привыкли держаться в середине, но тоже ждём внимания.",
    "Нура": "Нам важно перестать быть окраиной по доступности школ и транспорта.",
}
QUOTES = {
    "positive": (
        "Нас услышали: {projects}. Индекс района: {delta}. {concern}",
        "Теперь у нас есть адресные проекты: {projects}. Прибавка к индексу — {delta}. Будем следить за исполнением.",
        "Для нашего района {delta} — ощутимый шаг. Получили {projects}. {concern}",
    ),
    "negative": (
        "У нас живут {pop}% горожан, а достались только городские программы: {delta} к индексу. {concern}",
        "Городские меры дали нам {delta}, но районного проекта нет. За нами {pop}% жителей города. {concern}",
        "Прибавка {delta} есть. А где адресное решение для наших жителей — это {pop}% города? {concern}",
    ),
    "neutral": (
        "Индекс сдвинулся на {delta}. Спасибо за этот шаг, но вопросы жителей остаются. {concern}",
        "Вижу изменение {delta}. Будем судить по тому, что люди почувствуют во дворе. {concern}",
        "Наш результат — {delta} к индексу. Надеюсь, это начало последовательной работы. {concern}",
    ),
}


def template(plan: dict, facts: dict) -> dict:
    """No LLM and no scoring here; hash selection is stable across processes."""
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).digest()
    council = []
    for index, d in enumerate(facts["districts"]):
        mood = ("positive" if d["got_district_measure"] and d["delta_D"] >= 1.5
                else "negative" if not d["got_district_measure"] and d["approval"] < 50
                else "neutral")
        quote = QUOTES[mood][digest[index] % len(QUOTES[mood])].format(
            delta=f"{d['delta_D']:+.2f}", pop=f"{d['pop_percent']:g}",
            projects=", ".join(d["projects"]), concern=CONCERNS[d["district"]])
        council.append({"district": d["district"], "deputy": DEPUTIES[d["district"]],
                        "mood": mood, "quote": quote, "delta_D": d["delta_D"],
                        "approval": d["approval"]})
    winner = max(facts["districts"], key=lambda d: d["delta_D"])
    weakest = min(facts["districts"], key=lambda d: d["D_after"])
    problem = min(facts["crit_cells"], key=lambda c: c["value"], default=None)
    city = facts["approval"]["city"]
    headlines = [
        {"title": f"{winner['district']}: наибольший рост индекса",
         "lead": f"Изменение {winner['delta_D']:+.2f}; индекс района — {winner['D_after']:.2f}."},
        {"title": (f"{problem['district']}: {problem['indicator']} остаётся ниже порога" if problem
                   else f"Критических ячеек нет; {weakest['district']} всё ещё отстаёт"),
         "lead": (f"Значение {problem['value']:.2f} при пороге {facts['crit_threshold']}." if problem
                  else f"Самый низкий районный индекс — {weakest['D_after']:.2f}. Отсутствие критических ячеек не означает, что все проблемы решены.")},
        {"title": f"Рейтинг акима — {city:.2f}: " + (
            "горсовет обсуждает риск отставки" if not facts["approval"]["reelected"] else "порог поддержки пройден"),
         "lead": "Это модель политического риска, не часть официального Score."},
        {"title": f"Score — {facts['score']:.2f}: цена решений",
         "lead": f"Бюджет плана — {facts['cost']}. " + (
             "Ранг исходного плана" if facts.get("crisis") else "Ранг плана") +
             f" — {facts['rank']} из {facts['total_valid']}."},
    ]
    if facts.get("crisis"):
        crisis = facts["crisis"]
        lead = (f"Кризис стоил {crisis['crisis_cost']:.2f} Score; замена вернула {crisis['recovered']:+.2f}."
                if "recovered" in crisis else
                f"До кризиса — {crisis['score_before']:.2f}, после — {crisis['new_baseline_score']:.2f}. Совет ждёт замены меры.")
        headlines.append({"title": crisis["event"]["title"], "lead": lead})
    return {"council": council, "newspaper": {
        "masthead": "Астана Times", "date": "IV квартал 2028", "headlines": headlines,
        "editorial": (f"Score {facts['score']:.2f} и поддержка {city:.2f} отвечают на разные вопросы. "
                      f"{winner['district']} получает наибольшую прибавку, но средний успех не заменяет "
                      "внимания к каждому району. Адресные проекты и сроки их реализации нужно "
                      "объяснять жителям: рейтинг акима показывает политический риск, а не меняет формулу качества жизни."),
        "crit_sidebar": [dict(c) for c in facts["crit_cells"]]}}
