"""Conservative sign and tool-argument provenance regressions."""

from copy import deepcopy
import json

import pytest

from api import agent
from engine.model import load_dataset
from engine.optimize import optimize_info
from engine.score import evaluate


@pytest.mark.parametrize("sign", ["-", "−", "‐", "－", "- "])
def test_negative_sign_requires_negative_fact(sign):
    payload = {"text": f"Изменение: {sign}3,98 балла."}
    assert agent.guard(payload, [{"delta": 3.98}]) == ["-3.98"]
    assert agent.guard(payload, [{"delta": -3.98}]) == []


@pytest.mark.parametrize("sign", ["+", "＋", "+ "])
def test_positive_sign_requires_positive_fact(sign):
    payload = {"text": f"Изменение: {sign}2.32 балла."}
    assert agent.guard(payload, [{"recovered": -2.32}]) == ["2.32"]
    assert agent.guard(payload, [{"recovered": 2.32}]) == []


def test_unsigned_loss_retains_absolute_match_and_rounding():
    facts = [{"recovered": -123.4567}]
    assert agent.guard({"text": "Потеря 123.46 / 123.5 / 123."}, facts) == []
    assert agent.guard({"text": "Изменение -123.46 / -123.5 / -123."}, facts) == []
    assert agent.guard({"text": "+123.46 / +123.5 / +123"}, facts) == [
        "123.46", "123.5", "123"]


@pytest.mark.parametrize("key", ["args", "arguments"])
def test_echoed_arguments_do_not_supply_numbers_or_percentages(key):
    facts = [{"tool_trace": ({"tool": "score", key: {
        "nested": [{"invented": 1234.56}], "realized_share": {"M7": 0.32123}},
        "result": {"score": 56.54, "realized_share": {"M7": 0.625}}},)}]
    original = deepcopy(facts)
    assert agent.guard({"text": "Число 1234.56; доля 32.123%."}, facts) == [
        "1234.56", "32.123"]
    assert agent.guard({"text": "Score 56.54; доля 62.5%."}, facts) == []
    assert facts == original


@pytest.mark.parametrize("source", ["result", "precomputed"])
def test_argument_number_is_allowed_when_independently_supported(source):
    facts = [{"tool": "score", "args": {"invented": 1234.56}}]
    if source == "result":
        facts[0]["result"] = {"value": 1234.56}
    else:
        facts.append({"value": 1234.56})
    assert agent.guard({"text": "Число 1234.56."}, facts) == []


def test_model_payload_cannot_supply_its_own_fact():
    payload = {"text": "Число 1234.56.",
               "tool_trace": [{"tool": "score", "args": {"value": 1234.56}}]}
    assert agent.guard(payload, []) == ["1234.56"]


@pytest.mark.parametrize("supported", [False, True])
def test_tool_argument_echo_retries_and_falls_back(monkeypatch, supported):
    args = {"invented": 1234.56}
    result = {"args": args, "score": 56.54}
    if supported:
        result["value"] = args["invented"]
    call = {"id": "call_0", "type": "function", "function": {
        "name": "score", "arguments": json.dumps(args)}}
    content = json.dumps({"comment": "Число 1234.56."}, ensure_ascii=False)
    replies = iter([{"tool_calls": [call]}, {"content": content}, {"content": content}])
    monkeypatch.setattr(agent, "_client_factory", lambda **kwargs: object())
    monkeypatch.setattr(agent, "_complete", lambda *args, **kwargs: next(replies))
    monkeypatch.setattr(agent, "_tool", lambda *args: result)
    answer = agent._explain(
        ("openai", "fake-key", "test-model", "test-base"), [], [],
        {"comment": "Резерв."}, lambda payload: payload, agent=True)
    assert answer["grounded"] is supported
    assert answer["provider"] == ("openai" if supported else "offline")
    assert answer["guard"] == {
        "attempts": 1 if supported else 2,
        "rejected": [] if supported else ["1234.56"],
    }
    assert answer["comment"] == ("Число 1234.56." if supported else "Резерв.")


@pytest.mark.parametrize("optimal", [False, True])
def test_golden_plan_numbers_keep_their_sign(optimal):
    plan = load_dataset()["reference"]["doc_example"]
    if optimal:
        plan = optimize_info(plan)["best"]["plan"]
    facts = evaluate(plan)
    assert (facts["score"], facts["cost"], facts["delta"]) == (
        (57.24, 98, 4.68) if optimal else (56.54, 95, 3.98))
    assert agent.guard({"text": (
        f"Score {facts['score']:.2f}; стоимость {facts['cost']}; "
        f"прирост {facts['delta']:+.2f}."
    )}, [facts]) == []
    wrong_sign = f"-{facts['delta']:.2f}"
    assert agent.guard({"text": f"Прирост {wrong_sign}."}, [facts]) == [wrong_sign]
