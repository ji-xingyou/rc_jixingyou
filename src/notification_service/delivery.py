from __future__ import annotations

import http.client
import socket
import ssl
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from .domain import ClaimedNotification, DeliveryResult, utc_now


RETRYABLE_STATUS_CODES = {408, 425, 429}


class HttpDeliveryClient:
    def __init__(self, timeout_seconds: int, max_response_bytes: int = 4096):
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    def deliver(self, notification: ClaimedNotification) -> DeliveryResult:
        parts = urlsplit(notification.target_url)
        connection_type = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(
            parts.hostname,
            parts.port,
            timeout=self.timeout_seconds,
            context=ssl.create_default_context() if parts.scheme == "https" else None,
        ) if parts.scheme == "https" else connection_type(parts.hostname, parts.port, timeout=self.timeout_seconds)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        headers = {"User-Agent": "notification-service/0.1", **notification.headers}
        headers["X-Notification-Id"] = notification.id
        headers["X-Notification-Attempt"] = str(notification.attempt_count)
        try:
            connection.request(notification.method, path, body=notification.body, headers=headers)
            response = connection.getresponse()
            response.read(self.max_response_bytes)
            if 200 <= response.status < 300:
                return DeliveryResult(succeeded=True, retryable=False, status_code=response.status)
            retryable = response.status >= 500 or response.status in RETRYABLE_STATUS_CODES
            return DeliveryResult(
                succeeded=False, retryable=retryable, status_code=response.status,
                error=f"upstream returned HTTP {response.status}",
                retry_after_seconds=self._retry_after(response.getheader("Retry-After")),
            )
        except (OSError, socket.timeout, http.client.HTTPException) as exc:
            return DeliveryResult(False, True, error=f"{type(exc).__name__}: {exc}")
        finally:
            connection.close()

    @staticmethod
    def _retry_after(value: str | None) -> int | None:
        if not value:
            return None
        try:
            return max(0, int(value))
        except ValueError:
            try:
                return max(0, int((parsedate_to_datetime(value) - utc_now()).total_seconds()))
            except (TypeError, ValueError):
                return None
