"""Jargon regressions use engine facts and a fake client; no network required."""

from collections import OrderedDict
from copy import deepcopy

import pytest

from api import agent
from engine.model import load_dataset, load_events
from engine.optimize import optimize_info
from engine.score import evaluate
from engine.shock import resolve, shock
from test_agent import _answer, _call, _message, _online


@pytest.fixture(autouse=True)
def isolated_client(monkeypatch):
    monkeypatch.setattr(agent, "_CACHE", OrderedDict())

    def no_network(**kwargs):
        pytest.fail("Unexpected real OpenAI client construction")

    monkeypatch.setattr(agent, "OpenAI", no_network)


@pytest.fixture
def doc():
    return deepcopy(load_dataset()["reference"]["doc_example"])


@pytest.mark.parametrize("token", [
    "approval", "balanced", "marginal", "delta_D", "D_min", "d_min", "d_avg",
    "score_without", "n_crit", "crit_cells", "resolved_crit_cells",
    "got_district_measure", "best_at_same_cost", "future_engine_field",
    "D_after", "N_CRIT", "field_2_value",
    *[tool["function"]["name"] for tool in agent.TOOLS],
])
def test_internal_names_in_prose(token):
    assert agent.jargon({"text": f"Результат (см. `{token}`)."}) == [token]


@pytest.mark.parametrize("assignment", [
    "approval = 39,75", "got_district_measure = false", "flag=true",
    "value = -3.98", "city=Нура",
])
def test_assignments(assignment):
    assert agent.jargon({"text": assignment}) == [assignment]


def test_nested_values_are_checked_once_and_keys_are_not():
    payload = {"approval": 39.75, "score_without": None, "delta_D": 1.0,
               "text": ["balanced", {"quote": "marginal, balanced"}, ("what_if",)]}
    original = deepcopy(payload)
    assert agent.jargon(payload) == ["balanced", "marginal", "what_if"]
    assert payload == original


def test_legitimate_words_and_dataset_codes_pass():
    data = load_dataset()
    text = "Score, Safe City, AQI, ЛРТ, openai; Астана Times, IV квартал."
    assert agent.jargon({"text": text, "ids": [m["id"] for m in data["measures"]],
                         "indicators": [i["code"] for i in data["indicators"]],
                         "moods": ["positive", "negative", "neutral"]}) == []
    assert agent.jargon("disapproval, unbalanced, marginally") == []


@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("kind", ["analyze", "narrative", "swap_comment"])
def test_retry_and_fallback(monkeypatch, doc, kind, persistent):
    if kind == "analyze":
        run = lambda: agent.analyze(doc)
        clean = _answer(doc)
        dirty = deepcopy(clean)
        dirty["recommendation"]["why"] = "Выберите balanced (см. best_at_same_cost)."
        offenders = ["balanced", "best_at_same_cost"]
        field = "summary"
    elif kind == "narrative":
        run = lambda: agent.narrative(doc)
        clean = {k: v for k, v in run().items() if k in ("council", "newspaper")}
        dirty = deepcopy(clean)
        dirty["council"][0]["quote"] += " got_district_measure = false"
        offenders = ["got_district_measure = false"]
        field = "newspaper"
    else:
        resolved = resolve(doc, load_events()[0]["id"], shock(doc, 0)["best_swaps"][0])
        run = lambda: agent.swap_comment(resolved)
        clean = {"comment": run()["comment"]}
        dirty = {"comment": "Результат what_if: см. best_possible_swap."}
        offenders = ["what_if", "best_possible_swap"]
        field = "comment"
    expected = run()
    prefix = ([_message(calls=[_call("optimize_same_budget", {"plan": doc})])]
              if kind == "analyze" else [])
    client = _online(monkeypatch, prefix + [_message(dirty), _message(dirty if persistent else clean)])
    result = run()
    assert result["provider"] == ("offline" if persistent else "openai")
    assert result["grounded"] is not persistent
    assert result[field] == (expected if persistent else clean)[field]
    if kind != "swap_comment":
        assert result["guard"] == {"attempts": 2, "rejected": offenders}
    # Diagnostics deliberately retain offending text; test only published prose.
    assert agent.jargon({field: result[field]}) == []
    assert len(client.requests) == len(prefix) + 2
    correction = client.requests[-1]["messages"][-1]["content"]
    assert "Не используй внутренние названия полей и инструментов" in correction
    assert "обычным русским языком" in correction
    assert all(offender in correction for offender in offenders)
    assert "tools" not in client.requests[-1]
    assert client.closed


@pytest.mark.parametrize("second", ["clean", "number", "jargon", "exception"])
def test_guards_share_one_retry_and_preserve_diagnostics(monkeypatch, doc, second):
    bad = _answer(doc)
    bad["summary"] = "approval: 8765.43"
    retry = _answer(doc)
    if second == "number":
        retry["summary"] = "Рейтинг 8765.43"
    elif second == "jargon":
        retry["summary"] = "marginal"
    response = RuntimeError("unavailable") if second == "exception" else _message(retry)
    client = _online(monkeypatch, [_message(bad), response])
    result = agent.analyze(doc)
    assert result["provider"] == ("openai" if second == "clean" else "offline")
    assert result["grounded"] is (second == "clean")
    rejected = ["8765.43", "approval"] + (["marginal"] if second == "jargon" else [])
    assert result["guard"]["rejected"] == rejected
    assert len(client.requests) == 2
    correction = client.requests[-1]["messages"][-1]["content"]
    assert "Ты использовал числа" in correction
    assert "Не используй внутренние названия" in correction


@pytest.mark.parametrize("kind, score, cost", [
    ("doc", 56.54, 95), ("optimum", 57.24, 98), ("cheapest", 55.67, 61),
])
def test_offline_templates_are_jargon_free(doc, kind, score, cost):
    info = optimize_info(doc)
    plan = (doc if kind == "doc" else info["best"]["plan"] if kind == "optimum"
            else min(info["pareto"], key=lambda row: row["cost"])["plan"])
    facts = evaluate(plan)
    assert (facts["score"], facts["cost"]) == (score, cost)
    analysis = agent.analyze(plan)
    assert f"Score {score:.2f}" in analysis["summary"]
    assert agent.jargon(analysis) == []
    assert agent.jargon(agent.narrative(plan)) == []
    for seed, event in enumerate(load_events()):
        swaps = shock(plan, seed)["best_swaps"]
        for swap in (swaps[0], swaps[-1]):
            resolved = resolve(plan, event["id"], swap)
            assert agent.jargon(agent.swap_comment(resolved)) == []
            assert agent.jargon(agent.narrative(plan, event_id=event["id"], swap=swap)) == []


def test_public_words_may_use_equals_sign():
    from api.agent import jargon
    assert jargon({"t": "Лучший план: Score = 57.0, S1 = 48, M7 = школа."}) == []
    assert jargon({"t": "approval = 39,75"}) != []
