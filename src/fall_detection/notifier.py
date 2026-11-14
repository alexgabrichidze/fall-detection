"""Text-only Telegram transport and a bounded background notification worker.

Exception text, URLs, credentials, and server descriptions must never be logged.
A timeout is uncertain delivery, not proof that Telegram rejected a message.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from http.client import HTTPException
import json
from math import isfinite
import os
from queue import Empty, Full, Queue
import re
from threading import Event, Thread
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .contracts import DetectionSource
from .safety import Incident


class DeliveryStatus(StrEnum):
    ACCEPTED = "telegram_accepted_no_acknowledgement"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    SIMULATED = "simulated_accepted"


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    incident_id: str
    status: DeliveryStatus


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    token: str = field(repr=False)
    chat_id: str = field(repr=False)
    timeout_seconds: float = 5.0

    def __post_init__(self):
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", self.token):
            raise ValueError("TELEGRAM_BOT_TOKEN is missing or invalid")
        if not re.fullmatch(r"-?[0-9]+", self.chat_id):
            raise ValueError("TELEGRAM_CHAT_ID must be one numeric chat ID")
        if not isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 30:
            raise ValueError("notification timeout must be between 0 and 30 seconds")

    @classmethod
    def from_environment(cls):
        return cls(os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", ""))


class TelegramSender:
    def __init__(self, config: TelegramConfig, *, opener: Callable = urlopen):
        self._config = config
        self._opener = opener

    def __call__(self, incident: Incident) -> DeliveryResult:
        # Enforce separation at the network boundary, regardless of CLI options.
        if incident.source != DetectionSource.LIVE:
            return DeliveryResult(incident.incident_id, DeliveryStatus.FAILED)
        text = (
            "EXPERIMENTAL FALL DETECTION: possible fall; response countdown expired. "
            "Please check on the person. "
            f"Incident: {incident.incident_id}. No caregiver response has been confirmed."
        )
        return DeliveryResult(incident.incident_id, self._post(text))

    def send_test(self) -> DeliveryStatus:
        """Only called by an explicit standalone test command, never by simulations."""
        return self._post("TEST ONLY: fall detection Telegram setup check. No fall detected.")

    def _post(self, text: str) -> DeliveryStatus:
        body = json.dumps({"chat_id": self._config.chat_id, "text": text}).encode("utf-8")
        request = Request(
            f"https://api.telegram.org/bot{self._config.token}/sendMessage",
            data=body, headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with self._opener(request, timeout=self._config.timeout_seconds) as response:
                payload = json.loads(response.read(65536))
            if not isinstance(payload, dict):
                return DeliveryStatus.UNCERTAIN
            if payload.get("ok") is True and isinstance(payload.get("result"), dict):
                return DeliveryStatus.ACCEPTED
            if payload.get("ok") is False:
                return DeliveryStatus.FAILED
            return DeliveryStatus.UNCERTAIN
        except HTTPError as error:
            error.close()
            return DeliveryStatus.FAILED if 400 <= error.code < 500 else DeliveryStatus.UNCERTAIN
        except (URLError, OSError, HTTPException, ValueError):
            return DeliveryStatus.UNCERTAIN


class FakeSender:
    def __init__(self, status: DeliveryStatus = DeliveryStatus.SIMULATED):
        self.status = status

    def __call__(self, incident: Incident) -> DeliveryResult:
        return DeliveryResult(incident.incident_id, self.status)


class NotificationWorker:
    """Single outstanding request/result. No retries, unbounded queues, or history.

    UI owns deduplication through SafetyController. While a result is unread,
    submit refuses another job. close waits only a bounded time; a stuck OS
    network operation must not freeze local controls or application shutdown.
    """

    def __init__(self, sender: Callable[[Incident], DeliveryResult]):
        self._sender = sender
        self._requests: Queue[Incident] = Queue(maxsize=1)
        self._results: Queue[DeliveryResult] = Queue(maxsize=1)
        self._stop = Event()
        self._busy = False  # submit/poll are called only by the application thread
        self._thread = Thread(target=self._run, name="notification", daemon=True)
        self._thread.start()

    def submit(self, incident: Incident) -> bool:
        if self._stop.is_set() or self._busy or not self._thread.is_alive():
            return False
        try:
            self._requests.put_nowait(incident)
        except Full:
            return False
        self._busy = True
        return True

    def poll(self) -> DeliveryResult | None:
        try:
            result = self._results.get_nowait()
        except Empty:
            return None
        self._busy = False
        return result

    def close(self, timeout: float = 0.2) -> bool:
        self._stop.set()
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _run(self):
        while not self._stop.is_set():
            try:
                incident = self._requests.get(timeout=0.05)
            except Empty:
                continue
            try:
                result = self._sender(incident)
                if not isinstance(result, DeliveryResult) or result.incident_id != incident.incident_id:
                    raise ValueError("invalid notification result")
            except Exception:
                # Never propagate transport exception text: it can include tokens.
                result = DeliveryResult(incident.incident_id, DeliveryStatus.UNCERTAIN)
            self._results.put_nowait(result)
