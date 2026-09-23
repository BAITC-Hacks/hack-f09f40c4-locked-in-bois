"""Engine-backed analyst tools and crisis verdicts, without network access."""

from collections import OrderedDict
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api import agent
from api.main import app
from engine import grading, promise, stress
from engine.approval import approval
from engine.model import load_dataset, load_events
from engine.optimize import optimize_info
from engine.shock import resolve, shock, shocked_base


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    monkeypatch.setattr(agent, "_CACHE", OrderedDict())

    def no_network(**kwargs):
        pytest.fail("Unexpected network client")

    monkeypatch.setattr(agent, "_client_factory", no_network)


@pytest.fixture
def doc():
    return deepcopy(load_dataset()["reference"]["doc_example"])


def _message(payload=None, calls=None):
    return {"choices": [{"message": {
        "content": json.dumps(payload, ensure_ascii=False), "tool_calls": calls}}]}


def _call(name, args, index=0):
    return {"id": f"call_{index}", "type": "function", "function": {
        "name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _online(monkeypatch, replies):
    responses, requests = iter(replies), []

    def create(**kwargs):
        requests.append(deepcopy(kwargs))
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(agent, "_client_factory", lambda **kwargs: client)
    return requests


@pytest.mark.parametrize("name,module,function", [
    ("stress_test", stress, "stress_test"),
    ("grade_plan", grading, "grade"),
    ("price_of_promise", promise, "price"),
])
def test_new_tool_dispatch(monkeypatch, doc, name, module, function):
    value = [{"type": "min_approval", "value": approval(doc)["threshold"]}] if name == "price_of_promise" else doc
    engine_result = getattr(module, function)(value)
    calls = []

    def engine(argument):
        calls.append(argument)
        return deepcopy(engine_result)

    monkeypatch.setattr(module, function, engine)
    key = "promises" if name == "price_of_promise" else "plan"
    result = agent._tool(name, {key: value})
    assert calls == [value]
    schema = next(tool["function"]["parameters"] for tool in agent.TOOLS if tool["function"]["name"] == name)
    assert schema["required"] == [key]
    assert set(schema["properties"]) == {key}
    if name == "stress_test":
        assert set(result) == {"scenarios", "worst_event", "crisis_proof_plan"}
        assert result["worst_event"] == engine_result["worst_event"]
        assert result["crisis_proof_plan"] == engine_result["crisis_proof_plan"]
        assert len(result["scenarios"]) == len(engine_result["scenarios"])
        for row, full in zip(result["scenarios"], engine_result["scenarios"]):
            assert row == {key: full[key] for key in ("event_id", "title", "score", "loss", "insurance")}
    elif name == "grade_plan":
        assert set(result) == {"moves", "accuracy"}
        assert result["accuracy"] == engine_result["accuracy"]
        assert len(result["moves"]) == len(engine_result["moves"])
        for move, full in zip(result["moves"], engine_result["moves"]):
            assert move == {key: full[key] for key in (
                "measure", "district", "grade", "label", "loss", "best_alternative")}
    else:
        assert result == engine_result


@pytest.mark.parametrize("name", ["stress_test", "grade_plan", "price_of_promise"])
def test_new_tool_results_feed_number_guard(monkeypatch, doc, name):
    original = agent._tool
    args = {"promises": []} if name == "price_of_promise" else {"plan": doc}
    # A tool-only numerical fact must become available to the final guard.
    def tool(tool_name, arguments):
        return {**original(tool_name, arguments), "test_fact": 1234.56}

    monkeypatch.setattr(agent, "_tool", tool)
    payload = {"comment": "Факт движка: 1234.56."}
    assert agent.guard(payload, []) == ["1234.56"]
    requests = _online(monkeypatch, [_message(calls=[_call(name, args)]), _message(payload)])
    result = agent._explain_chain(agent._providers(), [], [], {"comment": "Резерв."}, lambda p: p, agent=True)
    assert result["provider"] == "openai" and result["grounded"]
    assert result["comment"] == payload["comment"]
    assert requests[0]["tool_choice"]["function"]["name"] == "optimize_same_budget"
    returned = json.loads(requests[1]["messages"][-1]["content"])
    assert returned["test_fact"] == 1234.56


@pytest.mark.parametrize("optimal", [False, True])
def test_offline_analysis_crisis_and_grading(doc, optimal):
    plan = optimize_info(doc)["best"]["plan"] if optimal else doc
    facts = agent._context(agent._plan(plan))
    result = agent.analyze(plan)
    assert agent.guard(result, [facts]) == []
    worst = next(row for row in facts["stress"]["scenarios"] if row["event_id"] == facts["stress"]["worst_event"])
    risk = next(line for line in result["risks"] if "Худший отдельный кризис" in line)
    assert worst["title"] in risk and f"{worst['loss']:.2f}" in risk
    assert worst["insurance"]["out"] in risk
    assert worst["insurance"]["in"]["measure"] in risk
    assert f"{worst['insurance']['recovered']:+.2f}" in risk
    if optimal:
        assert any("Все ходы лучшие" in line for line in result["strengths"])
    else:
        move = max(facts["grading"]["moves"], key=lambda m: m["loss"])
        line = next(line for line in result["risks"] if "Разбор ходов" in line)
        assert move["measure"] in line and f"{move['loss']:.2f}" in line
        assert move["best_alternative"]["measure"] in line


@pytest.mark.parametrize("event", load_events(), ids=lambda event: event["id"])
@pytest.mark.parametrize("kind", ["best", "suboptimal", "negative"])
def test_swap_comment_offline_guard(doc, event, kind):
    plan = optimize_info(doc)["best"]["plan"] if kind == "negative" else doc
    seed = next(i for i, item in enumerate(load_events()) if item["id"] == event["id"])
    swap = (shock(plan, seed)["best_swaps"][0] if kind != "suboptimal" else
            {"out": "M7", "in": {"measure": "M13", "district": "Алматы"}})
    resolved = resolve(plan, event["id"], swap)
    original = deepcopy(resolved)
    result = agent.swap_comment(resolved)
    assert set(result) == {"comment", "provider", "grounded"}
    assert result["provider"] == "offline" and result["grounded"]
    assert agent.guard(result, [resolved]) == []
    assert f"{resolved['crisis_cost']:.2f}" in result["comment"]
    assert f"{abs(resolved['recovered']):.2f}" in result["comment"]
    if kind == "suboptimal":
        assert not resolved["swap_was_optimal"]
        best = resolved["best_possible_swap"]
        assert f"лучше было заменить {best['out']} на {best['in']['measure']}" in result["comment"]
        assert f"{best['gain']:+.2f}" in result["comment"]
    else:
        assert resolved["swap_was_optimal"]
        assert "лучший возможный ход" in result["comment"]
    if kind == "negative":
        assert resolved["recovered"] < 0
        assert "дополнительно снизила" in result["comment"]
    assert result == agent.swap_comment(resolved)
    assert resolved == original


@pytest.mark.parametrize("failure", [None, "numbers", "structure", "exception"])
def test_swap_comment_provider_and_fallback(monkeypatch, doc, failure):
    resolved = resolve(doc, load_events()[0]["id"], shock(doc, 0)["best_swaps"][0])
    fallback = agent.swap_comment(resolved)
    payload = {"comment": fallback["comment"]}
    if failure == "numbers":
        payload = {"comment": "Кризис стоил 9876.54 балла."}
    elif failure == "structure":
        payload = {"comment": []}
    replies = [RuntimeError("offline test")] if failure == "exception" else [_message(payload), _message(payload)]
    requests = _online(monkeypatch, replies)
    result = agent.swap_comment(resolved)
    assert result["provider"] == ("offline" if failure else "openai")
    assert result["comment"] == fallback["comment"]
    assert result["grounded"] == (failure != "numbers")
    assert all("tools" not in request for request in requests)
    assert agent.guard({"comment": result["comment"]}, [resolved]) == []


@pytest.mark.parametrize("event", load_events(), ids=lambda event: event["id"])
def test_resolve_route_adds_comment(doc, event):
    swap = {"out": "M7", "in": {"measure": "M13", "district": "Алматы"}}
    expected = resolve(doc, event["id"], swap)
    expected["approval"] = approval(expected["new_plan"], shocked_base(event))
    with TestClient(app) as client:
        response = client.post("/api/shock/resolve", json={"plan": doc, "event_id": event["id"], "swap": swap})
    assert response.status_code == 200
    result = response.json()
    assert result.pop("comment") == agent.swap_comment(expected)["comment"]
    assert result == expected
