from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    value = int(os.getenv(name, default))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class Settings:
    db_path: str = "notifications.db"
    bind_host: str = "127.0.0.1"
    port: int = 8080
    worker_count: int = 2
    poll_interval_ms: int = 250
    request_timeout_seconds: int = 10
    lease_seconds: int = 30
    max_attempts: int = 8
    base_backoff_seconds: int = 2
    max_backoff_seconds: int = 3600
    max_body_bytes: int = 262_144
    allowed_hosts: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        hosts = tuple(
            host.strip().lower()
            for host in os.getenv("NOTIFY_ALLOWED_HOSTS", "").split(",")
            if host.strip()
        )
        return cls(
            db_path=os.getenv("NOTIFY_DB_PATH", "notifications.db"),
            bind_host=os.getenv("NOTIFY_BIND_HOST", "127.0.0.1"),
            port=_int("NOTIFY_PORT", 8080),
            worker_count=_int("NOTIFY_WORKERS", 2),
            poll_interval_ms=_int("NOTIFY_POLL_INTERVAL_MS", 250),
            request_timeout_seconds=_int("NOTIFY_REQUEST_TIMEOUT_SECONDS", 10),
            lease_seconds=_int("NOTIFY_LEASE_SECONDS", 30),
            max_attempts=_int("NOTIFY_MAX_ATTEMPTS", 8),
            base_backoff_seconds=_int("NOTIFY_BASE_BACKOFF_SECONDS", 2),
            max_backoff_seconds=_int("NOTIFY_MAX_BACKOFF_SECONDS", 3600),
            max_body_bytes=_int("NOTIFY_MAX_BODY_BYTES", 262_144),
            allowed_hosts=hosts,
        )

