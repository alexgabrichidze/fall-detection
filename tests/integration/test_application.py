from dataclasses import replace
import unittest
from unittest.mock import Mock

from fall_detection.audio import AudioResult
from fall_detection.config import ApplicationConfig
from fall_detection.contracts import DetectionSource, PoseStatus
from fall_detection.device import Command
from fall_detection.fake_detector import FakeDetector
from fall_detection.main import Application
from fall_detection.notifier import DeliveryResult, DeliveryStatus
from fall_detection.safety import SafetyState


class Clock:
    now = 10.0

    def __call__(self):
        return self.now


class ApplicationFlowTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.detector = FakeDetector(clock=self.clock)
        self.notifier = Mock()
        self.notifier.submit.return_value = True
        self.notifier.poll.return_value = None
        self.audio = Mock()
        self.audio.play.return_value = True
        self.audio.poll.return_value = None
        self.app = Application(self.detector, self.notifier, self.audio,
                               config=ApplicationConfig(countdown_seconds=5), clock=self.clock)
        self.app.start()

    def begin(self):
        self.detector.emit_possible_fall()
        view = self.app.step()
        if view.state == SafetyState.ANNOUNCING:
            self.audio.poll.return_value = AudioResult(self.app.safety.incident.incident_id, "played")
            view = self.app.step()
            self.audio.poll.return_value = None
        return view

    def test_complete_flow_with_audio_error_camera_loss_and_delivery_failure(self):
        self.assertEqual(self.begin().state, SafetyState.AWAITING_RESPONSE)
        incident = self.app.safety.incident
        self.audio.poll.return_value = AudioResult(incident.incident_id, "error: missing WAV")
        self.clock.now = 11
        self.detector.fail_camera()
        view = self.app.step((Command.PAUSE,))
        self.assertTrue(view.paused)
        self.assertEqual(view.remaining, 4)
        self.assertIn("error", view.audio)
        self.assertEqual(view.detector_error, "camera_failure")
        self.clock.now = 15
        self.app.step()
        self.notifier.submit.assert_called_once_with(incident)
        self.audio.play.assert_called_once_with(incident.incident_id)
        self.notifier.poll.return_value = DeliveryResult(incident.incident_id, DeliveryStatus.FAILED)
        view = self.app.step()
        self.assertEqual(view.delivery, "failed")
        self.app.step()
        self.notifier.submit.assert_called_once()
        self.assertEqual(self.app.step((Command.RESET,)).state, SafetyState.MONITORING)
        self.assertIsNotNone(self.app.detector_error)

    def test_cancellation_precedes_expiry_tick_and_never_sends(self):
        self.begin()
        self.clock.now = 14.999999
        self.app.step((Command.CANCEL,))
        self.clock.now = 100
        self.assertEqual(self.app.step().state, SafetyState.CANCELLED)
        self.notifier.submit.assert_not_called()
        self.audio.cancel.assert_called_once()

    def test_repeated_falls_do_not_restart_countdown_or_send_twice(self):
        self.begin()
        incident = self.app.safety.incident
        for now in (11, 12, 14, 15, 16):
            self.clock.now = now
            self.detector.emit_possible_fall()
            self.app.step()
        self.assertEqual(self.app.safety.incident, incident)
        self.audio.play.assert_called_once()
        self.notifier.submit.assert_called_once()

    def test_pose_loss_alone_does_not_start_incident(self):
        self.detector.set_pose_status(PoseStatus.NOT_VISIBLE)
        self.assertEqual(self.app.step().state, SafetyState.MONITORING)
        self.notifier.submit.assert_not_called()
        self.audio.play.assert_not_called()

    def test_detector_exception_does_not_stop_countdown(self):
        self.begin()
        self.detector.poll_event = Mock(side_effect=RuntimeError("worker died"))
        self.clock.now = 15
        view = self.app.step()
        self.assertEqual(view.detector_error, "detector worker unavailable")
        self.assertEqual(view.state, SafetyState.ALERTING)
        self.notifier.submit.assert_called_once()

    def test_unavailable_workers_are_visible_without_cancelling_countdown(self):
        self.audio.play.return_value = False
        self.assertIn("error", self.begin().audio)
        self.notifier.submit.return_value = False
        self.clock.now = 15
        self.assertEqual(self.app.step().delivery, "failed")

    def test_pending_delivery_prevents_reset_and_exit_reports_uncertainty(self):
        self.begin()
        self.clock.now = 15
        self.app.step()
        self.assertEqual(self.app.step((Command.RESET,)).state, SafetyState.ALERTING)
        self.assertTrue(any("delivery uncertain" in text for text in self.app.close()))

    def test_pausing_idle_monitoring_does_not_create_incident(self):
        self.assertTrue(self.app.step((Command.PAUSE,)).paused)
        self.assertEqual(self.app.step((Command.SIMULATE_FALL,)).state, SafetyState.MONITORING)
        self.assertFalse(self.app.step((Command.PAUSE,)).paused)
        self.assertEqual(self.app.step((Command.SIMULATE_FALL,)).state, SafetyState.ANNOUNCING)

    def test_source_mismatch_never_becomes_live_evidence(self):
        self.detector.emit_possible_fall()
        event = self.detector.poll_event()
        self.detector.poll_event = Mock(side_effect=[replace(event, source=DetectionSource.LIVE), None])
        view = self.app.step()
        self.assertEqual(view.state, SafetyState.MONITORING)
        self.assertEqual(view.detector_error, "detector source mismatch")

    def test_reset_clears_detector_history_and_rejects_already_queued_fall(self):
        self.begin()
        self.clock.now = 11
        self.app.step((Command.CANCEL,))
        self.detector.emit_possible_fall()
        self.assertEqual(self.app.step((Command.RESET,)).state, SafetyState.MONITORING)
        self.assertIsNone(self.detector.latest_preview().model_score)
        self.clock.now = 12
        self.assertEqual(self.app.step((Command.SIMULATE_FALL,)).state, SafetyState.ANNOUNCING)

    def test_blocked_notification_transport_leaves_pause_controls_responsive(self):
        from threading import Event
        from fall_detection.notifier import NotificationWorker
        entered, release = Event(), Event()
        def blocked(incident):
            entered.set()
            release.wait(2)
            return DeliveryResult(incident.incident_id, DeliveryStatus.UNCERTAIN)
        worker = NotificationWorker(blocked)
        self.app.notifier = worker
        try:
            self.begin()
            self.clock.now = 15
            self.app.step()
            self.assertTrue(entered.wait(1))
            view = self.app.step((Command.PAUSE,))
            self.assertTrue(view.paused)
            self.assertEqual(view.delivery, "pending")
            self.assertFalse(release.is_set())
        finally:
            release.set()
            self.assertTrue(worker.close(timeout=1))
