"""Grounded explanations. The engine owns scoring; this module only presents it."""

from collections import OrderedDict
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
import hashlib
import json
import logging
import math
import os
import re
from threading import RLock
from time import monotonic

from openai import OpenAI

from engine import grading, promise, stress
from engine.approval import approval
from engine.model import load_dataset, load_events, measures_by_id, normalize_plan, plan_cost
from engine.optimize import optimize_info, what_if
from engine.score import evaluate
from engine.shock import resolve, shock, shocked_base
from engine.validate import validate
from .narrative import template
from .prompts import ANALYST_SYSTEM, NARRATOR_SYSTEM, SWAP_SYSTEM

LOG = logging.getLogger(__name__)
_CACHE = OrderedDict()
_CACHE_LOCK = RLock()
_CACHE_LIMIT = 256
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_GROUP = re.compile(r"(?<!\d)\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?!\d)")


def _leaves(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _leaves(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _leaves(child)
    else:
        yield value


def _percentages(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "realized_share" and isinstance(child, dict):
                yield from (n * 100 for n in child.values())
            yield from _percentages(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _percentages(child)


def guard(payload: dict, facts: list) -> list[str]:
    """Check every string value against numeric leaves, with written precision.

    This is a provenance check for numbers, not a semantic fact checker. Model
    strings and model numeric fields never become their own supporting facts.
    """
    data = load_dataset()
    sources = [data, facts, 694395, *range(2026, 2031),
               *[d["pop"] * 100 for d in data["districts"]], *_percentages(facts)]
    allowed = set()
    for value in _leaves(sources):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            n = Decimal(str(value))
            for number in (n, abs(n)):
                allowed.add(number)
                allowed.update(number.quantize(Decimal(1).scaleb(-dp)) for dp in range(3))
    rejected, rounded = [], {}
    identifiers = re.compile(r"\b(?:" + "|".join(re.escape(m["id"]) for m in data["measures"]) + r")\b")
    for value in _leaves(payload):
        if not isinstance(value, str):
            continue
        value = _GROUP.sub(lambda m: re.sub(r"[ \u00a0\u202f]", "", m[0]), value)
        value = re.sub(r"(?<=\d),(?=\d)", ".", value.replace("−", "-"))
        # Dataset IDs such as M11 name a measure, not a numerical assertion.
        # Strip only complete, known IDs; a bare invented 11 still gets checked.
        value = identifiers.sub("", value)
        for token in _NUMBER.findall(value):
            n = Decimal(token)
            if "." not in token and abs(n) < 10:
                continue
            dp = len(token.partition(".")[2])
            if dp not in rounded:
                with localcontext() as ctx:
                    ctx.prec = max(32, dp + 20)
                    unit = Decimal(1).scaleb(-dp)
                    rounded[dp] = {v.quantize(unit, rounding=ROUND_HALF_EVEN) for v in allowed}
            if n not in rounded[dp] and token not in rejected:
                rejected.append(token)
    return rejected


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _plan(plan):
    valid, reason = validate(plan)
    if not valid:
        raise ValueError(reason)
    return {"decisions": sorted(normalize_plan(plan), key=lambda d: d["measure"])}


def _compact_score(plan):
    score = evaluate(plan)
    score.pop("timeline", None)
    for district in score["districts"].values():
        district.pop("before", None)
        district.pop("after", None)
    return score


def _optimize(plan):
    return {k: v for k, v in optimize_info(plan).items() if k != "pareto"}


def _stress(plan):
    result = stress.stress_test(plan)
    return {"scenarios": [{k: row[k] for k in (
                "event_id", "title", "score", "loss", "insurance")}
                for row in result["scenarios"]],
            "worst_event": result["worst_event"], "crisis_proof_plan": result["crisis_proof_plan"]}


def _grade(plan):
    result = grading.grade(plan)
    return {"moves": [{k: move[k] for k in (
                "measure", "district", "grade", "label", "loss", "best_alternative")}
                for move in result["moves"]], "accuracy": result["accuracy"]}


def _context(plan):
    score, political, opt = _compact_score(plan), approval(plan), _optimize(plan)
    best_approval = approval(opt["best"]["plan"])
    missed = [d for d in load_dataset()["districts"]
              if not best_approval["districts"][d["name"]]["got_district_measure"]]
    return {"score": score, "approval": political, "optimizer": opt,
            "stress": _stress(plan), "grading": _grade(plan),
            "best_approval": best_approval,
            "same_cost_approval": approval(_plan(opt["best_at_same_cost"]["plan"]))["city"],
            "best_unserved_percent": round(sum(d["pop"] for d in missed) * 100, 2)}


def _offline_analyze(plan, facts):
    from .narrative import district_case, indicator_label, join_words, number

    s, a, o = facts["score"], facts["approval"], facts["optimizer"]
    data = load_dataset()
    measures = measures_by_id()

    def measure(mid):
        return f"{mid} «{measures[mid]['name']}»"

    def location(name):
        return f"в {district_case(name, 'loc')}" if name else "по всему городу"

    def cell(c):
        return f"{indicator_label(c['indicator'])} {location(c['district'])}"

    contributions = sorted(s["contributions"], key=lambda c: c["marginal"], reverse=True)
    strengths = [f"Мера {measure(c['measure'])} {location(c['district'])} "
                 f"даёт {c['marginal']:+.2f} к Score" + (" — наибольший вклад в этом плане." if i == 0 else ".")
                 for i, c in enumerate(contributions[:2])]
    if s["resolved_crit_cells"]:
        strengths.append("Из красной зоны вышли показатели: " + "; ".join(
            f"{cell(c)}: {c['before']:.2f} → {c['after']:.2f}"
            for c in s["resolved_crit_cells"]) + ".")
    if s["synergies_triggered"]:
        strengths.append("Меры усиливают друг друга: " + "; ".join(
            f"{join_words(measure(mid) for mid in c['pair'])} {location(c['district'])}"
            for c in s["synergies_triggered"]) + ".")
    risks = [f"В красной зоне остаётся {cell(c)}: {c['value']:.2f} "
             f"при пороге {data['crit_threshold']}." for c in s["crit_cells"]]
    lagged = [d for d in plan["decisions"] if measures[d["measure"]]["lag"] >= 3]
    if lagged:
        risks.append(f"За {data['horizon_quarters']} кварталов долгие проекты дадут лишь часть расчётного эффекта: " + "; ".join(
            f"{measure(d['measure'])} {location(d['district'])} — {s['realized_share'][d['measure']] * 100:g}% "
            f"(задержка запуска — {measures[d['measure']]['lag']} кв.)" for d in lagged) + ".")
    if a["city"] < a["threshold"]:
        risks.append(f"Рейтинг акима — {a['city']:.2f} при пороге поддержки {a['threshold']}: есть риск отставки. "
                     "Рейтинг отражает политический риск и не входит в Score.")
    missed = [d for d in data["districts"] if not a["districts"][d["name"]]["got_district_measure"]]
    if missed:
        risks.append("Районные проекты не предусмотрены: " + "; ".join(
            f"в {d['cases']['loc']} — {d['pop'] * 100:g}% горожан" for d in missed)
            + ". Эти районы получают только общегородские меры.")
    worst = next(row for row in facts["stress"]["scenarios"]
                 if row["event_id"] == facts["stress"]["worst_event"])
    risk = f"Худший отдельный кризис — «{worst['title']}»: потеря {worst['loss']:.2f} балла."
    insurance = worst["insurance"]
    if insurance:
        risk += (f" Страхующая замена: {insurance['out']} на {insurance['in']['measure']} "
                 f"{location(insurance['in']['district'])}; изменение после кризиса — "
                 f"{insurance['recovered']:+.2f} балла.")
    else:
        risk += " Допустимой страхующей замены нет."
    risks.append(risk)
    review = facts["grading"]
    if all(move["grade"] == "best" for move in review["moves"]):
        strengths.append(f"Все ходы лучшие при остальных фиксированных решениях; точность — {review['accuracy']:.2f}%.")
    else:
        candidates = [move for move in review["moves"] if move["grade"] not in ("best", "sacrifice")]
        move = max(candidates or review["moves"], key=lambda m: m["loss"])
        alternative = move["best_alternative"]
        line = (f"Разбор ходов: {move['label']} — {move['measure']} {location(move['district'])}; "
                f"упущено {move['loss']:.2f} балла, точность — {review['accuracy']:.2f}%.")
        if alternative:
            line += f" Альтернатива: {alternative['measure']} {location(alternative['district'])}."
        if move["grade"] == "sacrifice":
            line += " Потеря сохраняет переизбрание; более сильной замены с переизбранием нет."
        risks.append(line)
    movers = sorted(a["districts"], key=lambda name: a["districts"][name]["delta_D"], reverse=True)[:2]
    consequences = [f"Индекс {district_case(name, 'gen')}: {s['districts'][name]['D_before']:.2f} → "
                    f"{s['districts'][name]['D_after']:.2f}." for name in movers]
    low = contributions[-1]
    consequences.append(f"Наименьший вклад у меры {measure(low['measure'])}: {low['marginal']:+.2f} Score при стоимости "
                        f"{low['cost']}; отдача на единицу бюджета — {low['marginal_per_cost']:.2f} Score.")
    best_targets = sorted({d["district"] for d in o["best"]["plan"]["decisions"] if d["district"]})
    tradeoffs = [
        f"Максимальный Score — {o['best']['score']:.2f}: все районные меры идут в "
        f"{join_words(district_case(name, 'acc') for name in best_targets)}; "
        f"{facts['best_unserved_percent']:g}% жителей получают только общегородские меры. "
        f"Рейтинг акима при таком плане — {facts['best_approval']['city']:.2f}, при вашем — {a['city']:.2f}. Рейтинг не входит в Score.",
        f"При расходах не выше {s['cost']} лучший Score — {o['best_at_same_cost']['score']:.2f} "
        f"за {o['best_at_same_cost']['cost']}; сейчас — {s['score']:.2f}.",
        f"Не использовано {s['remaining']} из {data['budget']} единиц бюджета; остаток не даёт бонуса.",
    ]
    if o["best_at_same_cost"]["score"] > s["score"]:
        rec = o["best_at_same_cost"]
        x = facts["same_cost_approval"]
        why = (f"При расходах не выше текущих {s['cost']} можно получить Score {rec['score']:.2f} "
               f"вместо {s['score']:.2f}; стоимость рекомендации — {rec['cost']}."
               f" Рейтинг акима при нём — {x:.2f}{' (ниже порога переизбрания)' if x < a['threshold'] else ''}.")
    elif o["balanced"] and _plan(o["balanced"]["plan"]) != plan:
        rec = o["balanced"]
        why = (f"В своём бюджете план уже оптимален по Score. Альтернатива с поддержкой не ниже "
               f"{a['threshold']}: Score {rec['score']:.2f}, рейтинг {rec['approval']:.2f}, стоимость {rec['cost']}.")
    else:
        rec = {"plan": plan}
        why = "План уже оптимален в своём бюджете."
    recommended = _plan(rec["plan"])
    return {"summary": f"План даёт Score {s['score']:.2f} — {s['delta']:+.2f} к исходным {s['baseline']:.2f}. "
            f"Он занимает место {number(o['rank'])} из {number(o['total_valid'])} и опережает {o['percentile']:.2f}% допустимых планов. "
            f"Самый низкий районный индекс — у {district_case(s['d_min_district'], 'gen')}: {s['d_min']:.2f}.",
            "strengths": strengths, "risks": risks, "consequences": consequences, "tradeoffs": tradeoffs,
            "recommendation": {"plan": recommended, "why": why, "expected_score": evaluate(recommended)["score"]}}


_DECISION_SCHEMA = {"type": "object", "properties": {
    "measure": {"type": "string", "enum": list(measures_by_id())},
    "district": {"type": ["string", "null"], "enum": [d["name"] for d in load_dataset()["districts"]] + [None]},
}, "required": ["measure", "district"], "additionalProperties": False}
_PLAN_SCHEMA = {"type": "object", "properties": {"decisions": {"type": "array", "items": _DECISION_SCHEMA}},
                "required": ["decisions"], "additionalProperties": False}
_SWAP_SCHEMA = {"type": "object", "properties": {"out": {"type": "string"}, "in": _DECISION_SCHEMA},
                "required": ["out", "in"], "additionalProperties": False}
_PROMISE_SCHEMA = {"anyOf": [
    {"type": "object", "properties": {"type": {"type": "string", "enum": [kind]}, **fields},
     "required": ["type", *required], "additionalProperties": False}
    for kind, fields, required in [
        ("include", _DECISION_SCHEMA["properties"], ["measure"]),
        ("exclude", {"measure": _DECISION_SCHEMA["properties"]["measure"]}, ["measure"]),
        ("district_project", {"district": {"type": "string", "enum": [d["name"] for d in load_dataset()["districts"]]}}, ["district"]),
        ("min_districts", {"value": {"type": "integer", "minimum": 0}}, ["value"]),
        ("max_cost", {"value": {"type": "number", "minimum": 0}}, ["value"]),
        ("min_approval", {"value": {"type": "number"}}, ["value"]),
        ("no_critical", {}, []),
        ("min_district_delta", {"district": {"type": "string", "enum": [d["name"] for d in load_dataset()["districts"]]},
                                "value": {"type": "number"}}, ["district", "value"]),
    ]
]}
TOOLS = [{"type": "function", "function": {"name": name, "description": desc,
          "parameters": {"type": "object", "properties": {"plan": _PLAN_SCHEMA, **({"swap": _SWAP_SCHEMA} if name == "what_if" else {})},
                         "required": ["plan", "swap"] if name == "what_if" else ["plan"], "additionalProperties": False}}}
         for name, desc in [("score", "Score, районные индексы, критические ячейки и вклады мер."),
                            ("validate", "Проверка правил и стоимости плана."),
                            ("optimize_same_budget", "Оптимумы, ранг и политически сбалансированный план."),
                            ("what_if", "Результат замены ровно одной меры."),
                            ("remove_one", "Вклад каждой меры при её удалении."),
                            ("stress_test", "Уязвимость к кризисам, страхующие замены и устойчивый план."),
                            ("grade_plan", "Оценки ходов, потери, лучшие альтернативы и точность плана.")]]
TOOLS.append({"type": "function", "function": {
    "name": "price_of_promise", "description": "Цена обещаний и справедливости в баллах Score; лучший план, выполняющий обещания.",
    "parameters": {"type": "object", "properties": {
        "promises": {"type": "array", "items": _PROMISE_SCHEMA}},
        "required": ["promises"], "additionalProperties": False}}})


def _tool(name, args):
    if name == "price_of_promise":
        return promise.price(args["promises"])
    plan = args["plan"]
    if name == "stress_test":
        return _stress(plan)
    if name == "grade_plan":
        return _grade(plan)
    if name == "score":
        return _compact_score(plan)
    if name == "validate":
        valid, reason = validate(plan)
        try:
            cost = plan_cost(plan)
        except (ValueError, KeyError, TypeError):
            cost = None
        return {"valid": valid, "reason": reason, "cost": cost}
    if name == "optimize_same_budget":
        return _optimize(plan)
    if name == "what_if":
        return what_if(plan, args["swap"])
    if name == "remove_one":
        return evaluate(plan)["contributions"]
    raise ValueError("Неизвестный инструмент")


_OFFLINE = ("offline", "", "", "")
_DEFAULT_MODEL = {"openai": "gpt-5-mini", "nvidia": "nvidia/nemotron-3-super-120b-a12b"}
# Keys that failed with an auth/model error are skipped for the rest of the process.
# Stored as fingerprints only; the key itself never leaves os.environ.
_DEAD = set()


def _fingerprint(key):
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _env_keys(prefix):
    keys = []
    for chunk in (os.getenv(prefix + "_API_KEYS", ""), os.getenv(prefix + "_API_KEY", "")):
        for key in chunk.split(","):
            key = key.strip()
            if key and key not in keys:
                keys.append(key)
    return keys


def _providers():
    """Ordered fallback chain: every key of every provider in LLM_PROVIDER (comma list).

    Returns [(provider, key, model, base, label)], label like "openai#2" (safe to show).
    An empty list means offline.
    """
    chain = []
    for provider in os.getenv("LLM_PROVIDER", "offline").lower().split(","):
        provider = provider.strip()
        if provider not in _DEFAULT_MODEL:
            continue
        prefix = provider.upper()
        model = os.getenv(prefix + "_MODEL") or _DEFAULT_MODEL[provider]
        base = "https://api.openai.com/v1" if provider == "openai" else (
            os.getenv("NVIDIA_BASE_URL") or "https://integrate.api.nvidia.com/v1")
        for index, key in enumerate(_env_keys(prefix), 1):
            if _fingerprint(key) not in _DEAD:
                chain.append((provider, key, model, base, f"{provider}#{index}"))
    return chain


def _chain_id(chain):
    """Cache namespace for a chain (no raw keys)."""
    if not chain:
        return _OFFLINE
    return (",".join(c[4] for c in chain), "".join(c[1] for c in chain),
            ",".join(c[2] for c in chain), ",".join(c[3] for c in chain))


def _client_factory(api_key, base_url):
    return OpenAI(api_key=api_key, base_url=base_url, timeout=float(os.getenv("LLM_TIMEOUT", "60")), max_retries=0)


def _cache_key(function, plan, lang, config, event_id=None, swap=None):
    provider, key, model, base = config
    # Configuration namespaces keep an offline click from hiding a later online run.
    return (function, _json(plan), event_id, _json(swap), lang, provider, model, base,
            hashlib.sha256(key.encode()).hexdigest())


def _cached(key):
    with _CACHE_LOCK:
        if key not in _CACHE:
            return None
        _CACHE.move_to_end(key)
        return deepcopy(_CACHE[key])


def _save(key, result):
    with _CACHE_LOCK:
        _CACHE[key] = deepcopy(result)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)
    return result


def _parse(content):
    if not isinstance(content, str):
        raise ValueError("Пустой ответ модели")
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", content):
        try:
            result, _ = decoder.raw_decode(content[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict):
            return result
    raise ValueError("Модель не вернула JSON")


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _complete(client, model, messages, deadline, use_tools=False, force_tool=None):
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("LLM deadline exceeded")
    kwargs = {"model": model, "messages": messages, "timeout": remaining,
              "response_format": {"type": "json_object"}}
    if use_tools:
        # The first agent turn is forced into a real tool call, so the analysis always
        # starts from the engine (rank, best plan in budget), not from the prompt alone.
        choice = {"type": "function", "function": {"name": force_tool}} if force_tool else "auto"
        kwargs.update(tools=TOOLS, tool_choice=choice, parallel_tool_calls=False)
    if str(model).startswith(("gpt-5", "o3", "o4")):
        kwargs["reasoning_effort"] = "low"  # judges' patience > eloquence
    # Unsupported response_format and all other provider failures fall back offline.
    result = client.chat.completions.create(**kwargs)
    if monotonic() > deadline:
        raise TimeoutError("LLM deadline exceeded")
    return _get(_get(result, "choices")[0], "message")


def _analyst_answer(payload, fallback):
    if not isinstance(payload.get("summary"), str):
        raise ValueError("Отсутствует summary")
    result = {"summary": payload["summary"]}
    for key in ("strengths", "risks", "consequences", "tradeoffs"):
        if not isinstance(payload.get(key), list) or not all(isinstance(x, str) for x in payload[key]):
            raise ValueError("Неверная структура ответа")
        result[key] = payload[key]
    rec = payload.get("recommendation")
    if not isinstance(rec, dict) or not validate(rec.get("plan"))[0]:
        rec = deepcopy(fallback["recommendation"])
    else:
        rec = {"plan": _plan(rec["plan"]), "why": rec.get("why")}
        if not isinstance(rec["why"], str):
            rec["why"] = "Рекомендованный план проверен движком."
    rec["expected_score"] = evaluate(rec["plan"])["score"]
    result["recommendation"] = rec
    return result


def _narrative_answer(payload, facts):
    council, paper = payload.get("council"), payload.get("newspaper")
    if not isinstance(council, list) or len(council) != len(facts["districts"]) or not isinstance(paper, dict):
        raise ValueError("Неверная структура газеты")
    by_name = {d.get("district"): d for d in council if isinstance(d, dict)}
    if set(by_name) != {d["district"] for d in facts["districts"]}:
        raise ValueError("Неверный состав совета")
    entries = []
    for d in facts["districts"]:
        entry = by_name[d["district"]]
        if entry.get("mood") not in ("positive", "neutral", "negative") or not all(
                isinstance(entry.get(k), str) for k in ("deputy", "quote")):
            raise ValueError("Неверная реплика депутата")
        entries.append({k: entry[k] for k in ("district", "deputy", "mood", "quote")})
        entries[-1].update(delta_D=d["delta_D"], approval=d["approval"])
    headlines = paper.get("headlines")
    if (not isinstance(headlines, list) or not 4 <= len(headlines) <= 5
            or not all(isinstance(h, dict) and all(isinstance(h.get(k), str) for k in ("title", "lead")) for h in headlines)
            or not isinstance(paper.get("editorial"), str)):
        raise ValueError("Неверная структура статей")
    return {"council": entries, "newspaper": {"masthead": "Астана Times", "date": "IV квартал 2028",
            "headlines": [{k: h[k] for k in ("title", "lead")} for h in headlines],
            "editorial": paper["editorial"], "crit_sidebar": deepcopy(facts["crit_cells"])}}


def _explain(config, messages, facts, fallback, finalize, agent=False, deadline=None):
    provider, key, model, base = config
    offline = {**deepcopy(fallback), "provider": "offline", "grounded": True,
               "guard": {"attempts": 0, "rejected": []}}
    trace, rejected, attempts = [], [], 0
    if agent:
        offline["tool_trace"] = []
    if provider == "offline":
        return offline
    client = None
    try:
        if deadline is None:
            deadline = monotonic() + float(os.getenv("LLM_TIMEOUT", "60"))
        client = _client_factory(api_key=key, base_url=base)
        while True:
            message = _complete(client, model, messages, deadline, use_tools=agent and len(trace) < 6,
                                force_tool="optimize_same_budget" if agent and not trace else None)
            calls = _get(message, "tool_calls") or []
            if not calls:
                content = _get(message, "content")
                break
            if not agent:
                raise ValueError("Неожиданный вызов инструмента")
            serialized = [{"id": _get(c, "id"), "type": "function", "function": {
                "name": _get(_get(c, "function"), "name"), "arguments": _get(_get(c, "function"), "arguments")}} for c in calls]
            messages.append({"role": "assistant", "content": _get(message, "content"), "tool_calls": serialized})
            for call in serialized:
                if len(trace) >= 6:
                    raise ValueError("Превышен лимит вызовов инструментов")
                name = call["function"]["name"]
                args = json.loads(call["function"]["arguments"])
                if not isinstance(args, dict):
                    raise ValueError("Неверные аргументы инструмента")
                trace.append({"tool": name, "args": deepcopy(args)})
                result = _tool(name, args)
                facts.append(result)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": _json(result)})
            if len(trace) == 6:
                messages.append({"role": "user", "content": "Лимит инструментов исчерпан. Верни финальный JSON."})
        for attempts in (1, 2):
            payload = _parse(content)
            offenders = guard(payload, facts)
            if not offenders:
                answer = finalize(payload)
                answer.update(provider=provider, grounded=True, guard={"attempts": attempts, "rejected": rejected})
                if agent:
                    answer["tool_trace"] = trace
                return answer
            rejected = list(dict.fromkeys(rejected + offenders))
            if attempts == 2:
                LOG.warning("Number guard rejected %s after two attempts: %s", provider, rejected)
                offline.update(grounded=False, guard={"attempts": 2, "rejected": rejected})
                return offline
            messages.extend([{"role": "assistant", "content": content}, {"role": "user", "content":
                f"Ты использовал числа, которых нет в результатах инструментов: {offenders}. Перепиши ответ, используя только числа из инструментов."}])
            message = _complete(client, model, messages, deadline)
            content = _get(message, "content")
    except Exception as exc:
        # Do not echo SDK exception bodies: they can contain credentials or request data.
        offline["fallback_reason"] = f"LLM unavailable: {type(exc).__name__}"
        offline["_error"] = (getattr(exc, "status_code", None), type(exc).__name__)
        offline["guard"] = {"attempts": attempts, "rejected": rejected}
        if rejected:
            offline["grounded"] = False
        return offline
    finally:
        if client is not None and callable(getattr(client, "close", None)):
            try:
                client.close()
            except Exception:
                pass


def _explain_chain(chain, messages, facts, fallback, finalize, agent=False):
    """Try each key in order under one shared deadline; offline template if all fail."""
    if not chain:
        return _explain(_OFFLINE, messages, facts, fallback, finalize, agent)
    deadline = monotonic() + float(os.getenv("LLM_TIMEOUT", "60"))
    failures = []
    for provider, key, model, base, label in chain:
        result = _explain((provider, key, model, base), deepcopy(messages), list(facts),
                          fallback, finalize, agent, deadline=deadline)
        error = result.pop("_error", None)
        if error is None or result.get("guard", {}).get("rejected"):
            if failures:
                result["fallback_chain"] = failures
            return result
        status, name = error
        failures.append(f"{label}: {name}" + (f" {status}" if status else ""))
        if status in (401, 403, 404, 410):
            _DEAD.add(_fingerprint(key))
        if monotonic() >= deadline:
            break
    LOG.warning("All LLM providers failed: %s", failures)
    result["fallback_reason"] = "LLM unavailable: " + "; ".join(failures)
    return result


def analyze(plan: dict, lang: str = "ru") -> dict:
    plan = _plan(plan)
    chain = _providers()
    key = _cache_key("analyze", plan, lang, _chain_id(chain))
    cached = _cached(key)
    if cached is not None:
        return cached
    context = _context(plan)
    fallback = _offline_analyze(plan, context)
    messages = [{"role": "system", "content": ANALYST_SYSTEM},
                {"role": "user", "content": _json({"plan": plan, "lang": lang, "dataset": load_dataset(), **context})}]
    result = _explain_chain(chain, messages, [context], fallback,
                            lambda payload: _analyst_answer(payload, fallback), agent=True)
    return result if "fallback_reason" in result else _save(key, result)


def swap_comment(result: dict) -> dict:
    """Explain an engine-resolved swap; all numerical facts come from resolve."""
    from .narrative import district_case

    facts = {k: result[k] for k in (
        "crisis_cost", "recovered", "swap_was_optimal", "best_possible_swap")}
    recovered = facts["recovered"]
    comment = f"Кризис стоил {facts['crisis_cost']:.2f} балла. "
    if recovered > 0:
        comment += f"Ваша замена вернула {recovered:.2f} балла"
    elif recovered < 0:
        comment += f"Ваша замена дополнительно снизила оценку на {abs(recovered):.2f} балла"
    else:
        comment += f"Ваша замена не изменила оценку: {recovered:.2f} балла"
    if facts["swap_was_optimal"]:
        comment += " — это лучший возможный ход при обязательной замене."
    else:
        best = facts["best_possible_swap"]
        target = best["in"]["district"]
        where = f"в {district_case(target, 'loc')}" if target else "по всему городу"
        comment += (f"; лучше было заменить {best['out']} на {best['in']['measure']} {where}: "
                    f"{best['gain']:+.2f} балла относительно оценки после кризиса.")

    def finalize(payload):
        if not isinstance(payload.get("comment"), str) or not payload["comment"].strip():
            raise ValueError("Отсутствует комментарий к замене")
        return {"comment": payload["comment"]}

    messages = [{"role": "system", "content": SWAP_SYSTEM},
                {"role": "user", "content": _json(facts)}]
    answer = _explain_chain(_providers(), messages, [facts], {"comment": comment}, finalize)
    return {k: answer[k] for k in ("comment", "provider", "grounded")}


def _narrative_facts(plan, event_id=None, swap=None):
    if swap is not None and event_id is None:
        raise ValueError("Для замены нужно указать event_id")
    active_plan, base, crisis = plan, None, None
    if event_id is not None:
        events = load_events()
        index = next((i for i, event in enumerate(events) if event["id"] == event_id), None)
        if index is None:
            raise ValueError(f"Неизвестное событие: {event_id}")
        event = events[index]
        base = shocked_base(event)
        if swap is not None:
            resolved = resolve(plan, event_id, swap)
            active_plan = resolved["new_plan"]
            crisis = {"event": {k: event[k] for k in ("id", "title", "district")}, **resolved}
        else:
            shocked = shock(plan, index)
            crisis = {**shocked, "event": {k: event[k] for k in ("id", "title", "district")},
                      "crisis_cost": round(shocked["score_before"] - shocked["new_baseline_score"], 2)}
    s, a, o = evaluate(active_plan, base), approval(active_plan, base), _optimize(plan)
    measures = measures_by_id()
    districts = [{"district": d["name"], "profile": d["profile"], "pop_percent": d["pop"] * 100,
                  **a["districts"][d["name"]], "D_after": s["districts"][d["name"]]["D_after"],
                  "measure_ids": [c["measure"] for c in active_plan["decisions"] if c["district"] == d["name"]],
                  "projects": [measures[c["measure"]]["name"] for c in active_plan["decisions"] if c["district"] == d["name"]]}
                 for d in load_dataset()["districts"]]
    ranked = sorted(s["contributions"], key=lambda c: c["marginal"], reverse=True)
    return {"districts": districts, "crit_cells": s["crit_cells"], "crit_threshold": load_dataset()["crit_threshold"],
            "unserved_percent": round(sum(d["pop_percent"] for d in districts if not d["got_district_measure"]), 2),
            "score": s["score"], "cost": s["cost"], "rank": o["rank"], "total_valid": o["total_valid"],
            "approval": a, "top_measures": ranked[:2], "bottom_measures": ranked[-2:],
            "realized_share": s["realized_share"], "crisis": crisis}


def narrative(plan: dict, lang: str = "ru", event_id: str | None = None, swap: dict | None = None) -> dict:
    plan = _plan(plan)
    chain = _providers()
    key = _cache_key("narrative", plan, lang, _chain_id(chain), event_id, swap)
    cached = _cached(key)
    if cached is not None:
        return cached
    facts = _narrative_facts(plan, event_id, swap)
    fallback = template(plan, facts)
    messages = [{"role": "system", "content": NARRATOR_SYSTEM},
                {"role": "user", "content": _json({"plan": plan, "lang": lang, "facts": facts})}]
    result = _explain_chain(chain, messages, [facts], fallback, lambda payload: _narrative_answer(payload, facts))
    # A failed online run is not cached, so the next click retries the providers.
    return result if "fallback_reason" in result else _save(key, result)


def brief(plan: dict) -> str:
    from .narrative import district_case, indicator_label, join_words

    plan = _plan(plan)
    context = _context(plan)
    analysis = _offline_analyze(plan, context)
    s, a = context["score"], context["approval"]
    measures = measures_by_id()
    verdict = "поддержки хватает для переизбрания" if a["reelected"] else "поддержка ниже порога переизбрания"
    lines = ["# Кабинет акима — решение команды", "",
             f"**Score {s['score']:.2f}; рейтинг акима {a['city']:.2f} — {verdict}.**", "",
             f"Бюджет: **{s['cost']} из {load_dataset()['budget']}**; остаток — **{s['remaining']}**. "
             f"Горизонт расчёта — {load_dataset()['horizon_quarters']} кварталов.", "",
             "## Что делаем", "", "| Мера | Название | Где | Стоимость | Задержка, кв. |",
             "|---|---|---|---:|---:|"]
    for d in plan["decisions"]:
        m = measures[d["measure"]]
        where = f"В {district_case(d['district'], 'loc')}" if d["district"] else "Весь город"
        lines.append(f"| {m['id']} | «{m['name']}» | {where} | {m['cost']} | {m['lag']} |")
    lines += ["", "## Что получаем", "", analysis["summary"], "",
              "| Район | Индекс до | Индекс после |", "|---|---:|---:|"]
    lines += [f"| {name} | {d['D_before']:.2f} | {d['D_after']:.2f} |" for name, d in s["districts"].items()]
    lines += ["", "**Сильные стороны**", "", *[f"- {text}" for text in analysis["strengths"][:2]]]
    if s["resolved_crit_cells"]:
        lines.append("- Из красной зоны вышли: " + "; ".join(
            f"{indicator_label(c['indicator'])} в {district_case(c['district'], 'loc')}: "
            f"{c['before']:.2f} → {c['after']:.2f}" for c in s["resolved_crit_cells"]) + ".")
    if s["synergies_triggered"]:
        lines.append("- Меры усиливают друг друга: " + "; ".join(
            f"{join_words(c['pair'])} в {district_case(c['district'], 'loc')}"
            for c in s["synergies_triggered"]) + ".")
    lines += ["", "**Риски**", ""]
    lines += [f"- {indicator_label(c['indicator'])} в {district_case(c['district'], 'loc')}: "
              f"{c['value']:.2f} — ниже порога {load_dataset()['crit_threshold']}." for c in s["crit_cells"]]
    lagged = [d for d in plan["decisions"] if measures[d["measure"]]["lag"] >= 3]
    if lagged:
        lines.append("- Долгие проекты дадут лишь часть расчётного эффекта: " + "; ".join(
            f"{d['measure']} — {s['realized_share'][d['measure']] * 100:g}%" for d in lagged) + ".")
    missed = [d for d in load_dataset()["districts"] if not a["districts"][d["name"]]["got_district_measure"]]
    if missed:
        lines.append("- Без районных проектов: " + join_words(
            f"{d['name']} ({d['pop'] * 100:g}% горожан)" for d in missed) + ".")
    lines.append(f"- Порог переизбрания — {a['threshold']}. Рейтинг акима отражает политический риск и не входит в Score.")
    rec = analysis["recommendation"]
    lines += ["", "## Рекомендация", "", rec["why"], "",
              "- Состав: " + "; ".join(f"{d['measure']} — " + (
                  f"в {district_case(d['district'], 'loc')}" if d["district"] else "весь город")
                  for d in rec["plan"]["decisions"]) + ".",
              "- Компромисс: " + analysis["tradeoffs"][0],
              "", "Все числа посчитаны движком `engine/`; ИИ только объясняет."]
    return "\n".join(lines) + "\n"
