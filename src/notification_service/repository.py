from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import timedelta
from typing import Any

from .db import Database
from .domain import ClaimedNotification, DeliveryResult, NewNotification, Status, isoformat, utc_now


class NotificationRepository:
    def __init__(self, database: Database):
        self.database = database

    def create(self, notification: NewNotification, body: bytes) -> tuple[dict[str, Any], bool]:
        notification_id = str(uuid.uuid4())
        now = isoformat(utc_now())
        try:
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """INSERT INTO notifications
                       (id,idempotency_key,target_url,method,headers_json,body,status,
                        next_attempt_at,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (notification_id, notification.idempotency_key, notification.target_url,
                     notification.method, json.dumps(notification.headers), body,
                     Status.PENDING, now, now, now),
                )
                connection.commit()
        except sqlite3.IntegrityError:
            if not notification.idempotency_key:
                raise
            existing = self.get_by_idempotency_key(notification.idempotency_key)
            if existing is None:
                raise
            return existing, False
        return self.get(notification_id), True

    def get(self, notification_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM notifications WHERE id=?", (notification_id,)
            ).fetchone()
            return self._public(row) if row else None

    def get_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM notifications WHERE idempotency_key=?", (key,)
            ).fetchone()
            return self._public(row) if row else None

    def attempts(self, notification_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT attempt_number,outcome,status_code,error,started_at,finished_at
                   FROM delivery_attempts WHERE notification_id=? ORDER BY attempt_number""",
                (notification_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def claim(self, lease_seconds: int) -> ClaimedNotification | None:
        now_dt = utc_now()
        now = isoformat(now_dt)
        lease_until = isoformat(now_dt + timedelta(seconds=lease_seconds))
        lease_token = str(uuid.uuid4())
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE notifications SET status='pending', lease_until=NULL,
                   lease_token=NULL, updated_at=?
                   WHERE status='in_flight' AND lease_until < ?""",
                (now, now),
            )
            row = connection.execute(
                """SELECT * FROM notifications
                   WHERE status='pending' AND next_attempt_at <= ?
                   ORDER BY next_attempt_at, created_at LIMIT 1""",
                (now,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            updated = connection.execute(
                """UPDATE notifications SET status='in_flight', lease_until=?, lease_token=?,
                   attempt_count=attempt_count+1, updated_at=?
                   WHERE id=? AND status='pending'""",
                (lease_until, lease_token, now, row["id"]),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return None
            connection.commit()
            return ClaimedNotification(
                id=row["id"], lease_token=lease_token, target_url=row["target_url"], method=row["method"],
                headers=json.loads(row["headers_json"]), body=row["body"],
                attempt_count=row["attempt_count"] + 1,
            )

    def complete(
        self,
        notification: ClaimedNotification,
        result: DeliveryResult,
        started_at: str,
        next_attempt_at: str | None,
        exhausted: bool,
    ) -> None:
        now = isoformat(utc_now())
        if result.succeeded:
            status, delivered_at = Status.SUCCEEDED, now
        elif not result.retryable or exhausted:
            status, delivered_at = Status.DEAD, None
        else:
            status, delivered_at = Status.PENDING, None
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """UPDATE notifications SET status=?, next_attempt_at=?, lease_until=NULL,
                   lease_token=NULL, last_status_code=?, last_error=?, delivered_at=?, updated_at=?
                   WHERE id=? AND status='in_flight' AND lease_token=?""",
                (status, next_attempt_at or now, result.status_code, result.error,
                 delivered_at, now, notification.id, notification.lease_token),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """INSERT INTO delivery_attempts
                       (notification_id,attempt_number,outcome,status_code,error,started_at,finished_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (notification.id, notification.attempt_count, status, result.status_code,
                     result.error, started_at, now),
                )
            connection.commit()

    def retry_dead(self, notification_id: str) -> bool:
        now = isoformat(utc_now())
        with self.database.connect() as connection:
            result = connection.execute(
                """UPDATE notifications SET status='pending', attempt_count=0,
                   next_attempt_at=?, lease_until=NULL, lease_token=NULL, last_error=NULL, updated_at=?
                   WHERE id=? AND status='dead'""",
                (now, now, notification_id),
            )
            return result.rowcount == 1

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "idempotency_key": row["idempotency_key"],
            "target_url": row["target_url"], "method": row["method"],
            "status": row["status"], "attempt_count": row["attempt_count"],
            "next_attempt_at": row["next_attempt_at"],
            "last_status_code": row["last_status_code"], "last_error": row["last_error"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "delivered_at": row["delivered_at"],
        }
