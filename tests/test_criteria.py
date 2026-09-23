"""The five verification criteria of the case, one test each (pytest tests/test_criteria.py -v)."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from api.main import app
from engine.model import load_dataset

client = TestClient(app)
DOC = {"decisions": deepcopy(load_dataset()["reference"]["doc_example"]["decisions"])}


def test_1_same_budget_and_initial_data_for_everyone():
    first, second = client.get("/api/dataset").json(), client.get("/api/dataset").json()
    assert first == second and first["budget"] == 100
    assert client.post("/api/score", json=DOC).json()["baseline"] == 52.56


def test_2_budget_cannot_be_exceeded():
    over = {"decisions": [{"measure": "M3", "district": "Нура"}, {"measure": "M13", "district": "Алматы"},
                          {"measure": "M7", "district": "Есиль"}, {"measure": "M5", "district": "Сарыарка"},
                          {"measure": "M2", "district": None}]}  # 30 + 28 + 24 + 25 + 22 = 129
    check = client.post("/api/validate", json=over).json()
    assert check["valid"] is False and check["cost"] == 129 and "бюджет" in check["reason"].lower()
    assert client.post("/api/score", json=over).status_code == 422
    assert client.post("/api/submit", json={"team": "x", "plan": over}).status_code == 422


def test_3_decisions_change_the_indicators():
    nura = client.post("/api/score", json=DOC).json()["districts"]["Нура"]
    s1 = 4  # T1 T2 E1 E2 [S1] ...
    assert nura["before"][s1] == 38 and nura["after"][s1] == 48
    assert nura["D_after"] > nura["D_before"]


def test_4_ai_explains_result_and_tradeoffs():
    analysis = client.post("/api/analyze", json=DOC).json()
    for field in ("summary", "strengths", "risks", "consequences", "tradeoffs"):
        assert analysis[field], field
    assert analysis["grounded"] is True
    assert analysis["recommendation"]["expected_score"] >= 56.54


@pytest.mark.parametrize("swap_in", [{"measure": "M4", "district": "Есиль"}, {"measure": "M14", "district": None}])
def test_5_changing_decisions_changes_the_score(swap_in):
    base = client.post("/api/score", json=DOC).json()["score"]
    changed = deepcopy(DOC)
    changed["decisions"][4] = swap_in  # replace M5 Сарыарка
    assert client.post("/api/score", json=changed).json()["score"] != base
