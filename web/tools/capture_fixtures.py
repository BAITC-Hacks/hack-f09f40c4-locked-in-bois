"""Capture real API responses into web/mock/ so ?mock=1 works without a backend.

Usage (backend running on :8000):  python web/tools/capture_fixtures.py [base_url]
"""
import json
import sys
import urllib.request
from pathlib import Path

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
OUT = Path(__file__).resolve().parents[1] / "mock"
OUT.mkdir(exist_ok=True)


def call(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def save(name, obj):
    (OUT / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{name}.json")


dataset = call("GET", "/api/dataset")
save("dataset", dataset)
plan = dataset["reference"]["doc_example"]
plan = {"decisions": plan["decisions"]}
body = {"plan": plan, "lang": "ru"}
save("score", call("POST", "/api/score", body))
save("optimize", call("POST", "/api/optimize", body))
save("analyze", call("POST", "/api/analyze", body))
save("narrative", call("POST", "/api/narrative", body))
shock = call("POST", "/api/shock", {"plan": plan, "seed": 1})
save("shock", shock)
event_id = shock["event"]["id"]
best = shock.get("best_swaps") or []
swap = {"out": best[0]["out"], "in": best[0]["in"]} if best else {"out": "M12", "in": {"measure": "M14", "district": None}}
save("resolve", call("POST", "/api/shock/resolve", {"plan": plan, "event_id": event_id, "swap": swap}))
save("narrative_crisis", call("POST", "/api/narrative", {**body, "event_id": event_id, "swap": swap}))
save("leaderboard", call("GET", "/api/leaderboard"))
