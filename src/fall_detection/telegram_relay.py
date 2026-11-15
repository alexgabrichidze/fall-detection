"""Shared Telegram pairing, alert delivery, and replies. Run behind HTTPS."""

import argparse
from dataclasses import dataclass, field
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from secrets import token_urlsafe
from threading import Lock
from time import monotonic
from uuid import UUID

from .clips import MAX_CLIP_BYTES
from .notifier import (
    CaregiverAction, DeliveryStatus, TelegramConfig, TelegramSender, answer_caregiver,
    get_caregiver_callback, telegram_request,
)
from .telegram_link import OWNER_SETTINGS, read_settings


@dataclass
class Pairing:
    code: str
    expires: float
    chats: dict[int, str] = field(default_factory=dict)


@dataclass
class PendingAlert:
    expires: float
    action: CaregiverAction | None = None
    status: DeliveryStatus = DeliveryStatus.UNCERTAIN


class TelegramRelay:
    def __init__(self, token: str):
        TelegramConfig(token, "0")  # Validate the server credential before using it.
        self._token = token
        self._key = hmac.digest(token.encode(), b"fall-monitor/telegram-grant/v1", "sha256")
        bot = telegram_request(token, "getMe", {})
        self.bot = bot.get("username", "") if isinstance(bot, dict) else ""
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", self.bot):
            raise ValueError("Telegram did not return a bot username.")
        webhook = telegram_request(token, "getWebhookInfo", {})
        if isinstance(webhook, dict) and webhook.get("url"):
            raise ValueError("This bot already has a webhook receiving Telegram updates.")
        self._tickets: dict[str, Pairing] = {}
        self._alerts: dict[tuple[str, str], PendingAlert] = {}
        self._last_send: dict[str, float] = {}
        self._lock = Lock()
        self._poll_lock = Lock()
        self._offset = 0
        self._offset_time = 0.0
        self._next_poll = 0.0

    def begin(self) -> dict:
        ticket, code = token_urlsafe(24), f"fall_{token_urlsafe(16)}"
        now = monotonic()
        with self._lock:
            self._tickets = {key: pair for key, pair in self._tickets.items() if pair.expires > now}
            if len(self._tickets) >= 256:
                raise ValueError("Telegram setup is busy. Try again in a few minutes.")
            self._tickets[ticket] = Pairing(code, now + 600)
        return {"ticket": ticket, "url": f"https://t.me/{self.bot}?start={code}", "command": f"/start {code}"}

    def finish(self, ticket: str) -> dict:
        with self._lock:
            pair = self._tickets.get(ticket)
            if pair is None or pair.expires <= monotonic():
                raise ValueError("This link expired. Open a new Telegram link.")
            ready = bool(pair.chats)
        if not ready:
            self._poll()
        with self._lock:
            if pair.expires <= monotonic():
                raise ValueError("This link expired. Open a new Telegram link.")
            if not pair.chats:
                raise ValueError("Tap Start in Telegram using this link, then try again.")
            if len(pair.chats) != 1:
                raise ValueError("More than one chat used this link. Create a new link for your chat.")
            chat_id, recipient = next(iter(pair.chats.items()))
            return {"grant": self._grant(str(chat_id)), "bot": self.bot, "recipient": recipient}

    def _poll(self):
        if not self._poll_lock.acquire(blocking=False):
            raise ValueError("The bot is checking for new chats. Try again in a moment.")
        answers = []
        try:
            now = monotonic()
            if now < self._next_poll:
                return
            self._next_poll = now + 1
            # Telegram keeps updates for up to 24 hours. An old offset can be retired.
            offset = self._offset if now - self._offset_time < 86400 else 0
            updates = telegram_request(self._token, "getUpdates", {
                "offset": offset, "timeout": 0, "allowed_updates": ["message", "callback_query"],
            })
            if not isinstance(updates, list):
                raise ValueError("Telegram returned an unexpected response.")
            with self._lock:
                by_command = {f"/start {pair.code}": pair for pair in self._tickets.values() if pair.expires > now}
                for update in updates:
                    self._offset = max(offset, update["update_id"] + 1)
                    self._offset_time = now
                    offset = self._offset
                    message = update.get("message", {})
                    chat = message.get("chat", {})
                    pair = by_command.get(message.get("text"))
                    if pair and chat.get("type") == "private" and type(chat.get("id")) is int:
                        name = " ".join(chat.get(key, "") for key in ("first_name", "last_name")).strip()
                        pair.chats[chat["id"]] = name or "Private chat"
                    callback = get_caregiver_callback(update)
                    if callback:
                        alert = self._alerts.get((callback.chat_id, callback.reply.incident_id))
                        accepted = alert is not None and alert.expires > now and alert.action is None
                        if accepted:
                            alert.action = callback.reply.action
                        answers.append((callback, accepted))
        finally:
            self._poll_lock.release()
        for callback, accepted in answers:
            answer_caregiver(self._token, callback, accepted=accepted)

    def _grant(self, chat_id: str) -> str:
        return f"{chat_id}.{hmac.digest(self._key, chat_id.encode(), 'sha256').hex()}"

    def authorized_chat(self, grant: str | None) -> str | None:
        if not isinstance(grant, str) or not re.fullmatch(r"[0-9]{1,20}\.[0-9a-f]{64}", grant):
            return None
        chat_id = grant.partition(".")[0]
        return chat_id if hmac.compare_digest(grant, self._grant(chat_id)) else None

    def notify(self, body: dict) -> DeliveryStatus:
        chat_id, kind = self.authorized_chat(body.get("grant")), body.get("kind")
        if not chat_id:
            return DeliveryStatus.FAILED
        if kind == "fall" and isinstance(body.get("incident_id"), str):
            return self.alert(chat_id, body["incident_id"], replies=body.get("replies") is True)
        if kind != "test":
            return DeliveryStatus.FAILED
        with self._lock:
            limited = self._rate_limited(chat_id, monotonic())
        return DeliveryStatus.FAILED if limited else self._sender(chat_id).send_test()

    def _rate_limited(self, chat_id: str, now: float) -> bool:
        self._last_send = {chat: due for chat, due in self._last_send.items() if due > now}
        if chat_id in self._last_send:
            return True
        self._last_send[chat_id] = now + 5
        return False

    def _sender(self, chat_id: str) -> TelegramSender:
        return TelegramSender(TelegramConfig(self._token, chat_id))

    def alert(self, chat_id: str, incident_id: str, clip: bytes | None = None, *, replies: bool) -> DeliveryStatus:
        incident_id = str(UUID(incident_id))
        key = chat_id, incident_id
        now = monotonic()
        alert = None
        with self._lock:
            existing = self._alerts.get(key)
            if existing and existing.expires > now:
                return existing.status
            if self._rate_limited(chat_id, now):
                return DeliveryStatus.FAILED
            if replies:
                self._alerts = {key: alert for key, alert in self._alerts.items() if alert.expires > now}
                if len(self._alerts) >= 256:
                    self._alerts.pop(next(iter(self._alerts)))
                alert = PendingAlert(now + 30 * 60)
                self._alerts[key] = alert
        status = self._sender(chat_id).send_alert(incident_id, clip, replies=replies)
        if alert:
            with self._lock:
                alert.status = status
        return status

    def response(self, body: dict) -> dict:
        chat_id = self.authorized_chat(body.get("grant"))
        incident_id = body.get("incident_id")
        if not chat_id or not isinstance(incident_id, str):
            raise ValueError("Invalid caregiver response request.")
        with self._lock:
            alert = self._alerts.get((chat_id, incident_id))
            if not alert or alert.expires <= monotonic() or alert.status == DeliveryStatus.FAILED:
                return {"action": None}
            if alert.action is not None:
                return {"action": alert.action.value}
        self._poll()
        with self._lock:
            return {"action": alert.action.value if alert.action else None}


class RelayHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        relay = self.server.relay
        status = 200
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if self.path == "/notify" and self.headers.get_content_type() == "video/mp4":
                result = self._video(size)
            else:
                if not 0 < size <= 2048 or self.headers.get_content_type() != "application/json":
                    raise ValueError("Invalid relay request.")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("Invalid relay request.")
                if self.path == "/link":
                    result = relay.begin()
                elif self.path == "/finish" and isinstance(body.get("ticket"), str):
                    result = relay.finish(body["ticket"])
                elif self.path == "/notify":
                    result = {"status": relay.notify(body).value}
                elif self.path == "/response":
                    result = relay.response(body)
                else:
                    status, result = 404, {"error": "Unknown relay request."}
        except ValueError as error:
            status, result = 400, {"error": str(error)}
        except OSError:
            return
        payload = json.dumps(result).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
        except OSError:
            pass

    def _video(self, size: int) -> dict:
        relay = self.server.relay
        auth = self.headers.get("Authorization", "")
        incident_id = self.headers.get("X-Incident-ID", "")
        chat_id = relay.authorized_chat(auth.removeprefix("Bearer ")) if auth.startswith("Bearer ") else None
        if not chat_id:
            return {"status": DeliveryStatus.FAILED.value}
        if not 8 <= size <= MAX_CLIP_BYTES:
            raise ValueError("Invalid relay request.")
        incident_id = str(UUID(incident_id))
        clip = self.rfile.read(size)
        if len(clip) != size or clip[4:8] != b"ftyp":
            raise ValueError("Invalid relay request.")
        return {"status": relay.alert(chat_id, incident_id, clip, replies=True).value}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN") or read_settings(OWNER_SETTINGS).get("token", "")
        relay = TelegramRelay(token)
        relay._poll()
        server = ThreadingHTTPServer(("127.0.0.1", args.port), RelayHandler)
    except (OSError, ValueError, OverflowError) as error:
        parser.error(str(error))
    server.relay = relay
    with server:
        print(f"Telegram relay for @{relay.bot} listening at http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
