"""Entire analyst suite runs without credentials or network access."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from api import agent
from engine.approval import approval
from engine.model import district_names, load_dataset, load_events
from engine.optimize import optimize_info
from engine.score import evaluate
from engine.shock import resolve, shocked_base


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    agent._CACHE.clear()
    def no_network(**kwargs):
        pytest.fail("Unexpected real OpenAI client construction")
    monkeypatch.setattr(agent, "OpenAI", no_network)


@pytest.fixture
def doc():
    return deepcopy(load_dataset()["reference"]["doc_example"])


def _plans(doc):
    cheapest = {"decisions": [{"measure": m, "district": None if m == "M12" else "Нура"}
                               for m in ("M9", "M11", "M10", "M12", "M4")]}
    return [doc, optimize_info(doc)["best"]["plan"], cheapest]


def _message(payload=None, calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else payload,
        tool_calls=calls))])


def _call(name, args, index=0):
    return SimpleNamespace(id=f"call_{index}", type="function", function=SimpleNamespace(
        name=name, arguments=json.dumps(args, ensure_ascii=False)))


class FakeClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []
        self.closed = False
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        response = next(self.replies)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


def _online(monkeypatch, replies, provider="openai"):
    client = FakeClient(replies)
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv(provider.upper() + "_API_KEY", "test-key-only")
    monkeypatch.setattr(agent, "_client_factory", lambda **kwargs: client)
    return client


def _answer(doc):
    return {"summary": "Score 56.54, прирост +3.98.", "strengths": ["Социальная сфера Нуры получает поддержку."],
            "risks": ["Районы получают разное внимание."], "consequences": [], "tradeoffs": [],
            "recommendation": {"plan": deepcopy(doc), "why": "Проверенный план.", "expected_score": 9999}}


def test_offline_analyze(doc):
    result = agent.analyze(doc)
    assert set(result) == {"summary", "strengths", "risks", "consequences", "tradeoffs", "recommendation",
                           "provider", "grounded", "guard", "tool_trace"}
    assert result["grounded"] is True
    assert result["provider"] == "offline"
    assert result["tool_trace"] == []
    assert result["guard"] == {"attempts": 0, "rejected": []}
    rec = result["recommendation"]
    assert rec["expected_score"] == evaluate(rec["plan"])["score"]
    assert "56.54" in result["summary"]
    assert "559" in result["summary"]  # Engine rank, not the illustrative PLAN.md rank.


def test_guard_normalization(doc):
    facts = [evaluate(doc), approval(doc), optimize_info(doc)]
    assert agent.guard({"text": "Score вырастет до 61.3"}, facts) == ["61.3"]
    assert agent.guard({"nested": ["56.54; +3.98; 694 395; 694\u00a0395; 694\u202f395; 27%",
                                     {"text": "56,54; +3,98; −1; 8 кварталов; M11"}]}, facts) == []
    assert agent.guard({"text": "−123.45 и +987.65"}, facts) == ["-123.45", "987.65"]
    assert agent.guard({"text": "лаг 3, доля 62.5%"}, facts) == []
    assert agent.guard({"text": "8.123456"}, facts) == ["8.123456"]


def test_guard_rounding_and_numeric_leaves():
    facts = [{"nested": [{"value": -123.4567}]}]
    assert agent.guard({"text": "123.46 / 123.5 / 123 / -123.4567"}, facts) == []
    assert agent.guard({"text": "321.789"}, [{"untrusted_text": "321.789"}]) == ["321.789"]
    assert agent.guard({"text": "321.789"}, [{"numeric": 321.789}]) == []


def test_guard_offline_outputs_for_golden_plans(doc):
    for plan in _plans(doc):
        context = agent._context(plan)
        result = agent.analyze(plan)
        assert agent.guard(result, [context]) == []
        narrative = agent.narrative(plan)
        assert agent.guard(narrative, [agent._narrative_facts(plan)]) == []
        assert agent.guard({"brief": agent.brief(plan)}, [context]) == []
        assert result["recommendation"]["expected_score"] == evaluate(result["recommendation"]["plan"])["score"]


def test_offline_russian_analyze_and_brief(doc):
    result = agent.analyze(doc)
    assert "694 395" in result["summary"]
    assert "все районные меры идут в Нуру" in result["tradeoffs"][0]
    assert "Индекс Сарыарки:" in " ".join(result["consequences"])
    assert "S1 (школы и детсады) в Нуре: 38.00 → 48.00" in " ".join(result["strengths"])
    assert "«Школа + детсад (модульное строительство)» в Нуре" in result["strengths"][0]
    text = json.dumps(result, ensure_ascii=False)
    for name in district_names():
        assert f"{name} / " not in text
    output = agent.brief(doc)
    assert output.splitlines()[2].startswith("**Score 56.54; рейтинг акима 50.78")
    assert "| Индекс до | Индекс после |" in output
    assert "| «Школа + детсад (модульное строительство)» | В Нуре |" in output
    assert "\n- " in output and "694 395" in output


@pytest.mark.parametrize("district", load_dataset()["districts"], ids=lambda d: d["name"])
@pytest.mark.parametrize("mood", ["positive", "neutral", "negative"])
def test_council_voices_with_engine_deltas(district, mood):
    name = district["name"]
    other = next(n for n in district_names() if n != name)
    plan = {"decisions": [
        {"measure": mid, "district": name if mood == "positive" or (mood == "neutral" and mid == "M11") else other}
        for mid in ("M4", "M9", "M10", "M11")
    ] + [{"measure": "M12", "district": None}]}
    result = agent.narrative(plan)
    deputy = next(d for d in result["council"] if d["district"] == name)
    actual = approval(plan)["districts"][name]
    assert deputy["mood"] == mood
    assert deputy["deputy"] == f"Депутат от {district['cases']['gen']}"
    assert f"{actual['delta_D']:+.2f}" in deputy["quote"]
    assert agent.guard(result, [agent._narrative_facts(plan)]) == []
    assert result == agent.narrative(list(reversed(plan["decisions"])))


def test_council_measure_names_are_quoted(doc):
    quote = agent.narrative(doc)["council"][-1]["quote"]
    assert "«Освещение и камеры», «Школа + детсад» и «Центр семейного здоровья»" in quote
    # With only two local projects, preserve the complete dataset names.
    plan = deepcopy(doc)
    next(d for d in plan["decisions"] if d["measure"] == "M10")["district"] = "Байконур"
    quote = agent.narrative(plan)["council"][-1]["quote"]
    assert "«Школа + детсад (модульное строительство)» и «Центр семейного здоровья / поликлиника»" in quote


def test_newspaper_coverage_approval_and_social_facts(doc):
    best = optimize_info(doc)["best"]["plan"]
    for plan in (doc, best):
        facts = agent._narrative_facts(plan)
        paper = agent.narrative(plan)["newspaper"]
        headlines = paper["headlines"]
        missed = [d for d in load_dataset()["districts"]
                  if not approval(plan)["districts"][d["name"]]["got_district_measure"]]
        percent = round(sum(d["pop"] for d in missed) * 100, 2)
        coverage = next(h for h in headlines if "районных проектов" in h["title"])
        assert all(d["name"] in coverage["title"] for d in missed)
        assert coverage["lead"].startswith(f"{percent:g}%")
        title = "Аким удержал кресло" if facts["approval"]["reelected"] else "Горсовет обсуждает отставку"
        political = next(h for h in headlines if h["title"] == title)
        assert f"{facts['approval']['city']:.2f}" in political["lead"]
        assert all(not any(char.isdigit() for char in h["title"]) for h in headlines)
        assert "В Нуре" in headlines[0]["title"]
        assert "62.5%" in headlines[0]["lead"]
        assert "694 395" in paper["editorial"]
        assert "\n" not in paper["editorial"]
        assert paper["masthead"] == "Астана Times" and paper["date"] == "IV квартал 2028"
        assert any(h["title"] == "Ни одного района в красной зоне" for h in headlines)


def test_newspaper_all_districts_and_remaining_critical_cells():
    plan = {"decisions": [{"measure": mid, "district": name} for mid, name in zip(
        ("M1", "M4", "M9", "M10", "M13"), district_names())]}
    result = agent.narrative(plan)
    paper = result["newspaper"]
    assert any(h["title"] == "Каждый район получил свой проект" for h in paper["headlines"])
    assert paper["crit_sidebar"] == evaluate(plan)["crit_cells"]
    assert paper["crit_sidebar"]
    assert any("В Нуре" in h["title"] and "ниже красной черты" in h["lead"] for h in paper["headlines"])
    assert agent.guard(result, [agent._narrative_facts(plan)]) == []


def test_invented_numbers_twice_fall_back(monkeypatch, doc, caplog):
    expected = agent.analyze(doc)
    bad = _answer(doc)
    bad["summary"] = "Score вырастет до 61.3"
    client = _online(monkeypatch, [_message(bad), _message(bad)])
    result = agent.analyze(doc)
    assert result["provider"] == "offline"
    assert result["grounded"] is False
    assert result["guard"] == {"attempts": 2, "rejected": ["61.3"]}
    assert result["summary"] == expected["summary"]
    assert result["tool_trace"] == []
    assert "61.3" in caplog.text
    assert len(client.requests) == 2
    assert "Ты использовал числа" in client.requests[-1]["messages"][-1]["content"]
    assert client.closed


def test_clean_online_and_engine_score_override(monkeypatch, doc):
    client = _online(monkeypatch, [_message(_answer(doc))])
    result = agent.analyze(doc)
    assert result["provider"] == "openai"
    assert result["grounded"] is True
    assert result["guard"] == {"attempts": 1, "rejected": []}
    assert result["recommendation"]["expected_score"] == 56.54
    assert len(client.requests) == 1
    req = client.requests[0]
    initial = json.loads(req["messages"][1]["content"])
    assert initial["score"]["score"] == 56.54
    assert initial["approval"] == approval(doc)
    assert req["response_format"] == {"type": "json_object"}
    assert 0 < req["timeout"] <= 60
    assert req["model"] == "gpt-5-mini"


def test_retry_can_recover(monkeypatch, doc):
    bad = _answer(doc)
    bad["risks"] = ["Падение 8765.43"]
    _online(monkeypatch, [_message(bad), _message(_answer(doc))])
    result = agent.analyze(doc)
    assert result["grounded"] is True
    assert result["provider"] == "openai"
    assert result["guard"] == {"attempts": 2, "rejected": ["8765.43"]}


@pytest.mark.parametrize("recommendation", [None, {}, {"plan": []}, {"plan": {"decisions": []}}])
def test_invalid_or_missing_recommendation_is_replaced(monkeypatch, doc, recommendation):
    expected = agent.analyze(doc)["recommendation"]
    clean = _answer(doc)
    if recommendation is None:
        clean.pop("recommendation")
    else:
        clean["recommendation"] = recommendation
    _online(monkeypatch, [_message(clean)])
    result = agent.analyze(doc)
    assert result["provider"] == "openai"
    assert result["recommendation"] == expected


def test_one_tool_call(monkeypatch, doc):
    args = {"plan": doc}
    client = _online(monkeypatch, [_message(calls=[_call("score", args)]), _message(_answer(doc))])
    result = agent.analyze(doc)
    assert result["tool_trace"] == [{"tool": "score", "args": args}]
    assert result["provider"] == "openai"
    tool_result = json.loads(client.requests[1]["messages"][-1]["content"])
    assert tool_result["score"] == 56.54
    assert "timeline" not in tool_result
    assert all("before" not in d and "after" not in d for d in tool_result["districts"].values())


def test_tool_only_numbers_are_allowed(monkeypatch, doc):
    candidate = optimize_info(doc)["best"]["plan"]
    number = evaluate(candidate)["districts"]["Нура"]["D_after"]
    answer = _answer(doc)
    answer["summary"] = f"Альтернатива: индекс Нуры {number:.2f}."
    assert agent.guard(answer, [agent._context(doc)]) == [f"{number:.2f}"]
    _online(monkeypatch, [_message(calls=[_call("score", {"plan": candidate})]), _message(answer)])
    assert agent.analyze(doc)["provider"] == "openai"


def test_six_call_limit_and_final_json(monkeypatch, doc):
    replies = [_message(calls=[_call("validate", {"plan": doc}, i)]) for i in range(6)]
    client = _online(monkeypatch, replies + [_message(_answer(doc))])
    result = agent.analyze(doc)
    assert len(result["tool_trace"]) == 6
    assert len(client.requests) == 7
    assert "tools" not in client.requests[-1]
    assert client.requests[-1]["response_format"] == {"type": "json_object"}


def test_tool_batch_cannot_exceed_limit(monkeypatch, doc):
    _online(monkeypatch, [_message(calls=[_call("validate", {"plan": doc}, i) for i in range(7)])])
    result = agent.analyze(doc)
    assert result["provider"] == "offline"
    assert "ValueError" in result["fallback_reason"]


@pytest.mark.parametrize("tool", ["validate", "optimize_same_budget", "what_if", "remove_one"])
def test_engine_tool_dispatch(tool, doc):
    args = {"plan": doc}
    if tool == "what_if":
        args["swap"] = {"out": "M7", "in": {"measure": "M13", "district": "Алматы"}}
    result = agent._tool(tool, args)
    if tool == "validate":
        assert result == {"valid": True, "reason": None, "cost": 95}
    elif tool == "optimize_same_budget":
        assert result["best"]["score"] == 57.24 and "pareto" not in result
    elif tool == "what_if":
        assert result["valid"] and result["score"] == evaluate(result["plan"])["score"]
    else:
        assert result == evaluate(doc)["contributions"]


@pytest.mark.parametrize("failure", [TimeoutError("test-key-only"), RuntimeError("test-key-only"),
                                      _message("not JSON"), _message({"summary": "incomplete"})])
def test_online_failure_falls_back(monkeypatch, doc, failure):
    _online(monkeypatch, [failure])
    result = agent.analyze(doc)
    assert result["provider"] == "offline"
    assert result["fallback_reason"]
    assert "test-key-only" not in result["fallback_reason"]


@pytest.mark.parametrize("wrapper", ["```json\n%s\n```", "Ответ:\n%s\nГотово."])
def test_json_parsing(monkeypatch, doc, wrapper):
    _online(monkeypatch, [_message(wrapper % json.dumps(_answer(doc), ensure_ascii=False))])
    assert agent.analyze(doc)["provider"] == "openai"


def test_cache_is_canonical_and_copies_results(monkeypatch, doc):
    client = _online(monkeypatch, [_message(_answer(doc))])
    result = agent.analyze(doc)
    result["recommendation"]["plan"]["decisions"].clear()
    repeated = agent.analyze(list(reversed(doc["decisions"])))
    assert len(client.requests) == 1
    assert len(repeated["recommendation"]["plan"]["decisions"]) == 5


@pytest.mark.parametrize("provider", ["openai", "nvidia"])
def test_missing_keys_use_offline(monkeypatch, doc, provider):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    assert agent.analyze(doc)["provider"] == "offline"


def test_unset_provider_is_offline_even_with_key(monkeypatch, doc):
    monkeypatch.delenv("LLM_PROVIDER")
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    assert agent.analyze(doc)["provider"] == "offline"


def test_nvidia_configuration(monkeypatch, doc):
    client = FakeClient([_message(_answer(doc))])
    options = {}
    def factory(**kwargs):
        options.update(kwargs)
        return client
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "fake-nvidia")
    monkeypatch.delenv("NVIDIA_MODEL", raising=False)
    monkeypatch.delenv("NVIDIA_BASE_URL", raising=False)
    monkeypatch.setattr(agent, "OpenAI", factory)
    assert agent.analyze(doc)["provider"] == "nvidia"
    assert options == {"api_key": "fake-nvidia", "base_url": "https://integrate.api.nvidia.com/v1",
                       "timeout": 60.0, "max_retries": 0}
    assert client.requests[0]["model"] == "nvidia/nemotron-3-super-120b-a12b"


def test_narrative_offline(doc):
    result = agent.narrative(doc)
    assert [d["district"] for d in result["council"]] == district_names()
    assert len(result["newspaper"]["headlines"]) in (4, 5)
    assert result["newspaper"]["crit_sidebar"] == evaluate(doc)["crit_cells"]
    assert result["provider"] == "offline" and result["grounded"]
    for entry in result["council"]:
        actual = approval(doc)["districts"][entry["district"]]
        assert entry["delta_D"] == actual["delta_D"]
        assert entry["approval"] == actual["approval"]
        assert f"{actual['delta_D']:+.2f}" in entry["quote"]
    assert result == agent.narrative(list(reversed(doc["decisions"])))


@pytest.mark.parametrize("event", load_events(), ids=lambda e: e["id"])
def test_narrative_crisis(doc, event):
    result = agent.narrative(doc, event_id=event["id"])
    assert any(event["title"] in h["title"] for h in result["newspaper"]["headlines"])
    assert len(result["newspaper"]["headlines"]) == 5
    score = evaluate(doc, shocked_base(event))
    assert result["newspaper"]["crit_sidebar"] == score["crit_cells"]
    assert agent.guard(result, [agent._narrative_facts(doc, event["id"])]) == []


def test_narrative_crisis_swap(doc):
    event = load_events()[0]
    swap = {"out": "M7", "in": {"measure": "M13", "district": "Алматы"}}
    result = agent.narrative(doc, event_id=event["id"], swap=swap)
    resolved = resolve(doc, event["id"], swap)
    assert result["newspaper"]["crit_sidebar"] == resolved["crit_cells"]
    a = approval(resolved["new_plan"], shocked_base(event))
    for d in result["council"]:
        assert d["delta_D"] == a["districts"][d["district"]]["delta_D"]
        assert d["approval"] == a["districts"][d["district"]]["approval"]
    assert agent.guard(result, [agent._narrative_facts(doc, event["id"], swap)]) == []


@pytest.mark.parametrize("event_id,swap,recovery", [
    ("heating_almaty", None, "Меру пока не заменили"),
    ("heating_almaty", {"out": "M5", "in": {"measure": "M14", "district": None}}, "Потерю удалось возместить."),
    ("smog_saryarka", {"out": "M12", "in": {"measure": "M4", "district": "Сарыарка"}}, "Потерю удалось возместить лишь частично."),
    ("heating_almaty", {"out": "M7", "in": {"measure": "M13", "district": "Алматы"}}, "Замена не возместила потерю."),
])
def test_newspaper_crisis_recovery_language(doc, event_id, swap, recovery):
    facts = agent._narrative_facts(doc, event_id, swap)
    result = agent.narrative(doc, event_id=event_id, swap=swap)
    crisis = facts["crisis"]
    headline = result["newspaper"]["headlines"][-1]
    assert headline["title"] == crisis["event"]["title"]
    assert f"{crisis['crisis_cost']:.2f}" in headline["lead"]
    assert recovery in headline["lead"]
    if swap:
        assert f"{crisis['recovered']:+.2f}" in headline["lead"]
    assert "место исходного плана до кризиса" in result["newspaper"]["editorial"]
    assert agent.guard(result, [facts]) == []


def test_online_narrative_overrides_engine_fields(monkeypatch, doc):
    clean = agent.narrative(doc)
    clean["council"].reverse()
    for d in clean["council"]:
        d["delta_D"] = 9999
        d["approval"] = 9999
    clean["newspaper"]["crit_sidebar"] = [{"district": "Нура", "indicator": "S1", "value": 9999}]
    client = _online(monkeypatch, [_message(clean)])
    result = agent.narrative(doc)
    assert result["provider"] == "openai" and result["grounded"]
    assert len(client.requests) == 1 and "tools" not in client.requests[0]
    assert [d["district"] for d in result["council"]] == district_names()
    for d in result["council"]:
        assert d["delta_D"] == approval(doc)["districts"][d["district"]]["delta_D"]
        assert d["approval"] == approval(doc)["districts"][d["district"]]["approval"]
    assert result["newspaper"]["crit_sidebar"] == evaluate(doc)["crit_cells"]


def test_narrative_guard_fallback(monkeypatch, doc):
    payload = agent.narrative(doc)
    payload["newspaper"]["editorial"] = "Рост до 9876.543"
    _online(monkeypatch, [_message(payload), _message(payload)])
    result = agent.narrative(doc)
    assert result["grounded"] is False and result["provider"] == "offline"
    assert result["guard"] == {"attempts": 2, "rejected": ["9876.543"]}


@pytest.mark.parametrize("function", [agent.analyze, agent.narrative, agent.brief])
@pytest.mark.parametrize("plan", [None, {}, [], {"decisions": []}, [{"measure": "M7"}] * 5])
def test_invalid_plan_is_value_error(function, plan):
    with pytest.raises(ValueError):
        function(plan)


def test_invalid_crisis_parameters(doc):
    with pytest.raises(ValueError, match="Неизвестное событие"):
        agent.narrative(doc, event_id="unknown")
    with pytest.raises(ValueError, match="event_id"):
        agent.narrative(doc, swap={})


def test_brief_is_instant_offline_and_deterministic(monkeypatch, doc):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    output = agent.brief(doc)
    assert "56.54" in output
    assert "Кабинет акима — решение команды" in output
    assert "Все числа посчитаны движком `engine/`; ИИ только объясняет." in output
    assert output == agent.brief(list(reversed(doc["decisions"])))


# --- multi-key fallback chain -------------------------------------------------

class _ApiError(Exception):
    def __init__(self, status):
        super().__init__(f"boom {status}")
        self.status_code = status


def _chain(monkeypatch, provider_env, keys, clients):
    """keys: {"OPENAI_API_KEYS": "k1,k2", ...}; clients: {key: FakeClient}."""
    monkeypatch.setenv("LLM_PROVIDER", provider_env)
    for name, value in keys.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(agent, "_client_factory", lambda api_key, base_url: clients[api_key])


def test_second_key_takes_over_and_dead_key_is_skipped(monkeypatch, doc):
    first = FakeClient([_ApiError(401)])
    second = FakeClient([_message(_answer(doc)), _message(_answer(doc))])
    _chain(monkeypatch, "openai", {"OPENAI_API_KEYS": "secret-one,secret-two"},
           {"secret-one": first, "secret-two": second})
    result = agent.analyze(doc)
    assert result["provider"] == "openai" and result["grounded"] is True
    assert result["fallback_chain"] == ["openai#1: _ApiError 401"]
    assert "secret" not in json.dumps(result, ensure_ascii=False)
    agent._CACHE.clear()
    agent.narrative(doc)  # the 401 key is now skipped: no second request to it
    assert len(first.requests) == 1


def test_openai_fails_then_nvidia_answers(monkeypatch, doc):
    clients = {"o1": FakeClient([_ApiError(429)]), "n1": FakeClient([_message(_answer(doc))])}
    _chain(monkeypatch, "openai,nvidia", {"OPENAI_API_KEYS": "o1", "NVIDIA_API_KEYS": "n1"}, clients)
    result = agent.analyze(doc)
    assert result["provider"] == "nvidia"
    assert result["fallback_chain"] == ["openai#1: _ApiError 429"]


def test_all_keys_fail_offline_not_cached(monkeypatch, doc):
    clients = {"a": FakeClient([_ApiError(401)]), "b": FakeClient([_ApiError(500), _message(_answer(doc))])}
    _chain(monkeypatch, "openai", {"OPENAI_API_KEYS": "a,b"}, clients)
    result = agent.analyze(doc)
    assert result["provider"] == "offline"
    assert result["fallback_reason"] == "LLM unavailable: openai#1: _ApiError 401; openai#2: _ApiError 500"
    assert "_error" not in result
    # not cached: the next click retries; key "a" is dead (401), key "b" had a transient 500
    retry = agent.analyze(doc)
    assert retry["provider"] == "openai"
    assert len(clients["a"].requests) == 1
