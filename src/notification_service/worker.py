from __future__ import annotations

import hashlib
import logging
import threading
import time
from datetime import timedelta

from .config import Settings
from .delivery import HttpDeliveryClient
from .domain import isoformat, utc_now
from .repository import NotificationRepository


LOGGER = logging.getLogger(__name__)


class DeliveryWorker:
    def __init__(self, repository: NotificationRepository, client: HttpDeliveryClient, settings: Settings):
        self.repository = repository
        self.client = client
        self.settings = settings

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            notification = self.repository.claim(self.settings.lease_seconds)
            if notification is None:
                stop_event.wait(self.settings.poll_interval_ms / 1000)
                continue
            started_at = isoformat(utc_now())
            result = self.client.deliver(notification)
            exhausted = notification.attempt_count >= self.settings.max_attempts
            delay = self._backoff_seconds(notification.id, notification.attempt_count)
            if result.retry_after_seconds is not None:
                delay = min(self.settings.max_backoff_seconds, max(delay, result.retry_after_seconds))
            next_attempt_at = isoformat(utc_now() + timedelta(seconds=delay))
            self.repository.complete(notification, result, started_at, next_attempt_at, exhausted)
            LOGGER.info(
                "delivery completed id=%s attempt=%d succeeded=%s retryable=%s status_code=%s",
                notification.id, notification.attempt_count, result.succeeded,
                result.retryable, result.status_code,
            )

    def _backoff_seconds(self, notification_id: str, attempt: int) -> float:
        base = min(self.settings.max_backoff_seconds, self.settings.base_backoff_seconds * (2 ** (attempt - 1)))
        # Stable 0.8..1.2 jitter avoids synchronized retry storms and keeps tests deterministic.
        byte = hashlib.sha256(f"{notification_id}:{attempt}".encode()).digest()[0]
        return base * (0.8 + byte / 255 * 0.4)


def start_workers(repository: NotificationRepository, settings: Settings, stop_event: threading.Event) -> list[threading.Thread]:
    threads = []
    for index in range(settings.worker_count):
        worker = DeliveryWorker(
            repository,
            HttpDeliveryClient(settings.request_timeout_seconds),
            settings,
        )
        thread = threading.Thread(
            target=worker.run, args=(stop_event,), name=f"delivery-worker-{index + 1}", daemon=True
        )
        thread.start()
        threads.append(thread)
    return threads

