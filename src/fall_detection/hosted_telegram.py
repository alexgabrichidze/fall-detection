"""Use the hosted browser bot from the local monitor, without a bot token.

Pairing tickets and chat-scoped grants are private credentials. They are never
returned by status(), included in logs, or put in request URLs.
"""

from http.client import HTTPSConnection, HTTPException
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from threading import Lock
from time import monotonic
from urllib.parse import parse_qs, urlsplit

from .contracts import DetectionSource
from .notifier import CaregiverAction, DeliveryResult, DeliveryStatus, FakeSender
from .safety import Incident
from .telegram_link import read_settings

DEFAULT_BOT_SERVICE = 'https://safe-n-sound.netlify.app'
_GRANT = re.compile(r'[0-9]{1,20}\.[0-9a-f]{64}')
_INCIDENT = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
MAX_HOSTED_CLIP_BYTES = 4 * 1024 * 1024


class HostedBotError(ValueError):
    def __init__(self, message, *, status=None, invalid_clip=False):
        super().__init__(message)
        self.status = status
        self.invalid_clip = invalid_clip


class HostedTelegramLink:
    def __init__(self, url: str = DEFAULT_BOT_SERVICE,
                 path: Path = Path('.env.telegram-hosted.json'), *,
                 connection_factory=HTTPSConnection, clock=monotonic):
        try:
            endpoint = urlsplit(url)
            valid = (endpoint.scheme == 'https' and endpoint.hostname
                     and endpoint.username is None and endpoint.password is None
                     and endpoint.path in ('', '/') and not endpoint.query and not endpoint.fragment)
            port = endpoint.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('The shared bot service must be an HTTPS site address.')
        self._url = url.rstrip('/')
        self._host, self._port = endpoint.hostname, port
        self._path = path
        self._connection_factory = connection_factory
        self._clock = clock
        self._lock = Lock()
        self._pairing = None
        self._settings = read_settings(path)
        if self._settings.get('service') != self._url:
            self._settings = {}

    def _grant(self):
        grant = self._settings.get('grant')
        return grant if isinstance(grant, str) and _GRANT.fullmatch(grant) else None

    def status(self) -> dict:
        with self._lock:
            return {'managed': True, 'hasToken': False, 'linked': bool(self._grant()),
                    'bot': self._settings.get('bot'), 'recipient': self._settings.get('recipient')}

    def _save(self, settings):
        # NamedTemporaryFile creates a private file (0600) on POSIX systems.
        with NamedTemporaryFile(mode='w', dir=self._path.parent, prefix='.env.telegram.', delete=False) as target:
            temporary = Path(target.name)
            try:
                json.dump(settings, target)
                target.close()
                temporary.replace(self._path)
            finally:
                temporary.unlink(missing_ok=True)
        self._settings = settings

    def _call(self, action, body, *, headers=None):
        if isinstance(body, dict):
            data = json.dumps(body).encode()
            request_headers = {'Content-Type': 'application/json'}
        else:
            data = body
            request_headers = {'Content-Type': 'video/mp4'}
        request_headers.update(headers or {})
        connection = self._connection_factory(self._host, self._port, timeout=24)
        try:
            connection.request('POST', f'/telegram/{action}', body=data, headers=request_headers)
            response = connection.getresponse()
            raw = response.read(65537)
            if len(raw) > 65536:
                raise HostedBotError('The shared Telegram bot returned an invalid response.')
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeError):
                result = None
            if not 200 <= response.status < 300:
                error = result.get('error') if isinstance(result, dict) else None
                messages = {
                    'Tap Start in Telegram using this link, then try again.',
                    'This link expired. Open a new Telegram link.',
                }
                message = error if isinstance(error, str) and error in messages else 'The shared Telegram bot is unavailable. Try again shortly.'
                raise HostedBotError(message, status=response.status,
                                     invalid_clip=response.status == 400 and error == 'Invalid relay request.')
            if not isinstance(result, dict):
                raise HostedBotError('The shared Telegram bot returned an invalid response.')
            return result
        except (OSError, HTTPException):
            # Never propagate transport exception text: it may contain grants.
            raise HostedBotError('The shared Telegram bot is unavailable. Check your connection.') from None
        finally:
            connection.close()

    def begin(self, token: str = '') -> dict:
        # Managed pairing deliberately never forwards a local/private bot token.
        result = self._call('link', {})
        ticket, url, command = (result.get(key) for key in ('ticket', 'url', 'command'))
        if not isinstance(ticket, str) or not re.fullmatch(r'[A-Za-z0-9_-]{48}', ticket) or not isinstance(url, str):
            raise HostedBotError('The shared Telegram bot returned an invalid pairing link.')
        target = urlsplit(url)
        code = parse_qs(target.query).get('start', [''])[0]
        if (target.scheme != 'https' or target.netloc != 't.me' or target.fragment
                or not re.fullmatch(r'/[A-Za-z0-9_]{5,32}', target.path)
                or not re.fullmatch(r'fall_[A-Za-z0-9_-]{48}', code) or command != f'/start {code}'):
            raise HostedBotError('The shared Telegram bot returned an invalid pairing link.')
        with self._lock:
            self._pairing = ticket, self._clock() + 600
        return {'url': url, 'command': command}

    def finish(self):
        with self._lock:
            pairing = self._pairing
        if pairing is None or self._clock() >= pairing[1]:
            raise ValueError('This link expired. Open a new Telegram link.')
        result = self._call('finish', {'ticket': pairing[0]})
        grant, bot, recipient = (result.get(key) for key in ('grant', 'bot', 'recipient'))
        if (not isinstance(grant, str) or not _GRANT.fullmatch(grant)
                or not isinstance(bot, str) or not re.fullmatch(r'[A-Za-z0-9_]{5,32}', bot)
                or not isinstance(recipient, str) or len(recipient) > 128):
            raise HostedBotError('The shared Telegram bot returned invalid chat settings.')
        with self._lock:
            if self._pairing != pairing or self._clock() >= pairing[1]:
                raise ValueError('This link expired. Open a new Telegram link.')
            self._save({'service': self._url, 'grant': grant, 'bot': bot, 'recipient': recipient})
            self._pairing = None

    def disconnect(self):
        with self._lock:
            self._save({})
            self._pairing = None

    def _linked_grant(self):
        with self._lock:
            return self._grant()

    @staticmethod
    def _delivery(result):
        try:
            status = DeliveryStatus(result.get('status'))
        except (ValueError, TypeError):
            return DeliveryStatus.UNCERTAIN
        return status if status != DeliveryStatus.SIMULATED else DeliveryStatus.UNCERTAIN

    def notify(self, incident: Incident, clip: bytes | None = None) -> DeliveryResult:
        if incident.source != DetectionSource.LIVE:
            return FakeSender()(incident)
        grant = self._linked_grant()
        if not grant:
            return FakeSender()(incident)
        if not _INCIDENT.fullmatch(incident.incident_id):
            return DeliveryResult(incident.incident_id, DeliveryStatus.FAILED)
        body = {'grant': grant, 'kind': 'fall', 'incident_id': incident.incident_id, 'replies': True}
        try:
            if clip and len(clip) <= MAX_HOSTED_CLIP_BYTES and clip[4:8] == b'ftyp':
                try:
                    result = self._call('notify', clip, headers={
                        'Authorization': f'Bearer {grant}', 'X-Incident-ID': incident.incident_id,
                    })
                    return DeliveryResult(incident.incident_id, self._delivery(result))
                except HostedBotError as error:
                    if not error.invalid_clip:
                        raise
            result = self._call('notify', body)
            status = self._delivery(result)
        except HostedBotError as error:
            status = (DeliveryStatus.FAILED if error.status is not None and 400 <= error.status < 500
                      else DeliveryStatus.UNCERTAIN)
        return DeliveryResult(incident.incident_id, status)

    def poll_reply(self, incident_id: str) -> CaregiverAction | None:
        grant = self._linked_grant()
        if not grant or not _INCIDENT.fullmatch(incident_id):
            return None
        result = self._call('response', {'grant': grant, 'incident_id': incident_id})
        try:
            return CaregiverAction(result.get('action'))
        except (ValueError, TypeError):
            return None

    def send_test(self) -> DeliveryStatus:
        grant = self._linked_grant()
        if not grant:
            raise ValueError('Link a Telegram chat first.')
        try:
            return self._delivery(self._call('notify', {'grant': grant, 'kind': 'test'}))
        except HostedBotError as error:
            return DeliveryStatus.FAILED if error.status and 400 <= error.status < 500 else DeliveryStatus.UNCERTAIN


def create_browser_telegram_link(*, private=False):
    if private:
        from .telegram_link import TelegramLink
        return TelegramLink()
    return HostedTelegramLink(os.environ.get('SAFE_SOUND_TELEGRAM_URL', DEFAULT_BOT_SERVICE))
