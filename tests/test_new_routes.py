"""HTTP coverage for receipts, duels, fairness, calendars and approval warnings."""

from copy import deepcopy
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from api import agent
from api.main import app
from engine.approval import approval
from engine.calendar import calendar
from engine.duel import duel, duel_vs_best
from engine.fairness import fairness
from engine.model import load_dataset
from engine.optimize import optimize_info
from engine.receipt import receipt
from engine.validate import validate


@pytest.fixture(scope="module")
def plans():
    doc = {"decisions": deepcopy(load_dataset()["reference"]["doc_example"]["decisions"])}
    return {"doc": doc, "optimum": optimize_info(doc)["best"]["plan"]}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as instance:
        yield instance


@pytest.mark.parametrize("name", ["doc", "optimum"])
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("route,function", [
    ("receipt", receipt), ("fairness", fairness), ("calendar", calendar),
])
def test_single_plan_routes_match_engine(client, plans, name, wrapped, route, function):
    plan = plans[name]
    response = client.post(f"/api/{route}", json={"plan": plan} if wrapped else plan)
    assert response.status_code == 200
    assert response.json() == function(plan)


@pytest.mark.parametrize("name", ["doc", "optimum"])
def test_duel_without_plan_b_matches_both_benchmarks(client, plans, name):
    response = client.post("/api/duel", json={"plan_a": plans[name]})
    assert response.status_code == 200
    assert response.json() == duel_vs_best(plans[name])


@pytest.mark.parametrize("a,b,winner,delta", [
    ("doc", "optimum", "B", -0.70),
    ("optimum", "doc", "A", 0.70),
    ("optimum", "optimum", "tie", 0.00),
])
def test_explicit_duel_direction_and_ties(client, plans, a, b, winner, delta):
    response = client.post("/api/duel", json={"plan_a": plans[a], "plan_b": plans[b]})
    assert response.status_code == 200
    result = response.json()
    assert result == duel(plans[a], plans[b])
    assert result["winner"] == winner
    assert result["score_delta"] == delta
    assert sum(Decimal(str(row["delta"])) for row in result["terms"].values()) == Decimal(str(delta))


@pytest.mark.parametrize("route", ["receipt", "fairness", "calendar", "duel_a", "duel_b"])
@pytest.mark.parametrize("problem", [
    "null", "scalar", "empty", "partial", "non_object", "unknown_measure",
    "measure_list", "district_dict", "duplicate", "over_budget", "city_district",
])
def test_invalid_plans_return_validator_reason(client, plans, route, problem):
    plan = deepcopy(plans["doc"])
    if problem == "null":
        plan = None
    elif problem == "scalar":
        plan = 42
    elif problem == "empty":
        plan = {}
    elif problem == "partial":
        plan["decisions"].pop()
    elif problem == "non_object":
        plan["decisions"][0] = None
    elif problem == "unknown_measure":
        plan["decisions"][0]["measure"] = "M404"
    elif problem == "measure_list":
        plan["decisions"][0]["measure"] = []
    elif problem == "district_dict":
        plan["decisions"][0]["district"] = {}
    elif problem == "duplicate":
        plan["decisions"][0] = deepcopy(plan["decisions"][1])
    elif problem == "over_budget":
        plan["decisions"][2] = {"measure": "M3", "district": "Нура"}
    elif problem == "city_district":
        plan["decisions"][3]["district"] = "Нура"
    valid, reason = validate(plan)
    assert not valid
    body = {"plan": plan}
    if route == "duel_a":
        body = {"plan_a": plan, "plan_b": plans["doc"]}
    elif route == "duel_b":
        body = {"plan_a": plans["doc"], "plan_b": plan}
    response = client.post(f"/api/{route.split('_')[0]}", json=body)
    assert response.status_code == 422
    assert response.json()["detail"] == reason


@pytest.mark.parametrize("route", ["receipt", "fairness", "calendar", "duel"])
@pytest.mark.parametrize("content", [b"", b"{", b"null", b"[]", b"{}"])
def test_missing_or_malformed_body_returns_422(client, route, content):
    response = client.post(f"/api/{route}", content=content,
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422


def test_duel_requires_plan_a_even_with_valid_plan_b(client, plans):
    response = client.post("/api/duel", json={"plan_b": plans["doc"]})
    assert response.status_code == 422


def test_offline_recommendation_discloses_approval_and_is_grounded(client, plans, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    agent._CACHE.clear()
    response = client.post("/api/analyze", json={"plan": plans["doc"]})
    assert response.status_code == 200
    result = response.json()
    recommendation = result["recommendation"]
    political = approval(recommendation["plan"])
    assert political["city"] == 47.16
    assert f"Рейтинг акима при нём — {political['city']:.2f}" in recommendation["why"]
    assert "ниже порога переизбрания" in recommendation["why"]
    assert result["provider"] == "offline"
    assert result["grounded"]
    assert not agent.guard(result, [agent._context(plans["doc"])])
