from __future__ import annotations

import tempfile
import threading
import unittest
from dataclasses import replace

from notification_service.config import Settings
from notification_service.db import Database
from notification_service.domain import DeliveryResult
from notification_service.repository import NotificationRepository
from notification_service.service import NotificationService
from notification_service.worker import DeliveryWorker


class FakeClient:
    def __init__(self, result: DeliveryResult):
        self.result = result
        self.called = threading.Event()

    def deliver(self, notification):
        self.called.set()
        return self.result


class WorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.NamedTemporaryFile(suffix=".db")
        database = Database(self.temp.name)
        database.initialize()
        self.repository = NotificationRepository(database)
        self.settings = replace(Settings(), db_path=self.temp.name, allowed_hosts=("example.com",), poll_interval_ms=10)
        self.service = NotificationService(self.repository, self.settings)

    def tearDown(self) -> None:
        self.temp.close()

    def _run_once(self, result: DeliveryResult):
        item, _ = self.service.submit({"target_url": "https://example.com", "body": {"ok": True}}, None)
        client = FakeClient(result)
        stop = threading.Event()
        thread = threading.Thread(target=DeliveryWorker(self.repository, client, self.settings).run, args=(stop,))
        thread.start()
        self.assertTrue(client.called.wait(1))
        stop.set()
        thread.join(1)
        return self.repository.get(item["id"])

    def test_success(self) -> None:
        item = self._run_once(DeliveryResult(True, False, status_code=204))
        self.assertEqual("succeeded", item["status"])
        self.assertEqual(1, item["attempt_count"])

    def test_non_retryable_response_goes_dead(self) -> None:
        item = self._run_once(DeliveryResult(False, False, status_code=400, error="bad request"))
        self.assertEqual("dead", item["status"])

    def test_retryable_response_is_rescheduled(self) -> None:
        item = self._run_once(DeliveryResult(False, True, status_code=503, error="unavailable"))
        self.assertEqual("pending", item["status"])
        self.assertGreater(item["next_attempt_at"], item["updated_at"])

    def test_stale_lease_cannot_complete_newer_claim(self) -> None:
        item, _ = self.service.submit({"target_url": "https://example.com", "body": {}}, None)
        stale = self.repository.claim(30)
        from dataclasses import replace as replace_claim
        forged_stale = replace_claim(stale, lease_token="expired-token")
        self.repository.complete(
            forged_stale, DeliveryResult(True, False, status_code=204),
            item["created_at"], item["created_at"], False,
        )
        current = self.repository.get(item["id"])
        self.assertEqual("in_flight", current["status"])
        self.assertEqual([], self.repository.attempts(item["id"]))


if __name__ == "__main__":
    unittest.main()
