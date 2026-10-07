from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    target_url TEXT NOT NULL,
    method TEXT NOT NULL,
    headers_json TEXT NOT NULL,
    body BLOB NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','in_flight','succeeded','dead')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    lease_until TEXT,
    lease_token TEXT,
    last_status_code INTEGER,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    delivered_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_notifications_claim
    ON notifications(status, next_attempt_at, created_at);

CREATE TABLE IF NOT EXISTS delivery_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id TEXT NOT NULL REFERENCES notifications(id),
    attempt_number INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    status_code INTEGER,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_notification
    ON delivery_attempts(notification_id, attempt_number);
"""


class Database:
    def __init__(self, path: str):
        self.path = path

    def initialize(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        if self.path != ":memory:":
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
        try:
            yield connection
        finally:
            connection.close()
