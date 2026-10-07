from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


class Status(StrEnum):
    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    SUCCEEDED = "succeeded"
    DEAD = "dead"


@dataclass(frozen=True)
class NewNotification:
    target_url: str
    method: str
    headers: dict[str, str]
    body: Any
    idempotency_key: str | None


@dataclass(frozen=True)
class ClaimedNotification:
    id: str
    lease_token: str
    target_url: str
    method: str
    headers: dict[str, str]
    body: bytes
    attempt_count: int


@dataclass(frozen=True)
class DeliveryResult:
    succeeded: bool
    retryable: bool
    status_code: int | None = None
    error: str | None = None
    retry_after_seconds: int | None = None
