"""Deterministic council and newspaper templates; numbers arrive from engine facts."""

import hashlib
import json

from engine.model import load_dataset, measures_by_id


def district_case(name, case):
    return next(d["cases"][case] for d in load_dataset()["districts"] if d["name"] == name)


def join_words(items):
    items = list(items)
    return ", ".join(items[:-1]) + " и " + items[-1] if len(items) > 1 else "".join(items)


def measure_list(names):
    names = list(names)
    if len(names) >= 3:
        names = [name.split(" (")[0].split(" / ")[0] for name in names]
    return join_words(f"«{name}»" for name in names)


def number(value):
    return f"{value:,}".replace(",", " ")


def indicator_label(code):
    name = next(i["name"] for i in load_dataset()["indicators"] if i["code"] == code)
    return f"{code} ({name[0].lower() + name[1:]})"


# Each voice follows its district's dataset profile. Variants never imply that
# a particular local problem was solved merely because the overall index rose.
QUOTES = {
    "Есиль": {
        "positive": (
            "Дорогие квартиры не спасают от очереди в школу. Нас услышали: {projects}. Индекс района прибавил {delta}; теперь спросим за исполнение.",
            "{projects} — уже предметный разговор. Рост индекса на {delta} поддерживаю, но жители будут судить по мостам и школьным классам.",
        ),
        "negative": (
            "За красивыми фасадами не видно переполненных школ. У нас {pop}% горожан, а районных проектов нет. Изменение индекса на {delta} этого не оправдывает.",
            "Состоятельный район тоже стоит в пробках на мостах. Индекс изменился на {delta}, но для наших {pop}% горожан отдельного проекта не нашлось.",
        ),
        "neutral": (
            "Индекс изменился на {delta}. Приму к сведению, но благополучие {gen} проверяется утром на мосту и у школьных дверей.",
            "Наш результат — {delta} к индексу. Красивую цифру в отчёте вижу; жду, когда перемены заметят родители школьников.",
        ),
    },
    "Алматы": {
        "positive": (
            "В плане есть {projects}, индекс прибавил {delta}. Хорошо. Только прошу отвечать за результат так же лично, как жители отвечают за лопнувшую трубу у себя дома.",
            "За {projects} голосую: район получил {delta} к индексу. Дальше проверим делом — старые сети и пробки красивых обещаний не читают.",
        ),
        "negative": (
            "Трубы стареют, дороги забиты, а районного проекта опять нет. Индекс изменился на {delta}. Здесь живут {pop}% горожан — им нужен ответ по существу.",
            "Нам досталось {delta} к индексу от общегородских мер. Для района со старыми сетями и пробками этого мало: за нами {pop}% жителей, отдельного проекта нет.",
        ),
        "neutral": (
            "Изменение индекса — {delta}. Я человек практический: смотреть буду на трубы зимой и на дороги в час пик.",
            "Запишем {delta} к индексу. Теперь нужны понятные сроки: старое ЖКХ не умеет ждать следующего красивого отчёта.",
        ),
    },
    "Сарыарка": {
        "positive": (
            "{projects} — за это спасибо. Индекс вырос на {delta}. Только настоящую оценку у нас ставят зимой, когда частный сектор топит печи.",
            "Наконец в плане есть {projects}: {delta} к индексу. Хочется, чтобы за этим ростом жители увидели чистый воздух, а не очередное обещание сквозь дым.",
        ),
        "negative": (
            "У нас {pop}% жителей города, и зимой они дышат дымом частного сектора. Районного проекта нет; изменение индекса на {delta} дым не развеет.",
            "Индекс изменился на {delta}, а отдельной меры для {gen} не нашлось. Наши {pop}% горожан имеют право открывать окна зимой.",
        ),
        "neutral": (
            "Индекс изменился на {delta}. Осторожно поддержу, но проверять будем зимой: дым из частного сектора виден лучше любой диаграммы.",
            "Наш результат — {delta} к индексу. Пусть это станет началом: жителям нужны зелень во дворе и воздух без запаха угля.",
        ),
    },
    "Байконур": {
        "positive": (
            "Мы редко говорим громче всех. Поэтому {projects} особенно ценим: {delta} к индексу. Теперь спокойно и по срокам доведём дело до конца.",
            "Хорошо, что вспомнили и о {loc}: {projects}. Индекс прибавил {delta}. Без громких лозунгов — просто проследим, чтобы всё сделали.",
        ),
        "negative": (
            "У нас {pop}% горожан. Мы не привыкли шуметь, но отсутствие районных проектов замечаем. Индекс изменился на {delta}; хотелось бы и своего адреса в плане.",
            "Тихий район — не значит район без запросов. Индекс изменился на {delta}, отдельного проекта нет, хотя здесь живут {pop}% горожан.",
        ),
        "neutral": (
            "Изменение индекса — {delta}. Без восторгов и без скандала: посмотрим, что из этого почувствуют жители обычных дворов.",
            "Наш результат — {delta} к индексу. Байконур держится в середине, но середина тоже заслуживает последовательной работы.",
        ),
    },
    "Нура": {
        "positive": (
            "Нас услышали: {projects}. Индекс вырос на {delta}. Для района, который привык догонять остальных по школам и транспорту, это шанс выбраться из хвоста.",
            "Сколько можно быть последними в очереди? Теперь у нас {projects} и {delta} к индексу. За этот шанс будем держаться и требовать исполнения.",
        ),
        "negative": (
            "Нура снова ждёт: районного проекта нет, индекс изменился на {delta}. Здесь {pop}% горожан, которым школы и транспорт нужны не когда-нибудь потом.",
            "Нашим {pop}% жителей предлагают общегородские меры и {delta} к индексу. А как догонять остальных по школам и транспорту без районного проекта?",
        ),
        "neutral": (
            "Индекс изменился на {delta}. Для {gen} каждый шаг важен, но мы хотим догнать город по школам и транспорту, а не привыкнуть к отставанию.",
            "Наш результат — {delta} к индексу. Запомним этот шаг, только не просите нас снова терпеливо ждать в конце очереди.",
        ),
    },
}


