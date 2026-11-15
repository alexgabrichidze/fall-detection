"""Link one private Telegram chat using a short-lived /start link."""

import json
import os
from pathlib import Path
import re
from secrets import token_urlsafe
from tempfile import NamedTemporaryFile
from threading import Lock
from time import monotonic

from .contracts import DetectionSource
from .notifier import (
    CaregiverAction, DeliveryResult, DeliveryStatus, FakeSender, TelegramConfig, TelegramSender, telegram_request,
)
from .safety import Incident


OWNER_SETTINGS = Path(".env.telegram.json")


def read_settings(path: Path) -> dict:
    try:
        settings = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise ValueError("Could not read the local Telegram settings.") from None
    if not isinstance(settings, dict):
        raise ValueError("The local Telegram settings must be a JSON object.")
    return settings


class TelegramLink:
    def __init__(self, path: Path = OWNER_SETTINGS) -> None:
        self._path = path
        self._lock = Lock()
        self._settings = read_settings(path)
        self._pairing: tuple[str, float] | None = None
        self._sender_config: TelegramConfig | None = None
        self._sender: TelegramSender | None = None

    def _save(self, settings: dict) -> None:
        with NamedTemporaryFile(mode="w", dir=self._path.parent, prefix=".env.telegram.", delete=False) as target:
            temporary_path = Path(target.name)
            try:
                json.dump(settings, target)
                target.close()
                temporary_path.replace(self._path)
            finally:
                temporary_path.unlink(missing_ok=True)
        self._settings = settings

    _request = staticmethod(telegram_request)

    def config(self) -> TelegramConfig | None:
        with self._lock:
            token = self._settings.get("token", "")
            chat_id = self._settings.get("chat_id", "")
        return TelegramConfig(token, chat_id) if token and chat_id else None

    def status(self) -> dict:
        with self._lock:
            return {
                "hasToken": bool(self._settings.get("token")),
                "linked": bool(self._settings.get("chat_id")),
                "bot": self._settings.get("bot"),
                "recipient": self._settings.get("recipient"),
            }

    def begin(self, token: str) -> dict:
        with self._lock:
            token = token.strip() or self._settings.get("token", "")
        if not isinstance(token, str) or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,100}", token):
            raise ValueError("Enter the bot token from BotFather.")
        bot = self._request(token, "getMe", {})
        username = bot.get("username", "") if isinstance(bot, dict) else ""
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
            raise ValueError("Telegram did not return a bot username.")
        webhook = self._request(token, "getWebhookInfo", {})
        if isinstance(webhook, dict) and webhook.get("url"):
            raise ValueError("This bot's incoming updates go to a webhook. Use a dedicated bot to link a desktop chat.")

        code = f"fall_{token_urlsafe(16)}"
        with self._lock:
            settings = self._settings.copy() if token == self._settings.get("token") else {}
            self._save({**settings, "token": token, "bot": username})
            self._pairing = code, monotonic() + 600
        return {"url": f"https://t.me/{username}?start={code}", "command": f"/start {code}"}

    def finish(self) -> None:
        with self._lock:
            pairing = self._pairing
            token = self._settings.get("token", "")
        if pairing is None or monotonic() >= pairing[1]:
            raise ValueError("This link expired. Open a new Telegram link.")
        # Omit the offset so setup doesn't acknowledge or discard other updates.
        updates = self._request(token, "getUpdates", {"limit": 100, "timeout": 0})
        if not isinstance(updates, list):
            raise ValueError("Telegram returned an unexpected response.")
        chats = {}
        for update in updates:
            message = update.get("message", {})
            chat = message.get("chat", {})
            if (message.get("text") == f"/start {pairing[0]}" and chat.get("type") == "private"
                    and type(chat.get("id")) is int):
                chats[chat["id"]] = chat
        if not chats:
            raise ValueError("Tap Start in Telegram using this link, then try again.")
        if len(chats) > 1:
            raise ValueError("More than one chat used this link. Create a new link for your chat.")
        chat_id, chat = next(iter(chats.items()))
        recipient = " ".join(chat.get(name, "") for name in ("first_name", "last_name")).strip()
        with self._lock:
            if self._pairing != pairing or monotonic() >= pairing[1]:
                raise ValueError("This link expired. Open a new Telegram link.")
            self._save({**self._settings, "chat_id": str(chat_id), "recipient": recipient or "Private chat"})
            self._pairing = None

    def disconnect(self) -> None:
        with self._lock:
            settings = self._settings.copy()
            settings.pop("chat_id", None)
            settings.pop("recipient", None)
            self._save(settings)
            self._pairing = None

    def _transport(self) -> TelegramSender | None:
        config = self.config()
        if config != self._sender_config:
            self._sender_config = config
            self._sender = TelegramSender(config, replies=True) if config else None
        return self._sender

    def notify(self, incident: Incident, clip: bytes | None = None) -> DeliveryResult:
        sender = self._transport() if incident.source == DetectionSource.LIVE else None
        return sender(incident, clip) if sender else FakeSender()(incident)

    def poll_reply(self, incident_id: str) -> CaregiverAction | None:
        sender = self._transport()
        return sender.poll_reply(incident_id) if sender else None

    def send_test(self) -> DeliveryStatus:
        config = self.config()
        if config is None:
            raise ValueError("Link a Telegram chat first.")
        return TelegramSender(config).send_test()


def telegram_config() -> TelegramConfig:
    if os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_CHAT_ID"):
        return TelegramConfig.from_environment()
    return TelegramLink().config() or TelegramConfig.from_environment()
