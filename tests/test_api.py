"""HTTP contract checks using the documented reference plan."""

import json

import pytest
from fastapi.testclient import TestClient

from api import db
from api.main import REPO_ROOT, app
from engine.model import load_dataset


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "leaderboard.db")
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    with TestClient(app) as client:
        yield client


@pytest.fixture
def plan():
    return {"decisions": [dict(d) for d in load_dataset()["reference"]["doc_example"]["decisions"]]}


def test_dataset(client):
    response = client.get("/api/dataset")
    assert response.status_code == 200
    assert response.content == (REPO_ROOT / "data" / "dataset.json").read_bytes()
    assert len(response.json()["measures"]) == 14
    assert len(response.json()["districts"]) == 5


def test_validate_partial_and_complete(client, plan):
    response = client.post("/api/validate", json={"plan": {"decisions": plan["decisions"][:3]}})
    assert response.status_code == 200
    partial = response.json()
    assert partial["valid"] is False
    assert partial["reason"]
    assert partial["cost"] == 56
    assert partial["remaining"] == 44
    assert partial["direction_counts"] == {
        "transport": 0, "ecology": 0, "social": 2, "safety": 1, "services": 0,
    }
    response = client.post("/api/validate", json=plan)
    assert response.status_code == 200
    assert response.json() == {
        "valid": True, "reason": None, "cost": 95, "remaining": 5,
        "direction_counts": {"transport": 0, "ecology": 1, "social": 2, "safety": 1, "services": 1},
    }


@pytest.mark.parametrize("body", [{}, {"decisions": []}, {"plan": None}, {"decisions": [None]}, []])
def test_validate_malformed_or_empty_plan(client, body):
    response = client.post("/api/validate", json=body)
    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is False
    assert result["reason"]
    assert result["cost"] == 0
    assert result["remaining"] == 100
    assert len(result["direction_counts"]) == 5


def test_validate_missing_and_invalid_json(client):
    for content in (b"", b"{"):
        response = client.post("/api/validate", content=content, headers={"Content-Type": "application/json"})
        assert response.status_code == 200
        assert response.json()["valid"] is False


def test_validate_unknown_measure(client, plan):
    plan["decisions"][0]["measure"] = "M404"
    result = client.post("/api/validate", json=plan).json()
    assert result["valid"] is False
    assert "Неизвестная мера" in result["reason"]
    assert result["cost"] == 71
    assert result["remaining"] == 29


@pytest.mark.parametrize("wrapped", [False, True])
def test_score(client, plan, wrapped):
    response = client.post("/api/score", json={"plan": plan} if wrapped else plan)
    assert response.status_code == 200
    result = response.json()
    assert result["score"] == 56.54
    assert result["baseline"] == 52.56
    assert result["cost"] == 95
    assert "city" in result["approval"]
    assert len(result["districts"]) == 5
    assert all(len(d["before"]) == len(d["after"]) == 10 for d in result["districts"].values())


@pytest.mark.parametrize("route", ["score", "approval", "optimize", "shock", "shock/resolve",
                                         "analyze", "narrative", "brief", "submit"])
def test_scoring_routes_reject_over_budget(client, plan, route):
    plan["decisions"][2] = {"measure": "M3", "district": "Нура"}
    response = client.post(f"/api/{route}", json={"team": "Команда", "plan": plan})
    assert response.status_code == 422
    assert "Превышен бюджет" in response.json()["detail"]


def test_approval(client, plan):
    response = client.post("/api/approval", json=plan)
    assert response.status_code == 200
    assert {"city", "threshold", "reelected", "districts"} <= response.json().keys()
    assert response.json() == client.post("/api/score", json=plan).json()["approval"]


def test_optimize(client, plan):
    response = client.post("/api/optimize", json={"plan": plan})
    assert response.status_code == 200
    result = response.json()
    assert result["total_valid"] == 694395
    assert result["best"]["score"] == 57.24
    assert result["rank"] >= 1


