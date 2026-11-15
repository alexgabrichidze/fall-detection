"""Link one private Telegram chat using a short-lived /start link."""

from http.client import HTTPConnection, HTTPSConnection, HTTPException
import json
import os
from pathlib import Path
import re
from secrets import token_urlsafe
from tempfile import NamedTemporaryFile
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit

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


class _LocalSettings:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()
        self._settings = read_settings(path)

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


class TelegramLink(_LocalSettings):
    def __init__(self, path: Path = OWNER_SETTINGS) -> None:
        super().__init__(path)
        self._pairing: tuple[str, float] | None = None
        self._sender_config: TelegramConfig | None = None
        self._sender: TelegramSender | None = None

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


class RelayTelegramLink(_LocalSettings):
    """Pair with the shared bot using a grant scoped to one private chat."""

    def __init__(self, url: str, path: Path = Path(".env.telegram-relay.json")) -> None:
        super().__init__(path)
        self._url = url.rstrip("/")
        self._endpoint = urlsplit(self._url)
        if (not self._endpoint.hostname or self._endpoint.username is not None
                or self._endpoint.query or self._endpoint.fragment
                or (self._endpoint.scheme != "https" and
                    (self._endpoint.scheme != "http" or self._endpoint.hostname != "127.0.0.1"))):
            raise ValueError("The shared Telegram relay needs an HTTPS address.")
        if self._settings.get("relay") != self._url:
            self._settings = {}
        self._ticket = None

    def status(self) -> dict:
        with self._lock:
            return {
                "managed": True,
                "linked": bool(self._settings.get("grant")),
                "bot": self._settings.get("bot"),
                "recipient": self._settings.get("recipient"),
            }

    def begin(self, _token: str = "") -> dict:
        link = self._post("/link", {})
        ticket = link.get("ticket")
        if not isinstance(ticket, str) or not all(isinstance(link.get(key), str) for key in ("url", "command")):
            raise ValueError("The shared bot returned an invalid link. Try again.")
        with self._lock:
            self._ticket = ticket
        return {"url": link["url"], "command": link["command"]}

    def finish(self) -> None:
        with self._lock:
            ticket = self._ticket
        if not ticket:
            raise ValueError("This link expired. Open a new Telegram link.")
        paired = self._post("/finish", {"ticket": ticket})
        if not all(isinstance(paired.get(key), str) and paired[key] for key in ("grant", "bot", "recipient")):
            raise ValueError("The shared bot could not link this chat. Try again.")
        with self._lock:
            if ticket != self._ticket:
                raise ValueError("This link expired. Open a new Telegram link.")
            self._save({"relay": self._url, "grant": paired["grant"],
                        "bot": paired["bot"], "recipient": paired["recipient"]})
            self._ticket = None

    def disconnect(self) -> None:
        with self._lock:
            self._save({})
            self._ticket = None

    def notify(self, incident: Incident, clip: bytes | None = None) -> DeliveryResult:
        if incident.source != DetectionSource.LIVE:
            return FakeSender()(incident)
        grant = self._grant()
        if grant and clip:
            try:
                result = self._post("/notify", clip, headers={
                    "Content-Type": "video/mp4", "Authorization": f"Bearer {grant}",
                    "X-Incident-ID": incident.incident_id,
                }, timeout=24)
                if not result.get("legacy"):
                    return DeliveryResult(incident.incident_id, DeliveryStatus(result.get("status")))
            except ValueError:
                return DeliveryResult(incident.incident_id, DeliveryStatus.UNCERTAIN)
        status = self._send("fall", incident_id=incident.incident_id, replies=True)
        return DeliveryResult(incident.incident_id, status)

    def poll_reply(self, incident_id: str) -> CaregiverAction | None:
        grant = self._grant()
        if not grant:
            return None
        result = self._post("/response", {"grant": grant, "incident_id": incident_id})
        action = result.get("action")
        return CaregiverAction(action) if action else None

    def send_test(self) -> DeliveryStatus:
        status = self._send("test")
        if status == DeliveryStatus.SIMULATED:
            raise ValueError("Link a Telegram chat first.")
        return status

    def _grant(self) -> str | None:
        with self._lock:
            return self._settings.get("grant")

    def _send(self, kind: str, **fields: str | bool) -> DeliveryStatus:
        grant = self._grant()
        if not grant:
            return DeliveryStatus.SIMULATED
        try:
            result = self._post("/notify", {"grant": grant, "kind": kind, **fields})
            return DeliveryStatus(result.get("status"))
        except ValueError:
            return DeliveryStatus.UNCERTAIN

    def _post(self, path: str, body: dict | bytes, *, headers: dict | None = None, timeout: int = 10) -> dict:
        endpoint = self._endpoint
        connection_type = HTTPSConnection if endpoint.scheme == "https" else HTTPConnection
        connection = connection_type(endpoint.hostname, endpoint.port, timeout=timeout)
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        try:
            connection.request("POST", endpoint.path + path, data, headers or {"Content-Type": "application/json"})
            response = connection.getresponse()
            status = response.status
            payload = json.loads(response.read(4096))
        except (HTTPException, OSError, ValueError):
            raise ValueError("Could not reach the shared Telegram bot. Try again shortly.") from None
        finally:
            connection.close()
        if (isinstance(body, bytes) and status == 400 and isinstance(payload, dict)
                and payload.get("error") == "Invalid relay request."):
            return {"legacy": True}
        if not isinstance(payload, dict) or status != 200:
            message = payload.get("error") if isinstance(payload, dict) else None
            raise ValueError(message or "The shared Telegram bot is unavailable. Try again shortly.")
        return payload


def create_telegram_link() -> TelegramLink | RelayTelegramLink:
    relay = os.environ.get("FALL_MONITOR_RELAY_URL", "").strip()
    if not relay:
        packaged_url = Path(__file__).with_name("relay-url.txt")
        if packaged_url.is_file():
            relay = packaged_url.read_text().strip()
    return RelayTelegramLink(relay) if relay else TelegramLink()


def telegram_config() -> TelegramConfig:
    if os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_CHAT_ID"):
        return TelegramConfig.from_environment()
    return TelegramLink().config() or TelegramConfig.from_environment()
