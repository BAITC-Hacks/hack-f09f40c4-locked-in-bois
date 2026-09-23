"""FastAPI transport for the deterministic engine and optional language agent."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from api import db
from engine import approval, optimize, score, shock, validate
from engine.model import load_dataset, load_events, measures_by_id

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

app = FastAPI(title="Кабинет акима — API")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


def _plan(body: dict):
    return body.get("plan", body)


def _engine_call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _text(body: dict, field: str, default):
    """Optional string field; anything else is a 422, never a 500 (found by tests/test_api_fuzz.py)."""
    value = body.get(field, default)
    if value is not None and not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"Поле {field} должно быть строкой")
    return value


def _valid_plan(body: dict):
    plan = _plan(body)
    valid, reason = validate.validate(plan)
    if not valid:
        raise HTTPException(status_code=422, detail=reason)
    return plan


def _validation_report(plan):
    data = load_dataset()
    measures = measures_by_id()
    counts = {direction["id"]: 0 for direction in data["directions"]}
    decisions = plan.get("decisions") if isinstance(plan, dict) else plan
    cost = 0
    for decision in decisions if isinstance(decisions, list) else []:
        mid = decision.get("measure") if isinstance(decision, dict) else None
        measure = measures.get(mid) if isinstance(mid, str) else None
        if measure is not None:
            cost += measure["cost"]
            counts[measure["direction"]] += 1
    valid, reason = validate.validate(plan)
    return {"valid": valid, "reason": reason, "cost": cost,
            "remaining": data["budget"] - cost, "direction_counts": counts}


@app.exception_handler(RequestValidationError)
async def request_validation_error(request: Request, exc: RequestValidationError):
    # The live validator always returns its contract, even for malformed bodies.
    if request.url.path == "/api/validate":
        result = _validation_report({})
        result["reason"] = "Тело запроса должно быть JSON-объектом с планом"
        return JSONResponse(result)
    return await request_validation_exception_handler(request, exc)


@app.get("/api/dataset")
def dataset():
    return FileResponse(REPO_ROOT / "data" / "dataset.json", media_type="application/json")


@app.post("/api/validate")
def validate_plan(body: dict = Body(...)):
    return _validation_report(_plan(body))


@app.post("/api/score")
def score_plan(body: dict = Body(...)):
    plan = _valid_plan(body)
    result = _engine_call(score.evaluate, plan)
    result["approval"] = _engine_call(approval.approval, plan)
    return result


@app.post("/api/approval")
def approval_plan(body: dict = Body(...)):
    return _engine_call(approval.approval, _valid_plan(body))


@app.post("/api/optimize")
def optimize_plan(body: dict = Body(...)):
    return _engine_call(optimize.optimize_info, _valid_plan(body))


@app.post("/api/analyze")
def analyze_plan(body: dict = Body(...)):
    plan = _valid_plan(body)
    from api import agent

    return _engine_call(agent.analyze, plan, lang=_text(body, "lang", "ru"))


@app.post("/api/narrative")
def narrative_plan(body: dict = Body(...)):
    plan = _valid_plan(body)
    from api import agent

    return _engine_call(agent.narrative, plan, lang=_text(body, "lang", "ru"),
                        event_id=_text(body, "event_id", None), swap=body.get("swap"))


@app.post("/api/shock")
def shock_plan(body: dict = Body(...)):
    plan = _valid_plan(body)
    seed = body.get("seed", 0)
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise HTTPException(status_code=422, detail="Seed должен быть целым числом")
    return _engine_call(shock.shock, plan, seed)


@app.post("/api/shock/resolve")
def resolve_shock(body: dict = Body(...)):
    from api import agent

    plan = _valid_plan(body)
    event_id = body.get("event_id")
    result = _engine_call(shock.resolve, plan, event_id, body.get("swap"))
    event = next(event for event in load_events() if event["id"] == event_id)
    result["approval"] = _engine_call(
        approval.approval, result["new_plan"], base_values=shock.shocked_base(event)
    )
    result["comment"] = agent.swap_comment(result)["comment"]
    return result


@app.post("/api/submit")
def submit_plan(body: dict = Body(...)):
    team = body.get("team")
    if not isinstance(team, str) or not 1 <= len(team.strip()) <= 40:
        raise HTTPException(status_code=422, detail="Название команды должно содержать от 1 до 40 символов")
    plan = _valid_plan(body)
    result = _engine_call(score.evaluate, plan)
    political = _engine_call(approval.approval, plan)
    submission_id, rank = db.add(team.strip(), result["score"], result["cost"],
                                 political["city"], plan)
    return {"ok": True, "id": submission_id, "rank": rank}


@app.get("/api/leaderboard")
def leaderboard():
    return db.top()


def _brief(body: dict):
    plan = _valid_plan(body)
    from api import agent

    md = _engine_call(agent.brief, plan)
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")


@app.get("/api/brief")
def get_brief(plan: str = ""):
    try:
        body = json.loads(plan)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="План должен быть корректным JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="План должен быть JSON-объектом")
    return _brief(body)


@app.post("/api/brief")
def post_brief(body: dict = Body(...)):
    return _brief(body)


@app.post("/api/stress")
def stress_plan(body: dict = Body(...)):
    """Crisis stress test: the plan under each crisis and all three at once + the crisis-proof plan."""
    from engine import stress

    return _engine_call(stress.stress_test, _valid_plan(body))


@app.post("/api/promise")
def promise_price(body: dict = Body(...)):
    """Price of a promise: best legal plan keeping every promise, and its cost in Score points."""
    from engine import promise

    promises = body.get("promises")
    if not isinstance(promises, list):
        raise HTTPException(status_code=422, detail="Поле promises должно быть списком обещаний")
    plan = body.get("plan")
    return _engine_call(promise.price, promises, plan)


@app.get("/api/promise/catalog")
def promise_catalog():
    from engine import promise

    return promise.promise_catalog()


@app.post("/api/grade")
def grade_plan(body: dict = Body(...)):
    """«Разбор партии»: chess-engine grading of each decision + eval bar."""
    from engine import grading

    return _engine_call(grading.grade, _valid_plan(body))


@app.get("/api/health")
def health():
    return {"ok": True, "provider": os.getenv("LLM_PROVIDER", "offline"),
            "cache": optimize.CACHE_PATH.is_file()}


if (REPO_ROOT / "web").is_dir():
    app.mount("/", StaticFiles(directory=str(REPO_ROOT / "web"), html=True), name="web")
else:
    @app.get("/", response_class=HTMLResponse)
    def frontend_placeholder():
        return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<title>Кабинет акима</title></head><body>'
                '<p>The frontend is not built yet.</p><a href="/docs">API docs</a>'
                '</body></html>')