def _mover_headline(district, facts):
    measures = measures_by_id()
    local = district["measure_ids"]
    social = [mid for mid in local if measures[mid]["direction"] == "social"]
    where = district_case(district["district"], "loc")
    if "M7" in social and "M8" in social:
        title = f"В {where} берутся за школы и медицину"
    elif "M8" in social:
        title = f"В {where} вкладываются в здоровье жителей"
    elif "M7" in social:
        title = f"В {where} строят школу и детсад"
    elif "M9" in social:
        title = f"В {where} делают ставку на дворовый спорт"
    elif local:
        direction = measures[local[0]]["direction"]
        action = {"transport": "вкладываются в транспорт", "ecology": "берутся за экологию",
                  "safety": "делают улицы безопаснее", "services": "укрепляют городские службы"}[direction]
        title = f"В {where} {action}"
    else:
        title = f"{district['district']} лидирует по приросту индекса"
    lead = f"Индекс района вырос на {district['delta_D']:+.2f} — до {district['D_after']:.2f}."
    shares = {}
    for mid in social or local:
        shares.setdefault(facts["realized_share"][mid], []).append(measures[mid]["name"])
    if shares:
        lead += f" За {load_dataset()['horizon_quarters']} кварталов реализуется " + "; ".join(
            f"{share * 100:g}% расчётного эффекта: {measure_list(names)}" for share, names in shares.items()) + "."
    return {"title": title, "lead": lead}


