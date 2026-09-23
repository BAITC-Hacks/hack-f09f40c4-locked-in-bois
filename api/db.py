"""Small SQLite store for server-scored leaderboard submissions."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "leaderboard.db"


@contextmanager
def _connection():
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS submissions (
                    id INTEGER PRIMARY KEY,
                    team TEXT,
                    score REAL,
                    cost INTEGER,
                    approval REAL,
                    plan_json TEXT,
                    created_at TEXT
                )
            """)
            yield connection
    finally:
        connection.close()


def add(team, score, cost, approval, plan) -> tuple[int, int]:
    with _connection() as connection:
        created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        cursor = connection.execute(
            """INSERT INTO submissions
               (team, score, cost, approval, plan_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (team, score, cost, approval, json.dumps(plan, ensure_ascii=False), created_at),
        )
        submission_id = cursor.lastrowid
        rank = connection.execute(
            """SELECT COUNT(*) FROM submissions
               WHERE score > ? OR (score = ? AND
                   (created_at < ? OR (created_at = ? AND id <= ?)))""",
            (score, score, created_at, created_at, submission_id),
        ).fetchone()[0]
    return submission_id, rank


def top(limit=50) -> list[dict]:
    with _connection() as connection:
        rows = connection.execute(
            """SELECT team, score, cost, approval, plan_json, created_at
               FROM submissions ORDER BY score DESC, created_at ASC, id ASC LIMIT ?""",
            (limit,),
        ).fetchall()
    entries = []
    for row in rows:
        entry = dict(row)
        entry["plan"] = json.loads(entry.pop("plan_json"))
        entries.append(entry)
    return entries
