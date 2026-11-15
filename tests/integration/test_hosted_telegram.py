from dataclasses import replace
import http.client
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fall_detection.contracts import DetectionSource
from fall_detection.hosted_telegram import HostedBotError, HostedTelegramLink, create_browser_telegram_link
from fall_detection.notifier import CaregiverAction, DeliveryStatus
from fall_detection.safety import Incident
from fall_detection.web_device import WebDevice

GRANT = '123456.' + 'a' * 64
LINK = {'ticket': 'A' * 48, 'url': 'https://t.me/shared_demo_bot?start=fall_' + 'B' * 48,
        'command': '/start fall_' + 'B' * 48}
CHAT = {'grant': GRANT, 'bot': 'shared_demo_bot', 'recipient': 'Demo caregiver'}
EVENT = Incident('11111111-1111-4111-8111-111111111111', DetectionSource.LIVE, 10, 30)


class HostedTelegramTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / '.env.telegram-hosted.json'
        self.now = 10
        self.client = HostedTelegramLink('https://shared.example', self.path, clock=lambda: self.now)
        self.call = Mock()
        self.client._call = self.call

    def pair(self):
        self.call.side_effect = [LINK, CHAT]
        result = self.client.begin('private token must never be forwarded')
        self.assertEqual(result, {'url': LINK['url'], 'command': LINK['command']})
        self.client.finish()
        self.call.side_effect = None
        self.call.reset_mock()

    def test_pairing_needs_no_bot_token_and_saves_only_chat_scoped_credentials(self):
        personal = self.path.parent / '.env.telegram.json'
        personal.write_text('keep existing private-bot settings')
        self.call.side_effect = [LINK, CHAT]
        self.client.begin('never forward this token')
        self.call.assert_called_once_with('link', {})
        self.client.finish()
        self.assertEqual(self.call.call_args.args, ('finish', {'ticket': LINK['ticket']}))
        self.assertEqual(personal.read_text(), 'keep existing private-bot settings')
        settings = json.loads(self.path.read_text())
        self.assertNotIn('token', settings)
        self.assertEqual(settings['grant'], GRANT)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.client.status()['managed'])
        self.assertTrue(self.client.status()['linked'])
        self.assertNotIn(GRANT, json.dumps(self.client.status()))
        restored = HostedTelegramLink('https://shared.example', self.path)
        self.assertTrue(restored.status()['linked'])
        self.assertFalse(HostedTelegramLink('https://different.example', self.path).status()['linked'])

    def test_disconnect_clears_pairing_and_grant(self):
        self.pair()
        self.client.disconnect()
        self.assertFalse(self.client.status()['linked'])
        self.assertEqual(json.loads(self.path.read_text()), {})
        with self.assertRaises(ValueError):
            self.client.finish()
        self.call.assert_not_called()

    def test_expired_or_replaced_pairing_cannot_save_credentials(self):
        self.call.return_value = LINK
        self.client.begin()
        self.now = 610
        self.call.reset_mock()
        with self.assertRaises(ValueError):
            self.client.finish()
        self.call.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_only_telegram_links_and_valid_settings_are_accepted(self):
        for bad in [dict(LINK, url='https://evil.example/start'), dict(LINK, ticket='bad'),
                    dict(LINK, command='/start different')]:
            self.call.return_value = bad
            with self.assertRaises(HostedBotError):
                self.client.begin()
        self.call.return_value = LINK
        self.client.begin()
        self.call.return_value = dict(CHAT, grant='bad\r\nHeader: injected')
        with self.assertRaises(HostedBotError):
            self.client.finish()
        self.assertFalse(self.path.exists())

    def test_simulation_and_unlinked_monitor_cannot_send(self):
        self.client.notify(EVENT)
        self.call.assert_not_called()
        self.pair()
        self.client.notify(replace(EVENT, source=DetectionSource.SIMULATED), b'clip')
        self.call.assert_not_called()

    def test_fall_alert_and_caregiver_reply_use_the_browser_service_protocol(self):
        self.pair()
        self.call.return_value = {'status': DeliveryStatus.ACCEPTED.value}
        result = self.client.notify(EVENT)
        self.assertEqual(result.status, DeliveryStatus.ACCEPTED)
        self.call.assert_called_once_with('notify', {'grant': GRANT, 'kind': 'fall',
                                                   'incident_id': EVENT.incident_id, 'replies': True})
        for action in CaregiverAction:
            self.call.return_value = {'action': action.value}
            self.assertEqual(self.client.poll_reply(EVENT.incident_id), action)
            self.call.assert_called_with('response', {'grant': GRANT, 'incident_id': EVENT.incident_id})
        self.call.return_value = {'action': None}
        self.assertIsNone(self.client.poll_reply(EVENT.incident_id))

    def test_clip_uses_binary_body_and_header_grant(self):
        self.pair()
        clip = b'\0\0\0\x18ftyp' + b'example mp4 bytes'
        self.call.return_value = {'status': DeliveryStatus.ACCEPTED.value}
        self.assertEqual(self.client.notify(EVENT, clip).status, DeliveryStatus.ACCEPTED)
        self.call.assert_called_once_with('notify', clip, headers={
            'Authorization': f'Bearer {GRANT}', 'X-Incident-ID': EVENT.incident_id})

    def test_only_explicit_clip_rejection_allows_text_fallback(self):
        self.pair()
        clip = b'\0\0\0\x18ftyp' + b'example mp4 bytes'
        self.call.side_effect = [HostedBotError('Invalid clip', status=400, invalid_clip=True),
                                 {'status': DeliveryStatus.ACCEPTED.value}]
        self.assertEqual(self.client.notify(EVENT, clip).status, DeliveryStatus.ACCEPTED)
        self.assertEqual(self.call.call_count, 2)
        self.call.reset_mock()
        self.call.side_effect = HostedBotError('Timeout')
        self.assertEqual(self.client.notify(EVENT, clip).status, DeliveryStatus.UNCERTAIN)
        self.call.assert_called_once()

    def test_service_failures_are_distinguished_without_duplicate_attempts(self):
        self.pair()
        for code, expected in [(403, DeliveryStatus.FAILED), (503, DeliveryStatus.UNCERTAIN), (None, DeliveryStatus.UNCERTAIN)]:
            self.call.reset_mock()
            self.call.side_effect = HostedBotError('Unavailable', status=code)
            self.assertEqual(self.client.notify(EVENT).status, expected)
            self.call.assert_called_once()

    def test_setup_message_is_only_requested_explicitly(self):
        with self.assertRaises(ValueError):
            self.client.send_test()
        self.call.assert_not_called()
        self.pair()
        self.call.return_value = {'status': DeliveryStatus.ACCEPTED.value}
        self.assertEqual(self.client.send_test(), DeliveryStatus.ACCEPTED)
        self.call.assert_called_once_with('notify', {'grant': GRANT, 'kind': 'test'})

    def test_non_https_and_credential_bearing_service_urls_are_rejected(self):
        for url in ['http://shared.example', 'https://token@shared.example', 'https://shared.example/path',
                    'https://shared.example?grant=secret', 'https://shared.example/#fragment']:
            with self.assertRaises(ValueError):
                HostedTelegramLink(url, self.path)

    def test_transport_keeps_credentials_out_of_urls_and_sanitizes_network_errors(self):
        connection = Mock()
        response = Mock(status=200)
        response.read.return_value = b'{"action":"help"}'
        connection.getresponse.return_value = response
        factory = Mock(return_value=connection)
        client = HostedTelegramLink('https://shared.example', self.path, connection_factory=factory)
        self.assertEqual(client._call('response', {'grant': GRANT}), {'action': 'help'})
        args, kwargs = connection.request.call_args
        self.assertEqual(args, ('POST', '/telegram/response'))
        self.assertEqual(json.loads(kwargs['body']), {'grant': GRANT})
        self.assertNotIn(GRANT, args[1])
        connection.request.side_effect = OSError('sensitive credential ' + GRANT)
        with self.assertRaises(HostedBotError) as error:
            client._call('response', {'grant': GRANT})
        self.assertNotIn(GRANT, str(error.exception))
        connection.close.assert_called()

    def test_shared_bot_is_default_and_private_bot_remains_explicit(self):
        with patch('fall_detection.hosted_telegram.HostedTelegramLink') as shared:
            self.assertIs(create_browser_telegram_link(), shared.return_value)
        with patch('fall_detection.telegram_link.TelegramLink') as private:
            self.assertIs(create_browser_telegram_link(private=True), private.return_value)

    def test_local_browser_exposes_managed_pairing_without_private_credentials(self):
        device = WebDevice(0, live=True, countdown=20, telegram=self.client)
        self.addCleanup(device.close)
        self.call.return_value = LINK
        connection = http.client.HTTPConnection(device.host, timeout=2)
        self.addCleanup(connection.close)
        connection.request('GET', '/api/status')
        status = json.loads(connection.getresponse().read())
        self.assertTrue(status['telegram']['managed'])
        self.assertFalse(status['telegram']['hasToken'])
        connection.request('POST', '/api/telegram/link', '{}',
                           {'Content-Type': 'application/json', 'Origin': device.url})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        link = json.loads(response.read())
        self.assertEqual(link['url'], LINK['url'])
        self.assertNotIn('ticket', link)
        self.call.assert_called_once_with('link', {})