def template(plan: dict, facts: dict) -> dict:
    """No LLM and no scoring here; hash selection is stable across processes."""
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).digest()
    council = []
    for index, d in enumerate(facts["districts"]):
        mood = ("positive" if d["got_district_measure"] and d["delta_D"] >= 1.5
                else "negative" if not d["got_district_measure"] and d["approval"] < 50
                else "neutral")
        variants = QUOTES[d["district"]][mood]
        quote = variants[digest[index] % len(variants)].format(
            delta=f"{d['delta_D']:+.2f}", pop=f"{d['pop_percent']:g}", projects=measure_list(d["projects"]),
            gen=district_case(d["district"], "gen"), loc=district_case(d["district"], "loc"))
        council.append({"district": d["district"], "deputy": f"Депутат от {district_case(d['district'], 'gen')}",
                        "mood": mood, "quote": quote, "delta_D": d["delta_D"], "approval": d["approval"]})
    winner = max(facts["districts"], key=lambda d: d["delta_D"])
    weakest = min(facts["districts"], key=lambda d: d["D_after"])
    problem = min(facts["crit_cells"], key=lambda c: c["value"], default=None)
    missed = [d["district"] for d in facts["districts"] if not d["got_district_measure"]]
    city = facts["approval"]["city"]
    if problem:
        issue = {"T1": "пробки не отступают", "T2": "транспорт остаётся слабым местом",
                 "E1": "не хватает зелени", "E2": "воздух остаётся в красной зоне",
                 "S1": "не хватает школ и детсадов", "S2": "не хватает первичной медпомощи",
                 "B1": "безопасность улиц остаётся под вопросом", "B2": "дороги остаются опасными",
                 "C1": "коммунальные сети остаются уязвимыми", "C2": "обращения ждут ответа"}[problem["indicator"]]
        critical = {"title": f"В {district_case(problem['district'], 'loc')} {issue}",
                    "lead": f"{indicator_label(problem['indicator'])}: {problem['value']:.2f} — ниже красной черты {facts['crit_threshold']}."}
    else:
        critical = {"title": "Ни одного района в красной зоне",
                    "lead": f"Все показатели достигли порога {facts['crit_threshold']}. Самый низкий районный индекс — "
                            f"у {district_case(weakest['district'], 'gen')}: {weakest['D_after']:.2f}. Отставание ещё предстоит сократить."}
    coverage = ({"title": f"{join_words(missed)}: районных проектов пока нет",
                 "lead": f"{facts['unserved_percent']:g}% горожан живут в районах без адресных мер. Им достаются только общегородские программы."}
                if missed else
                {"title": "Каждый район получил свой проект",
                 "lead": "План охватил все районы адресными мерами. Теперь важно довести их до результата."})
    headlines = [
        _mover_headline(winner, facts), coverage,
        {"title": "Аким удержал кресло" if facts["approval"]["reelected"] else "Горсовет обсуждает отставку",
         "lead": f"Рейтинг акима — {city:.2f} при пороге поддержки {facts['approval']['threshold']}. "
                 "Это модель политического риска; рейтинг не входит в официальный Score."},
        critical,
    ]
    if facts.get("crisis"):
        crisis = facts["crisis"]
        lead = f"Кризис отнял {crisis['crisis_cost']:.2f} балла Score. "
        if "recovered" in crisis:
            if crisis["recovered"] >= crisis["crisis_cost"]:
                recovery = "Потерю удалось возместить."
            elif crisis["recovered"] > 0:
                recovery = "Потерю удалось возместить лишь частично."
            else:
                recovery = "Замена не возместила потерю."
            lead += f"Замена изменила результат на {crisis['recovered']:+.2f}. {recovery}"
        else:
            lead += (f"Результат снизился с {crisis['score_before']:.2f} до {crisis['new_baseline_score']:.2f}. "
                     "Меру пока не заменили — восстановления за счёт замены ещё нет.")
        headlines.append({"title": crisis["event"]["title"], "lead": lead})
    rank_label = "Место исходного плана до кризиса" if facts.get("crisis") else "Место плана"
    fairness = (f"Но {facts['unserved_percent']:g}% горожан остались без районных проектов: для них успех города пока слишком общий. "
                if missed else "Адресный проект достался каждому району — справедливое начало, но ещё не равный результат. ")
    return {"council": council, "newspaper": {
        "masthead": "Астана Times", "date": "IV квартал 2028", "headlines": headlines,
        "editorial": (f"При расходах {facts['cost']} ед. бюджета город получает Score {facts['score']:.2f}; "
                      f"{rank_label.lower()} — {number(facts['rank'])} из {number(facts['total_valid'])}. "
                      + fairness + f"Индекс {district_case(winner['district'], 'gen')} вырос на {winner['delta_D']:+.2f}, "
                      "и редакция считает этот рост поводом требовать таких же шансов для остальных. "
                      "Город нельзя оценивать только из окна самого удачного проекта."),
        "crit_sidebar": [dict(c) for c in facts["crit_cells"]]}}
