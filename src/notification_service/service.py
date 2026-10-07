from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlsplit

from .config import Settings
from .domain import NewNotification
from .repository import NotificationRepository


class ValidationError(ValueError):
    pass


class NotificationService:
    ALLOWED_METHODS = {"POST", "PUT", "PATCH"}
    FORBIDDEN_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}

    def __init__(self, repository: NotificationRepository, settings: Settings):
        self.repository = repository
        self.settings = settings

    def submit(self, payload: object, idempotency_key: str | None) -> tuple[dict, bool]:
        if not isinstance(payload, dict):
            raise ValidationError("request body must be a JSON object")
        target_url = payload.get("target_url")
        method = str(payload.get("method", "POST")).upper()
        headers = payload.get("headers", {})
        if not isinstance(target_url, str):
            raise ValidationError("target_url is required")
        self._validate_url(target_url)
        if method not in self.ALLOWED_METHODS:
            raise ValidationError(f"method must be one of {sorted(self.ALLOWED_METHODS)}")
        if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
            raise ValidationError("headers must be a string-to-string object")
        if any(k.lower() in self.FORBIDDEN_HEADERS for k in headers):
            raise ValidationError("headers contain a hop-by-hop or transport-controlled header")
        if idempotency_key is not None and not (1 <= len(idempotency_key) <= 128):
            raise ValidationError("Idempotency-Key must be 1..128 characters")
        if "body" in payload and "raw_body" in payload:
            raise ValidationError("body and raw_body are mutually exclusive")
        if "raw_body" in payload:
            if not isinstance(payload["raw_body"], str):
                raise ValidationError("raw_body must be a UTF-8 string")
            body = payload["raw_body"].encode()
            default_content_type = "text/plain; charset=utf-8"
        else:
            body = json.dumps(payload.get("body"), ensure_ascii=False, separators=(",", ":")).encode()
            default_content_type = "application/json"
        if len(body) > self.settings.max_body_bytes:
            raise ValidationError(f"serialized body exceeds {self.settings.max_body_bytes} bytes")
        if not any(key.lower() == "content-type" for key in headers):
            headers = {**headers, "Content-Type": default_content_type}
        notification = NewNotification(target_url, method, headers, payload.get("body"), idempotency_key)
        return self.repository.create(notification, body)

    def _validate_url(self, target_url: str) -> None:
        parts = urlsplit(target_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValidationError("target_url must be an http(s) URL without embedded credentials")
        hostname = parts.hostname.lower()
        if self.settings.allowed_hosts and hostname not in self.settings.allowed_hosts:
            raise ValidationError("target host is not allowlisted")
        if not self.settings.allowed_hosts:
            try:
                address = ipaddress.ip_address(hostname)
                if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
                    raise ValidationError("private/reserved IP targets require an explicit host allowlist")
            except ValueError:
                pass
