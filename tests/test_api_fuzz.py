"""Deterministic malformed HTTP inputs; no random generation or real LLM calls.

Only observed server failures belong in KNOWN_BUGS. Strict xfails must become
XPASS failures when the corresponding product bug is fixed.
"""

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
LONG = "я" * 10_000
UNICODE = "Қазақша / 日本語 / 🏙️ / е\u0308 / \u0000"
BAD_VALUES = (
    ("null", None), ("empty-string", ""), ("string", "garbage"),
    ("negative", -1), ("float", 1.5), ("bool", True),
    ("list", []), ("object", {}), ("nested", {"bad": [None, {"x": []}]}),
    ("huge-string", LONG), ("unicode", UNICODE),
)


def _plan():
    return {"decisions": deepcopy(load_dataset()["reference"]["doc_example"]["decisions"])}


def _body(route):
    """Supply valid siblings so one malformed field reaches its own validator."""
    body = {"plan": _plan()}
    if route == "/api/submit":
        body["team"] = "Фаззинг"
    elif route == "/api/promise":
        body["promises"] = []
    elif route == "/api/shock/resolve":
        body.update(event_id=load_events()[0]["id"], swap={
            "out": "M7", "in": {"measure": "M13", "district": "Алматы"},
        })
    return body


def _cases():
    """Yield (route, readable case name, exact request bytes) in stable order."""
    def encoded(route, name, body):
        return route, name, json.dumps(body, ensure_ascii=True).encode("utf-8")

    for route in POST_ROUTES:
        for name, content in (
            ("empty-body", b""), ("whitespace-body", b" \n\t"),
            ("truncated-json", b'{"plan":'), ("trailing-comma", b'{"plan":{},}'),
            ("invalid-utf8", b"\xff"),
        ):
            yield route, name, content
        for name, value in (("empty-object", {}), ("nonempty-list", [None, {}, []]), *BAD_VALUES):
            yield encoded(route, "body-" + name, value)

        plans = [("missing-decisions", {}), ("nested-plan", {"plan": _plan()})]
        plans.extend(("plan-" + name, value) for name, value in BAD_VALUES)
        plans.extend(("decisions-" + name, {"decisions": value}) for name, value in BAD_VALUES)
        for count in (0, 4, 6, 100):
            decisions = _plan()["decisions"]
            plans.append((f"decision-count-{count}", {
                "decisions": (decisions * (count // len(decisions) + 1))[:count],
            }))
        for name, value in BAD_VALUES:
            decisions = _plan()["decisions"]
            decisions[0] = value
            plans.append(("decision-item-" + name, {"decisions": decisions}))
        for field in ("measure", "district"):
            for name, value in (("unknown", "unknown-id"), *BAD_VALUES):
                plan = _plan()
                plan["decisions"][0][field] = value
                plans.append((field + "-" + name, plan))
            plan = _plan()
            del plan["decisions"][0][field]
            plans.append(("missing-" + field, plan))
        for name, plan in plans:
            body = _body(route)
            body["plan"] = plan
            yield encoded(route, "wrapped-" + name, body)
            # The promise endpoint accepts only the wrapped optional plan.
            if isinstance(plan, dict) and route != "/api/promise":
                bare = _body(route)
                del bare["plan"]
                bare.update(plan)
                yield encoded(route, "bare-" + name, bare)

    for route, fields in (
        ("/api/shock", ("seed",)),
        ("/api/analyze", ("lang",)),
        ("/api/narrative", ("lang", "event_id")),
        ("/api/submit", ("team", "score")),
        ("/api/shock/resolve", ("event_id",)),
        ("/api/promise", ("promises",)),
    ):
        for field in fields:
            body = _body(route)
            body.pop(field, None)
            yield encoded(route, field + "-missing", body)
            for name, value in BAD_VALUES:
                body = _body(route)
                body[field] = value
                yield encoded(route, field + "-" + name, body)

    for name, value in (("integer-string", "42"), ("integral-float", 42.0),
                        ("false", False), ("huge-integer", 10**100)):
        yield encoded("/api/shock", "seed-" + name, {"plan": _plan(), "seed": value})
    for name, value in (("whitespace", " \n\t"), ("41-characters", "я" * 41),
                        ("40-characters", "я" * 40), ("padded-huge", " " * 10_000 + "А")):
        yield encoded("/api/submit", "team-" + name, {"plan": _plan(), "team": value})

    for route in ("/api/shock/resolve", "/api/narrative"):
        swaps = [("missing", None), *BAD_VALUES]
        swaps.extend((name, value) for name, value in (
            ("missing-in", {"out": "M7"}),
            ("missing-out", {"in": {"measure": "M13", "district": "Алматы"}}),
            ("unchanged", {"out": "M7", "in": _plan()["decisions"][0]}),
        ))
        for name, value in BAD_VALUES:
            swaps.append(("out-" + name, {"out": value, "in": {"measure": "M14"}}))
            swaps.append(("in-" + name, {"out": "M7", "in": value}))
            for field in ("measure", "district"):
                incoming = {"measure": "M13", "district": "Алматы", field: value}
                swaps.append(("in-" + field + "-" + name, {"out": "M7", "in": incoming}))
        for name, swap in swaps:
            body = {"plan": _plan(), "event_id": load_events()[0]["id"]}
            if name != "missing":
                body["swap"] = swap
            yield encoded(route, "swap-" + name, body)

    for name, item in BAD_VALUES:
        # Both a bad first item and a bad later item must be checked.
        for prefix in ([], [{"type": "no_critical"}]):
            yield encoded("/api/promise", f"promise-item-{len(prefix)}-{name}", {
                "promises": prefix + [item], "plan": _plan(),
            })
    promise_shapes = (
        ("include", {"measure": "M7", "district": "Нура"}),
        ("exclude", {"measure": "M7"}),
        ("district_project", {"district": "Нура"}),
        ("min_districts", {"value": 1}),
        ("max_cost", {"value": load_dataset()["budget"]}),
        ("min_approval", {"value": 50}),
        ("min_district_delta", {"district": "Нура", "value": 1}),
        ("no_critical", {}),
    )
    for kind, fields in promise_shapes:
        original = {"type": kind, **fields}
        variants = [("unknown-field", {**original, "garbage": {"nested": [None]}})]
        for field in original:
            missing = dict(original)
            del missing[field]
            variants.append((field + "-missing", missing))
            variants.extend((field + "-" + name, {**original, field: value})
                            for name, value in BAD_VALUES)
        for name, promise in variants:
            yield encoded("/api/promise", kind + "-" + name, {"promises": [promise]})


CASES = tuple(_cases())
# Keys are (route, case name); values must document a reproduced traceback.
KNOWN_BUGS = {}  # all bugs found by this file are fixed (lang/event_id type check in api/main.py)


class ServerFailure(AssertionError):
    """Only a server response failure may satisfy a known-bug xfail."""


def _parameter(route, name, content):
    reason = KNOWN_BUGS.get((route, name))
    marks = (pytest.mark.xfail(strict=True, reason=reason, raises=ServerFailure),) if reason else ()
    return pytest.param(route, content, id=route + "-" + name, marks=marks)


@pytest.fixture(scope="module")
def fuzz_client(tmp_path_factory):
    # One isolated database and ASGI portal suffice for this stateless corpus;
    # avoid thousands of temporary directories/threads in concurrent suite runs.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(db, "DB_PATH", tmp_path_factory.mktemp("api-fuzz") / "leaderboard.db")
        patch.setenv("LLM_PROVIDER", "offline")
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


def test_fuzz_corpus_covers_every_post_route():
    assert POST_ROUTES
    assert {route for route, _, _ in CASES} == set(POST_ROUTES)
    identities = [(route, name) for route, name, _ in CASES]
    assert len(identities) == len(set(identities))
    assert set(KNOWN_BUGS) <= set(identities)


@pytest.mark.parametrize("route,content", [_parameter(*case) for case in CASES])
def test_post_malformed_input_never_500(fuzz_client, route, content):
    response = fuzz_client.post(route, content=content, headers={"Content-Type": "application/json"})
    if response.status_code >= 500:
        raise ServerFailure(f"POST {route}: HTTP {response.status_code}: {response.text[:500]}")
    assert response.status_code == 200 or 400 <= response.status_code < 500
    try:
        body = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        assert response.status_code in (400, 422)
    if response.status_code == 200 and route == "/api/brief":
        assert response.headers["content-type"].startswith("text/markdown")
        assert response.text.strip()
        return
    assert response.headers["content-type"].startswith("application/json")
    result = response.json()
    assert isinstance(result, dict)
    if response.status_code >= 400:
        assert response.status_code in (400, 422)
        assert "detail" in result
        detail = result["detail"]
        assert isinstance(detail, str)
        assert re.search("[А-Яа-яЁё]", detail), detail
    elif route == "/api/validate" and result.get("valid") is False:
        assert isinstance(result.get("reason"), str)
        assert re.search("[А-Яа-яЁё]", result["reason"])
