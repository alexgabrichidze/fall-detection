"""Telegram delivery and caregiver responses on a background worker.

Exception text, URLs, credentials, and server descriptions must never be logged.
A timeout is uncertain delivery, not proof that Telegram rejected a message.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from http.client import HTTPSConnection, HTTPException
import json
from math import isfinite
import os
from queue import Empty, Full, Queue
import re
from secrets import token_hex
from threading import Event, Lock, Thread
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .clips import ClipFrames, encode_clip
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


class CaregiverAction(StrEnum):
    HELP = "help"
    FALSE_ALARM = "false_alarm"


@dataclass(frozen=True, slots=True)
class CaregiverReply:
    incident_id: str
    action: CaregiverAction


@dataclass(frozen=True, slots=True)
class TelegramCallback:
    reply: CaregiverReply
    chat_id: str
    message_id: int
    query_id: str


def get_caregiver_callback(update: dict) -> TelegramCallback | None:
    query = update.get("callback_query", {})
    message = query.get("message", {})
    chat = message.get("chat", {})
    data = query.get("data")
    if (not isinstance(data, str) or chat.get("type") != "private"
            or type(chat.get("id")) is not int or query.get("from", {}).get("id") != chat["id"]
            or type(message.get("message_id")) is not int or not isinstance(query.get("id"), str)):
        return None
    prefix, _, tail = data.partition(":")
    action, _, incident_id = tail.partition(":")
    if prefix != "fall" or not incident_id:
        return None
    try:
        decision = CaregiverAction(action)
    except ValueError:
        return None
    return TelegramCallback(CaregiverReply(incident_id, decision), str(chat["id"]),
                            message["message_id"], query["id"])


def telegram_request(token: str, method: str, body: dict):
    connection = HTTPSConnection("api.telegram.org", timeout=7)
    try:
        connection.request("POST", f"/bot{token}/{method}", json.dumps(body).encode(),
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        status = response.status
        payload = json.loads(response.read(1024 * 1024))
    except (HTTPException, OSError, ValueError):
        raise ValueError("Could not reach Telegram. Check your connection and try again.") from None
    finally:
        connection.close()
    if status in (401, 404):
        raise ValueError("Telegram rejected the bot token. Check it in BotFather.")
    if status == 409:
        raise ValueError("Another service is receiving messages for this bot.")
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ValueError("Telegram could not complete the request. Try again shortly.")
    return payload.get("result")


def answer_caregiver(token: str, callback: TelegramCallback, *, accepted: bool) -> None:
    try:
        telegram_request(token, "answerCallbackQuery", {
            "callback_query_id": callback.query_id,
            "text": "Response recorded." if accepted else "This alert already has a reply or has expired.",
        })
        if accepted:
            telegram_request(token, "editMessageReplyMarkup", {
                "chat_id": callback.chat_id, "message_id": callback.message_id,
                "reply_markup": {"inline_keyboard": []},
            })
    except ValueError:
        # The selected response stays recorded even if Telegram can't update the message.
        pass


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
    def __init__(self, config: TelegramConfig, *, opener: Callable = urlopen, replies: bool = False):
        self._config = config
        self._opener = opener
        self._replies = replies
        self._offset = 0
        self._subscribed = False

    def __call__(self, incident: Incident, clip: bytes | None = None) -> DeliveryResult:
        # Enforce separation at the network boundary, regardless of CLI options.
        if incident.source != DetectionSource.LIVE:
            return DeliveryResult(incident.incident_id, DeliveryStatus.FAILED)
        return DeliveryResult(incident.incident_id, self.send_alert(incident.incident_id, clip, replies=self._replies))

    def send_alert(self, incident_id: str, clip: bytes | None = None, *, replies: bool = False) -> DeliveryStatus:
        text = (
            "EXPERIMENTAL FALL DETECTION: possible fall; response countdown expired. "
            "Please check on the person. "
            f"Incident: {incident_id}. No caregiver response has been confirmed."
        )
        fields: dict = {"chat_id": self._config.chat_id}
        if replies and not self._config.chat_id.startswith("-"):
            if self._replies and not self._subscribed:
                try:
                    telegram_request(self._config.token, "getUpdates", {
                        "limit": 1, "timeout": 0, "allowed_updates": ["message", "callback_query"],
                    })
                    self._subscribed = True
                except ValueError:
                    pass
            fields["reply_markup"] = {"inline_keyboard": [[
                {"text": "Help is on the way", "callback_data": f"fall:help:{incident_id}"},
                {"text": "False alarm", "callback_data": f"fall:false_alarm:{incident_id}"},
            ]]}
        if clip:
            status = self._post("sendVideo", {**fields, "caption": text}, clip)
            if status != DeliveryStatus.FAILED:
                return status
        return self._post("sendMessage", {**fields, "text": text})

    def poll_reply(self, incident_id: str) -> CaregiverAction | None:
        if not self._replies or self._config.chat_id.startswith("-"):
            return None
        token = self._config.token
        updates = telegram_request(token, "getUpdates", {
            "timeout": 0, "offset": self._offset, "allowed_updates": ["message", "callback_query"],
        })
        if not isinstance(updates, list):
            return None
        decision = None
        for update in updates:
            self._offset = max(self._offset, update["update_id"] + 1)
            callback = get_caregiver_callback(update)
            if callback and callback.chat_id == self._config.chat_id and callback.reply.incident_id == incident_id:
                answer_caregiver(token, callback, accepted=decision is None)
                decision = decision or callback.reply.action
        return decision

    def send_test(self) -> DeliveryStatus:
        """Only called by an explicit standalone test command, never by simulations."""
        return self._post("sendMessage", {
            "chat_id": self._config.chat_id, "text": "TEST ONLY: fall detection Telegram setup check. No fall detected.",
        })

    def _post(self, method: str, fields: dict, clip: bytes | None = None) -> DeliveryStatus:
        content_type = "application/json"
        if clip:
            boundary = token_hex(16)
            parts = [
                (f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'
                 f'{value if isinstance(value, str) else json.dumps(value)}\r\n').encode()
                for key, value in fields.items()
            ]
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="video"; '
                         'filename="fall-clip.mp4"\r\nContent-Type: video/mp4\r\n\r\n'.encode())
            parts.extend((clip, f"\r\n--{boundary}--\r\n".encode()))
            body = b"".join(parts)
            content_type = f"multipart/form-data; boundary={boundary}"
        else:
            body = json.dumps(fields).encode()
        request = Request(
            f"https://api.telegram.org/bot{self._config.token}/{method}",
            data=body, headers={"Content-Type": content_type}, method="POST",
        )
        timeout = max(15, self._config.timeout_seconds) if clip else self._config.timeout_seconds
        try:
            with self._opener(request, timeout=timeout) as response:
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

    def __init__(self, sender: Callable, receive: Callable[[str], CaregiverAction | None] | None = None):
        self._sender = sender
        self._receive = receive
        self._requests: Queue[tuple[Incident, ClipFrames]] = Queue(maxsize=1)
        self._results: Queue[DeliveryResult] = Queue(maxsize=1)
        self._replies: Queue[CaregiverReply] = Queue(maxsize=1)
        self._lock = Lock()
        self._watching: str | None = None
        self._watch_until = 0.0
        self._next_check = 0.0
        self._stop = Event()
        self._busy = False  # submit/poll are called only by the application thread
        self._thread = Thread(target=self._run, name="notification", daemon=True)
        self._thread.start()

    def submit(self, incident: Incident, clip: ClipFrames = ()) -> bool:
        if self._stop.is_set() or self._busy or not self._thread.is_alive():
            return False
        try:
            self._requests.put_nowait((incident, clip))
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

    def watch(self, incident_id: str | None) -> None:
        if self._receive is None:
            return
        with self._lock:
            self._watching = incident_id
            self._watch_until = monotonic() + 30 * 60 if incident_id else 0
            self.poll_reply()

    def poll_reply(self) -> CaregiverReply | None:
        try:
            return self._replies.get_nowait()
        except Empty:
            return None

    def close(self, timeout: float = 0.2) -> bool:
        self._stop.set()
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _run(self):
        while not self._stop.is_set():
            try:
                incident, frames = self._requests.get(timeout=0.05)
            except Empty:
                self._check_reply()
                continue
            try:
                if frames:
                    try:
                        clip = encode_clip(frames)
                    except Exception:
                        clip = None
                    result = self._sender(incident, clip) if clip else self._sender(incident)
                else:
                    result = self._sender(incident)
                if not isinstance(result, DeliveryResult) or result.incident_id != incident.incident_id:
                    raise ValueError("invalid notification result")
            except Exception:
                # Never propagate transport exception text: it can include tokens.
                result = DeliveryResult(incident.incident_id, DeliveryStatus.UNCERTAIN)
            self._results.put_nowait(result)

    def _check_reply(self) -> None:
        now = monotonic()
        with self._lock:
            incident_id = self._watching if now < self._watch_until else None
        if self._receive is None or incident_id is None or now < self._next_check:
            return
        self._next_check = now + 2
        try:
            action = self._receive(incident_id)
        except (HTTPException, OSError, ValueError):
            return
        if not isinstance(action, CaregiverAction):
            return
        with self._lock:
            if self._watching == incident_id:
                self._watching = None
                self._replies.put_nowait(CaregiverReply(incident_id, action))
