from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace

from notification_service.config import Settings
from notification_service.db import Database
from notification_service.repository import NotificationRepository
from notification_service.service import NotificationService, ValidationError


class NotificationServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.NamedTemporaryFile(suffix=".db")
        database = Database(self.temp.name)
        database.initialize()
        self.repository = NotificationRepository(database)
        settings = replace(Settings(), db_path=self.temp.name, allowed_hosts=("example.com",))
        self.service = NotificationService(self.repository, settings)

    def tearDown(self) -> None:
        self.temp.close()

    def test_submit_and_idempotency(self) -> None:
        payload = {"target_url": "https://example.com/hook", "headers": {"X-Tenant": "acme"}, "body": {"event": "paid"}}
        first, created = self.service.submit(payload, "order-42")
        second, duplicate_created = self.service.submit(payload, "order-42")
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual("pending", first["status"])

    def test_rejects_non_allowlisted_target(self) -> None:
        with self.assertRaisesRegex(ValidationError, "allowlisted"):
            self.service.submit({"target_url": "https://evil.example/hook", "body": {}}, None)

    def test_rejects_transport_headers(self) -> None:
        with self.assertRaisesRegex(ValidationError, "transport-controlled"):
            self.service.submit({"target_url": "https://example.com", "headers": {"Host": "evil"}}, None)

    def test_accepts_raw_vendor_body(self) -> None:
        item, created = self.service.submit({
            "target_url": "https://example.com/xml",
            "headers": {"Content-Type": "application/xml"},
            "raw_body": "<event id=\"42\" />",
        }, None)
        claimed = self.repository.claim(30)
        self.assertTrue(created)
        self.assertEqual(item["id"], claimed.id)
        self.assertEqual(b'<event id="42" />', claimed.body)
        self.assertEqual("application/xml", claimed.headers["Content-Type"])

    def test_rejects_two_body_encodings(self) -> None:
        with self.assertRaisesRegex(ValidationError, "mutually exclusive"):
            self.service.submit({
                "target_url": "https://example.com", "body": {}, "raw_body": "{}"
            }, None)

    def test_claim_is_exclusive(self) -> None:
        item, _ = self.service.submit({"target_url": "https://example.com", "body": {}}, None)
        claimed = self.repository.claim(30)
        self.assertEqual(item["id"], claimed.id)
        self.assertIsNone(self.repository.claim(30))


if __name__ == "__main__":
    unittest.main()
