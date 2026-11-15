import unittest
from unittest.mock import Mock

from fall_detection.audio import AudioResult
from fall_detection.config import ApplicationConfig
from fall_detection.device import Command
from fall_detection.fake_detector import FakeDetector
from fall_detection.main import Application
from fall_detection.safety import SafetyState


class WarningCountdownTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.detector = FakeDetector(clock=lambda: self.now)
        self.audio, self.notifier = Mock(), Mock()
        self.audio.play.return_value = self.notifier.submit.return_value = True
        self.audio.poll.return_value = self.notifier.poll.return_value = None
        self.app = Application(self.detector, self.notifier, self.audio,
                               config=ApplicationConfig(countdown_seconds=20), clock=lambda: self.now)
        self.app.start()

    def fall(self):
        self.detector.emit_possible_fall()
        return self.app.step()

    def finish(self, status='played'):
        self.audio.poll.return_value = AudioResult(self.app.safety.incident.incident_id, status)
        view = self.app.step()
        self.audio.poll.return_value = None
        return view

    def test_six_second_warning_is_followed_by_a_full_twenty_second_window(self):
        self.assertEqual(self.fall().state, SafetyState.ANNOUNCING)
        self.assertIsNone(self.app.safety.incident.deadline)
        self.now = 16
        self.assertIsNone(self.app.step().remaining)
        self.notifier.submit.assert_not_called()
        view = self.finish()
        self.assertEqual(view.state, SafetyState.AWAITING_RESPONSE)
        self.assertEqual(view.remaining, 20)
        self.assertEqual(self.app.safety.incident.deadline, 36)
        self.now = 35.99
        self.assertGreater(self.app.step().remaining, 0)
        self.notifier.submit.assert_not_called()
        self.now = 36
        self.assertEqual(self.app.step().state, SafetyState.ALERTING)
        self.app.step()
        self.notifier.submit.assert_called_once()

    def test_cancel_during_warning_ignores_its_late_completion(self):
        self.fall()
        self.now = 14
        self.assertEqual(self.app.step((Command.CANCEL,)).state, SafetyState.CANCELLED)
        self.assertEqual(self.finish().state, SafetyState.CANCELLED)
        self.now = 100
        self.app.step()
        self.notifier.submit.assert_not_called()

    def test_audio_failure_starts_full_window_without_blocking_manual_controls(self):
        self.fall()
        self.now = 13
        self.assertEqual(self.finish('error: output failed').remaining, 20)
        self.assertEqual(self.app.step((Command.CANCEL,)).state, SafetyState.CANCELLED)
        self.notifier.submit.assert_not_called()

    def test_unavailable_audio_worker_starts_full_window_immediately(self):
        self.audio.play.return_value = False
        view = self.fall()
        self.assertEqual(view.state, SafetyState.AWAITING_RESPONSE)
        self.assertEqual(view.remaining, 20)
        self.assertIn('error', view.audio)

    def test_stalled_worker_is_cancelled_before_starting_a_full_window(self):
        self.fall()
        self.now = 22
        self.assertEqual(self.app.step().remaining, 20)
        self.audio.cancel.assert_called_once()
        self.now = 24
        self.assertEqual(self.finish().remaining, 18)
        self.assertEqual(self.app.safety.incident.deadline, 42)
        self.notifier.submit.assert_not_called()

    def test_reminder_and_duplicate_falls_cannot_restart_response_timer(self):
        self.fall()
        self.now = 16
        self.finish()
        self.detector.emit_possible_fall()
        self.now = 26
        self.assertEqual(self.app.step().remaining, 10)
        self.assertEqual(self.audio.play.call_args.args[1].name, 'ten-seconds-left.wav')
        self.now = 27.5
        self.assertEqual(self.finish().remaining, 8.5)
        self.assertEqual(self.app.safety.incident.deadline, 36)

    def test_stale_playback_result_cannot_start_a_new_incident_countdown(self):
        self.fall()
        self.audio.poll.return_value = AudioResult('old incident', 'played')
        self.now = 15
        self.assertEqual(self.app.step().state, SafetyState.ANNOUNCING)
        self.assertIsNone(self.app.safety.incident.deadline)


    def test_busy_audio_waits_for_the_warning_instead_of_skipping_it(self):
        self.audio.busy = True
        self.audio.play.return_value = False
        self.assertEqual(self.fall().state, SafetyState.ANNOUNCING)
        self.assertIsNone(self.app.view().remaining)
        self.now = 12
        self.audio.busy = False
        self.audio.play.return_value = True
        self.assertEqual(self.app.step().state, SafetyState.ANNOUNCING)
        self.now = 18
        self.assertEqual(self.finish().remaining, 20)
        self.assertEqual(self.app.safety.incident.deadline, 38)
