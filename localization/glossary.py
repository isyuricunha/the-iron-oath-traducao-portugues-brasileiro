"""Project-local smart glossary (SQLite) with selective worker routing."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

GLOSSARY_STATUSES = (
    "locked",
    "confirmed",
    "provisional",
    "challenged",
    "conflicted",
    "preserved",
    "deprecated",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS glossary_entries (
    term TEXT NOT NULL,
    sense TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'provisional',
    confidence REAL NOT NULL DEFAULT 0.5,
    target TEXT,
    aliases TEXT NOT NULL DEFAULT '[]',
    evidence TEXT NOT NULL DEFAULT '[]',
    linked_units TEXT NOT NULL DEFAULT '[]',
    generation TEXT,
    revision TEXT NOT NULL DEFAULT '0',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (term, sense)
);
CREATE TABLE IF NOT EXISTS glossary_tasks (
    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def normalize(term: str) -> str:
    return " ".join(term.strip().lower().split())


class Glossary:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def upsert(
        self,
        term: str,
        sense: str,
        status: str,
        target: str | None = None,
        confidence: float = 0.5,
        aliases: list[str] | None = None,
        evidence: list[str] | None = None,
        linked_units: list[str] | None = None,
        generation: str | None = None,
        revision: str = "0",
    ) -> None:
        if status not in GLOSSARY_STATUSES:
            raise ValueError(f"unknown glossary status {status!r}")
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO glossary_entries VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    normalize(term),
                    sense,
                    status,
                    confidence,
                    target,
                    json.dumps(aliases or [], ensure_ascii=False),
                    json.dumps(evidence or [], ensure_ascii=False),
                    json.dumps(linked_units or [], ensure_ascii=False),
                    generation,
                    revision,
                    now_iso(),
                ),
            )
            self.conn.commit()

    def relevant(self, texts: list[str], limit: int = 40) -> list[dict]:
        """Bounded relevant view: exact normalized matches + phrase overlap.

        Locked entries always win when they match.
        """
        joined = " ".join(texts).lower()
        with self.lock:
            rows = self.conn.execute("SELECT * FROM glossary_entries").fetchall()
        hits: list[dict] = []
        for row in rows:
            term = row["term"]
            score = 0.0
            if term in joined:
                score = 1.0
            else:
                for alias in json.loads(row["aliases"] or "[]"):
                    if alias.lower() in joined:
                        score = 0.8
                        break
            if score:
                hits.append(dict(row))
        hits.sort(key=lambda e: (e["status"] == "locked", e["confidence"]), reverse=True)
        return hits[:limit]

    def enqueue(self, kind: str, payload: dict) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO glossary_tasks (kind, status, payload, created_at) VALUES (?,?,?,?)",
                (kind, "pending", json.dumps(payload, ensure_ascii=False), now_iso()),
            )
            self.conn.commit()

    def next_task(self) -> dict | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM glossary_tasks WHERE status='pending' ORDER BY task_id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            self.conn.execute(
                "UPDATE glossary_tasks SET status='running' WHERE task_id=?",
                (row["task_id"],),
            )
            self.conn.commit()
        task = dict(row)
        task["payload"] = json.loads(task["payload"])
        return task

    def finish_task(self, task_id: int, result: dict | None = None) -> None:
        with self.lock:
            self.conn.execute(
                "UPDATE glossary_tasks SET status='done', payload=? WHERE task_id=?",
                (json.dumps(result or {}, ensure_ascii=False), task_id),
            )
            self.conn.commit()

    def outstanding(self) -> int:
        with self.lock:
            row = self.conn.execute(
                "SELECT COUNT(*) AS c FROM glossary_tasks WHERE status != 'done'"
            ).fetchone()
        return int(row["c"])