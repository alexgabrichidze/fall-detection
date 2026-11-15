import unittest
from unittest.mock import Mock, patch

from fall_detection.audio import AudioResult
from fall_detection.config import ApplicationConfig
from fall_detection.device import Command
from fall_detection.fake_detector import FakeDetector
from fall_detection.main import Application, main
from fall_detection.safety import SafetyController, SafetyState
from fall_detection.voice import VoiceResult


class VoiceCancellationTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.clock = lambda: self.now
        self.detector = FakeDetector(clock=self.clock)
        self.audio, self.notifier, self.voice = Mock(), Mock(), Mock()
        self.audio.poll.return_value = self.notifier.poll.return_value = self.voice.poll.return_value = None
        self.voice.status = ('ready', False)
        self.app = Application(self.detector, self.notifier, self.audio, voice=self.voice,
                               config=ApplicationConfig(countdown_seconds=5), clock=self.clock)
        self.app.start()
        self.detector.emit_possible_fall()
        self.app.step()
        self.incident = self.app.safety.incident

    def prompt_finishes(self):
        self.audio.poll.return_value = AudioResult(self.incident.incident_id, 'played')
        view = self.app.step()
        self.audio.poll.return_value = None
        return view

    def test_no_capture_during_prompt_then_recognition_cancels(self):
        self.voice.start.assert_not_called()
        self.assertEqual(self.app.view().voice, 'waiting for prompt')
        self.prompt_finishes()
        self.voice.start.assert_called_once_with(self.incident.incident_id, self.incident.deadline)
        self.voice.status = ("listening: say I'm okay", True)
        self.assertTrue(self.app.view().listening)
        self.now = 14.9
        self.voice.poll.return_value = VoiceResult(self.incident.incident_id)
        self.assertEqual(self.app.step().state, SafetyState.CANCELLED)
        self.now = 16
        self.app.step()
        self.notifier.submit.assert_not_called()
        self.voice.cancel.assert_called()

    def test_speech_at_deadline_cannot_retract_alert(self):
        self.prompt_finishes()
        self.now = 15
        self.voice.poll.return_value = VoiceResult(self.incident.incident_id)
        self.assertEqual(self.app.step().state, SafetyState.ALERTING)
        self.app.step()
        self.notifier.submit.assert_called_once_with(self.incident)
        self.voice.cancel.assert_called()

    def test_old_incident_result_and_prompt_cannot_cancel_new_incident(self):
        self.app.step((Command.CANCEL,))
        self.now = 11
        self.app.step((Command.RESET,))
        self.now = 12
        self.detector.emit_possible_fall()
        self.app.step()
        self.voice.poll.return_value = VoiceResult(self.incident.incident_id)
        self.prompt_finishes()
        self.assertEqual(self.app.safety.state, SafetyState.AWAITING_RESPONSE)
        self.voice.start.assert_not_called()

    def test_prompt_failure_keeps_manual_cancellation_and_countdown(self):
        self.audio.poll.return_value = AudioResult(self.incident.incident_id, 'error: playback failed')
        self.app.step()
        self.voice.start.assert_not_called()
        self.assertEqual(self.app.step((Command.CANCEL,)).state, SafetyState.CANCELLED)

    def test_voice_error_does_not_suppress_countdown(self):
        self.prompt_finishes()
        self.voice.status = ('unavailable: check voice model and microphone', False)
        self.assertIn('unavailable', self.app.step().voice)
        self.now = 15
        self.assertEqual(self.app.step().state, SafetyState.ALERTING)
        self.notifier.submit.assert_called_once()

    def test_reminder_waits_for_microphone_release_then_resumes_voice(self):
        self.app.safety = SafetyController(20, clock=self.clock)
        self.detector.emit_possible_fall()
        self.app.step()
        self.incident = self.app.safety.incident
        self.prompt_finishes()
        self.voice.start.reset_mock()
        self.audio.play.reset_mock()
        self.voice.idle = False
        self.voice.status = ("listening: say I'm okay", True)
        self.now = 20
        view = self.app.step()
        self.assertEqual(view.remaining, 10)
        self.voice.cancel.assert_called()
        self.audio.play.assert_not_called()
        self.voice.start.assert_not_called()
        self.voice.idle = True
        self.voice.status = ('ready', False)
        self.app.step()
        self.assertEqual(self.audio.play.call_args.args[1].name, 'ten-seconds-left.wav')
        self.voice.start.assert_not_called()
        self.prompt_finishes()
        self.voice.start.assert_called_once_with(self.incident.incident_id, self.incident.deadline)

    def test_voice_cancellation_discards_a_queued_reminder(self):
        self.app.safety = SafetyController(20, clock=self.clock)
        self.detector.emit_possible_fall()
        self.app.step()
        self.incident = self.app.safety.incident
        self.prompt_finishes()
        self.voice.idle = False
        self.now = 20
        self.app.step()
        self.audio.play.reset_mock()
        self.voice.poll.return_value = VoiceResult(self.incident.incident_id)
        self.voice.idle = True
        self.assertEqual(self.app.step().state, SafetyState.CANCELLED)
        self.audio.play.assert_called_once()
        self.assertEqual(self.audio.play.call_args.args[1].name, 'alarm-cancelled.wav')
        self.notifier.submit.assert_not_called()

    def test_browser_exposes_microphone_status(self):
        from fall_detection.web_device import WebDevice
        device = WebDevice(0, live=True, countdown=5, telegram=None)
        try:
            self.prompt_finishes()
            self.voice.status = ("listening: say I'm okay", True)
            device.render(self.app.view())
            status = device.status()
            self.assertTrue(status['listening'])
            self.assertEqual(status['voice'], "listening: say I'm okay")
        finally:
            device.close()

    def test_simulation_rejects_microphone_capture(self):
        with patch("sys.stderr"), self.assertRaises(SystemExit) as error:
            main(['--detector', 'fake', '--voice'])
        self.assertEqual(error.exception.code, 2)