def test_shock_and_resolve(client, plan):
    response = client.post("/api/shock", json={"plan": plan, "seed": 0})
    assert response.status_code == 200
    crisis = response.json()
    assert "event" in crisis
    assert crisis["must"] == "swap_one"
    swap = {"out": "M7", "in": {"measure": "M13", "district": "Алматы"}}
    response = client.post("/api/shock/resolve", json={
        "plan": plan, "event_id": crisis["event"]["id"], "swap": swap,
    })
    assert response.status_code == 200
    result = response.json()
    assert {"crisis_cost", "recovered", "approval", "new_plan"} <= result.keys()
    assert result["new_plan"]["decisions"][0] == swap["in"]
    assert result["cost"] == 99
    invalid = client.post("/api/shock/resolve", json={
        "plan": plan, "event_id": crisis["event"]["id"],
        "swap": {"out": "M12", "in": {"measure": "M13", "district": "Алматы"}},
    })
    assert invalid.status_code == 422
    assert "Превышен бюджет" in invalid.json()["detail"]


def test_submit_ignores_client_score(client, plan):
    assert not db.DB_PATH.exists()
    response = client.post("/api/submit", json={"team": "  Команда  ", "plan": plan, "score": 99})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "id": 1, "rank": 1}
    response = client.get("/api/leaderboard")
    assert response.status_code == 200
    entry, = response.json()
    assert entry["team"] == "Команда"
    assert entry["score"] == 56.54
    assert entry["cost"] == 95
    assert entry["plan"] == plan
    assert entry["approval"] == client.post("/api/approval", json=plan).json()["city"]
    assert entry["created_at"]


@pytest.mark.parametrize("team", [None, "", "   ", "x" * 41, 123])
def test_submit_rejects_invalid_team(client, plan, team):
    assert client.post("/api/submit", json={"team": team, "plan": plan}).status_code == 422
    assert not db.DB_PATH.exists()


def test_leaderboard_order_and_rank(client, plan):
    assert client.get("/api/leaderboard").json() == []
    assert db.add("First", 56, 95, 50, plan) == (1, 1)
    assert db.add("Lower", 55, 95, 50, plan) == (2, 2)
    assert db.add("Equal", 56, 95, 50, plan) == (3, 2)
    assert db.add("Best", 57, 95, 50, plan) == (4, 1)
    assert [entry["team"] for entry in client.get("/api/leaderboard").json()] == [
        "Best", "First", "Equal", "Lower",
    ]
    assert len(db.top(limit=2)) == 2


def test_health_and_cors(client, monkeypatch, tmp_path):
    from engine import optimize

    monkeypatch.setattr(optimize, "CACHE_PATH", tmp_path / "missing-cache.json")
    response = client.get("/api/health", headers={"Origin": "https://example.com"})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "provider": "offline", "cache": False}
    assert response.headers["access-control-allow-origin"] == "*"


def test_analyze_offline(client, plan):
    pytest.importorskip("api.agent")
    response = client.post("/api/analyze", json={"plan": plan, "lang": "ru"})
    assert response.status_code == 200
    result = response.json()
    assert {"summary", "strengths", "risks", "consequences", "tradeoffs", "recommendation",
            "provider", "grounded"} <= result.keys()
    assert result["provider"] == "offline"
    assert {"plan", "expected_score", "why"} <= result["recommendation"].keys()


def test_narrative_offline(client, plan):
    pytest.importorskip("api.agent")
    response = client.post("/api/narrative", json={"plan": plan, "lang": "ru"})
    assert response.status_code == 200
    result = response.json()
    assert {"council", "newspaper", "provider", "grounded"} <= result.keys()
    assert result["provider"] == "offline"
    assert len(result["council"]) == 5
    assert {"masthead", "date", "headlines", "editorial", "crit_sidebar"} <= result["newspaper"].keys()


def test_brief_offline(client, plan):
    pytest.importorskip("api.agent")
    responses = [client.post("/api/brief", json={"plan": plan}),
                 client.get("/api/brief", params={"plan": json.dumps(plan, ensure_ascii=False)})]
    for response in responses:
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/markdown; charset=utf-8"
        assert response.text.strip()
        assert "56.54" in response.text or "56,54" in response.text


@pytest.mark.parametrize("plan_json", ["", "{", "null", "[]", "{}"])
def test_brief_rejects_invalid_plan(client, plan_json):
    response = client.get("/api/brief", params={"plan": plan_json})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
