"""Raw HTTP regressions for every POST route, without network LLM calls."""

from copy import deepcopy
import json
import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api import db
from api.main import app
from engine.model import load_dataset, load_events


POST_ROUTES = tuple(sorted(
    route.path for route in app.routes
    if isinstance(route, APIRoute) and "POST" in route.methods
))
UNICODE_DETAIL = "Недопустимый Unicode в запросе"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(db, "DB_PATH", tmp_path_factory.mktemp("hardening") / "leaderboard.db")
        patch.setenv("LLM_PROVIDER", "offline")
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


def _body(route):
    plan = {"decisions": deepcopy(load_dataset()["reference"]["doc_example"]["decisions"])}
    body = {"plan": plan}
    if route == "/api/duel":
        body = {"plan_a": plan, "plan_b": deepcopy(plan)}
    elif route == "/api/submit":
        body["team"] = "Команда"
    elif route == "/api/promise":
        body["promises"] = []
    elif route == "/api/shock/resolve":
        body.update(event_id=load_events()[0]["id"], swap={
            "out": "M7", "in": {"measure": "M13", "district": "Алматы"},
        })
    return body


def _post(client, route, content):
    return client.post(route, content=content, headers={"Content-Type": "application/json"})


def _assert_error(response, status):
    assert response.status_code == status, response.text
    result = response.json()
    assert set(result) == {"detail"}
    assert isinstance(result["detail"], str)
    assert re.search("[А-Яа-яЁё]", result["detail"])
    assert "input" not in result["detail"]
    return result["detail"]


@pytest.mark.parametrize("route", POST_ROUTES)
@pytest.mark.parametrize("token", [b"NaN", b"Infinity", b"-Infinity", b"1e999", b"-1e999"])
@pytest.mark.parametrize("nested", [False, True], ids=["root", "nested"])
def test_nonfinite_numbers_are_russian_422(client, route, token, nested):
    content = token
    if nested:
        body = _body(route)
        body["extra"] = [None, {"number": "RAW_NUMBER"}]
        content = json.dumps(body).encode().replace(b'"RAW_NUMBER"', token)
    detail = _assert_error(_post(client, route, content), 422)
    assert token.decode() not in detail


@pytest.mark.parametrize("route", POST_ROUTES)
@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff", "before\ud800after", "\udc00\ud800"])
@pytest.mark.parametrize("location", [
    "root", "list", "key", "nested_key", "nested_value", "measure", "district", "team",
])
def test_unpaired_surrogates_are_rejected_everywhere(client, monkeypatch, route, surrogate, location):
    def unexpected_storage(*args, **kwargs):
        pytest.fail("Unsafe input reached storage")

    monkeypatch.setattr(db, "add", unexpected_storage)
    body = _body(route)
    plan = body["plan_a" if route == "/api/duel" else "plan"]
    if location == "root":
        body = surrogate
    elif location == "list":
        body = [surrogate]
    elif location == "key":
        body[surrogate] = "ignored"
    elif location == "nested_key":
        body["extra"] = [{surrogate: "ignored"}]
    elif location == "nested_value":
        body["extra"] = [{"value": [surrogate]}]
    elif location == "team":
        body["team"] = surrogate
    else:
        plan["decisions"][0][location] = surrogate
    content = json.dumps(body, ensure_ascii=True).encode()
    assert _assert_error(_post(client, route, content), 422) == UNICODE_DETAIL


@pytest.mark.parametrize("route", POST_ROUTES)
@pytest.mark.parametrize("content,status", [
    (b"", 422), (b" \n\t", 422), (b"{", 422), (b"[]", 422),
    (b"null", 422), (b'"do-not-echo"', 422),
    (b'{"secret":"do-not-echo",}', 422),
    (b"\xff", 400), (b'{"team":"\xff"}', 400),
    (b'{"team":"\xed\xa0\x80"}', 422),
    pytest.param(b"9" * 5000, 400, id="integer-decoding-limit"),
])
def test_transport_errors_are_russian(client, route, content, status):
    detail = _assert_error(_post(client, route, content), status)
    assert "do-not-echo" not in detail


@pytest.mark.parametrize("route", POST_ROUTES)
@pytest.mark.parametrize("escaped", [False, True], ids=["utf8", "json-escapes"])
def test_valid_unicode_and_documented_bodies_still_work(client, route, escaped):
    body = _body(route)
    body["extra"] = {"Қазақша 🏙": ["日本語", "е\u0308", "\u0000"]}
    if route == "/api/submit":
        body["team"] = "Астана 🏙"
    content = json.dumps(body, ensure_ascii=escaped).encode("utf-8")
    response = _post(client, route, content)
    assert response.status_code == 200, response.text
    if route == "/api/score":
        assert (response.json()["score"], response.json()["cost"]) == (56.54, 95)
    elif route == "/api/submit":
        assert any(row["team"] == body["team"] for row in client.get("/api/leaderboard").json())


@pytest.mark.parametrize("wrapped", [False, True])
def test_validate_keeps_partial_and_unknown_measure_reports(client, wrapped):
    plan = _body("/api/validate")["plan"]
    for decisions, cost in [(plan["decisions"][:3], 56), ([], 0)]:
        partial = {"decisions": decisions}
        body = {"plan": partial} if wrapped else partial
        response = _post(client, "/api/validate", json.dumps(body).encode())
        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is False
        assert result["cost"] == cost
        assert result["remaining"] == load_dataset()["budget"] - cost
        assert isinstance(result["direction_counts"], dict)
        assert re.search("[А-Яа-яЁё]", result["reason"])
    plan["decisions"][0]["measure"] = "M404"
    body = {"plan": plan} if wrapped else plan
    response = _post(client, "/api/validate", json.dumps(body).encode())
    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["cost"] == 71
    assert "Неизвестная мера" in response.json()["reason"]


def test_business_http_errors_are_preserved(client):
    response = _post(client, "/api/submit", b'{"team":""}')
    assert _assert_error(response, 422) == "Название команды должно содержать от 1 до 40 символов"


def test_get_brief_rejects_escaped_surrogate(client):
    body = _body("/api/brief")
    body["plan"]["decisions"][0]["measure"] = "\ud800"
    response = client.get("/api/brief", params={"plan": json.dumps(body)})
    assert _assert_error(response, 422) == UNICODE_DETAIL
