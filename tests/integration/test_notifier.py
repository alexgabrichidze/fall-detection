import io
import json
from threading import Event
import unittest
from urllib.error import HTTPError, URLError

from fall_detection.contracts import DetectionSource
from fall_detection.notifier import (
    DeliveryResult, DeliveryStatus, NotificationWorker, TelegramConfig, TelegramSender,
)
from fall_detection.safety import Incident


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.config = TelegramConfig("123:SECRET", "42")
        self.incident = Incident("incident-1", DetectionSource.LIVE, 10, 15)

    def test_request_contains_only_chat_and_minimal_text_metadata(self):
        calls = []

        def opener(request, *, timeout):
            calls.append((request, timeout))
            return io.BytesIO(b'{"ok":true,"result":{"message_id":1}}')

        result = TelegramSender(self.config, opener=opener)(self.incident)
        self.assertEqual(result.status, DeliveryStatus.ACCEPTED)
        self.assertEqual(len(calls), 1)
        request, timeout = calls[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(timeout, 5)
        body = json.loads(request.data)
        self.assertEqual(set(body), {"chat_id", "text"})
        self.assertIn("incident-1", body["text"])
        self.assertNotIn("SECRET", body["text"])
        self.assertNotIn("SECRET", repr(self.config))

    def test_simulation_can_never_reach_real_sender(self):
        def forbidden(*args, **kwargs):
            self.fail("network reached from simulated incident")
        result = TelegramSender(self.config, opener=forbidden)(
            Incident("fake", DetectionSource.SIMULATED, 0, 5))
        self.assertEqual(result.status, DeliveryStatus.FAILED)

    def test_rejection_timeout_and_invalid_response_have_distinct_outcomes(self):
        def timeout(*args, **kwargs):
            raise URLError("secret URL must not escape")
        self.assertEqual(TelegramSender(self.config, opener=timeout)(self.incident).status,
                         DeliveryStatus.UNCERTAIN)
        for payload, expected in [(b'{"ok":false}', DeliveryStatus.FAILED),
                                  (b'invalid', DeliveryStatus.UNCERTAIN),
                                  (b'[]', DeliveryStatus.UNCERTAIN),
                                  (b'{"ok":true}', DeliveryStatus.UNCERTAIN)]:
            with self.subTest(payload=payload):
                sender = TelegramSender(self.config, opener=lambda *a, **kw: io.BytesIO(payload))
                self.assertEqual(sender(self.incident).status, expected)
        for code, expected in [(401, DeliveryStatus.FAILED), (503, DeliveryStatus.UNCERTAIN)]:
            def http_error(*a, **kw):
                raise HTTPError("secret", code, "secret", {}, None)
            self.assertEqual(TelegramSender(self.config, opener=http_error)(self.incident).status, expected)

    def test_test_message_is_explicitly_labelled(self):
        def opener(request, **kwargs):
            self.assertIn("TEST ONLY", json.loads(request.data)["text"])
            return io.BytesIO(b'{"ok":true,"result":{}}')
        self.assertEqual(TelegramSender(self.config, opener=opener).send_test(), DeliveryStatus.ACCEPTED)

    def test_worker_is_bounded_and_returns_sanitized_failure_without_retry(self):
        entered, release = Event(), Event()
        calls = []

        def slow_sender(incident):
            calls.append(incident)
            entered.set()
            release.wait(2)
            raise RuntimeError("sensitive transport exception")

        worker = NotificationWorker(slow_sender)
        try:
            self.assertTrue(worker.submit(self.incident))
            self.assertTrue(entered.wait(1))
            self.assertFalse(worker.submit(self.incident))
            self.assertIsNone(worker.poll())
            self.assertFalse(worker.close(timeout=0))
        finally:
            release.set()
            worker.close(timeout=1)
        result = worker.poll()
        self.assertEqual(result, DeliveryResult("incident-1", DeliveryStatus.UNCERTAIN))
        self.assertEqual(len(calls), 1)
        self.assertFalse(worker.submit(self.incident))
