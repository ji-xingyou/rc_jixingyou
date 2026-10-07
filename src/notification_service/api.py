from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .repository import NotificationRepository
from .service import NotificationService, ValidationError


LOGGER = logging.getLogger(__name__)


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "NotificationService/0.1"
    service: NotificationService
    repository: NotificationRepository
    max_request_bytes: int

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlsplit(self.path).path
        if path == "/health/live":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/health/ready":
            try:
                with self.repository.database.connect() as connection:
                    connection.execute("SELECT 1").fetchone()
                self._json(HTTPStatus.OK, {"status": "ready"})
            except Exception:
                LOGGER.exception("readiness check failed")
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"status": "unavailable"})
            return
        notification_id, suffix = self._notification_path(path)
        if notification_id and suffix in {"", "/attempts"}:
            item = self.repository.get(notification_id)
            if item is None:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "notification not found")
            elif suffix == "/attempts":
                self._json(HTTPStatus.OK, {"notification_id": notification_id, "attempts": self.repository.attempts(notification_id)})
            else:
                self._json(HTTPStatus.OK, item)
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found", "route not found")

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/v1/notifications":
            self._submit()
            return
        notification_id, suffix = self._notification_path(path)
        if notification_id and suffix == "/retry":
            if self.repository.retry_dead(notification_id):
                self._json(HTTPStatus.ACCEPTED, self.repository.get(notification_id))
            elif self.repository.get(notification_id) is None:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "notification not found")
            else:
                self._error(HTTPStatus.CONFLICT, "invalid_state", "only dead notifications can be retried")
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found", "route not found")

    def _submit(self) -> None:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self._error(HTTPStatus.LENGTH_REQUIRED, "length_required", "Content-Length is required")
            return
        try:
            length = int(raw_length)
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_length", "invalid Content-Length")
            return
        if length < 0 or length > self.max_request_bytes:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "payload_too_large", "request body is too large")
            return
        try:
            payload = json.loads(self.rfile.read(length))
            item, created = self.service.submit(payload, self.headers.get("Idempotency-Key"))
            self._json(HTTPStatus.ACCEPTED if created else HTTPStatus.OK, item)
        except json.JSONDecodeError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_json", "body must be valid JSON")
        except ValidationError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "validation_error", str(exc))

    @staticmethod
    def _notification_path(path: str) -> tuple[str | None, str | None]:
        prefix = "/v1/notifications/"
        if not path.startswith(prefix):
            return None, None
        rest = path[len(prefix):]
        parts = rest.split("/", 1)
        return parts[0] or None, ("/" + parts[1]) if len(parts) == 2 else ""

    def _json(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: HTTPStatus, code: str, message: str) -> None:
        self._json(status, {"error": {"code": code, "message": message}})

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("client=%s " + format, self.client_address[0], *args)


def create_server(host: str, port: int, service: NotificationService, repository: NotificationRepository, max_request_bytes: int) -> ThreadingHTTPServer:
    handler = type("ConfiguredApiHandler", (ApiHandler,), {
        "service": service,
        "repository": repository,
        "max_request_bytes": max_request_bytes,
    })
    return ThreadingHTTPServer((host, port), handler)

