import unittest

from fall_detection.contracts import (
    CameraStatus,
    Detector,
    DetectionSource,
    EventKind,
    FaultCode,
    PoseStatus,
)
from fall_detection.fake_detector import FakeDetector


class TestClock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now


class FakeDetectorTests(unittest.TestCase):
    def setUp(self):
        self.clock = TestClock()
        self.detector = FakeDetector(clock=self.clock)
        self.detector.start()

    def test_implements_contract_and_starts_without_synthetic_fall(self):
        self.assertIsInstance(self.detector, Detector)
        self.assertEqual(self.detector.source, DetectionSource.SIMULATED)
        self.assertIsNone(self.detector.poll_event())
        preview = self.detector.latest_preview()
        self.assertIsNone(preview.model_score)
        self.assertFalse(preview.model_ready)
        self.assertEqual(preview.pose_status, PoseStatus.UNKNOWN)

    def test_events_survive_preview_replacement_and_remain_fifo(self):
        self.detector.emit_possible_fall(0.8)
        self.clock.now += 1
        self.detector.emit_possible_fall(0.9)
        for _ in range(20):
            self.detector.set_pose_status(PoseStatus.UNRELIABLE)
            self.detector.latest_preview()
        first = self.detector.poll_event()
        second = self.detector.poll_event()
        self.assertEqual((first.model_score, second.model_score), (0.8, 0.9))
        self.assertNotEqual(first.event_id, second.event_id)
        self.assertEqual((first.timestamp, second.timestamp), (10.0, 11.0))
        self.assertIsNone(self.detector.poll_event())

    def test_pause_keeps_pending_event_and_resume_requires_fresh_evidence(self):
        self.detector.emit_possible_fall()
        self.detector.set_paused(True)
        self.assertEqual(self.detector.latest_preview().camera_status, CameraStatus.PAUSED)
        with self.assertRaises(RuntimeError):
            self.detector.emit_possible_fall()
        self.detector.set_paused(False)
        preview = self.detector.latest_preview()
        self.assertFalse(preview.model_ready)
        self.assertIsNone(preview.model_score)
        self.assertEqual(preview.pose_status, PoseStatus.UNKNOWN)
        self.assertEqual(self.detector.poll_event().kind, EventKind.POSSIBLE_FALL)

    def test_missing_person_is_unknown_not_a_fall_or_camera_failure(self):
        self.detector.set_pose_status(PoseStatus.NOT_VISIBLE)
        preview = self.detector.latest_preview()
        self.assertEqual(preview.camera_status, CameraStatus.AVAILABLE)
        self.assertEqual(preview.pose_status, PoseStatus.NOT_VISIBLE)
        self.assertIsNone(preview.model_score)
        self.assertIsNone(self.detector.poll_event())

    def test_camera_failure_is_a_fault_and_survives_pause_resume(self):
        self.detector.fail_camera()
        self.detector.set_paused(True)
        self.detector.set_paused(False)
        event = self.detector.poll_event()
        self.assertEqual(event.kind, EventKind.FAULT)
        self.assertEqual(event.fault_code, FaultCode.CAMERA_FAILURE)
        self.assertEqual(self.detector.latest_preview().camera_status, CameraStatus.FAILED)
        with self.assertRaises(RuntimeError):
            self.detector.emit_possible_fall()

    def test_overflow_is_explicit_preserves_queued_events_and_latches(self):
        detector = FakeDetector(clock=self.clock, event_capacity=2)
        detector.start()
        detector.emit_possible_fall(0.7)
        detector.emit_possible_fall(0.8)
        detector.emit_possible_fall(0.9)
        overflow = detector.poll_event()
        self.assertEqual(overflow.kind, EventKind.FAULT)
        self.assertEqual(overflow.fault_code, FaultCode.EVENT_QUEUE_OVERFLOW)
        self.assertEqual(detector.poll_event().model_score, 0.7)
        self.assertEqual(detector.poll_event().model_score, 0.8)
        self.assertIsNone(detector.poll_event())
        self.assertFalse(detector.latest_preview().model_ready)
        detector.set_paused(True)
        detector.set_paused(False)
        with self.assertRaises(RuntimeError):
            detector.emit_possible_fall()
        detector.stop()
        detector.start()
        detector.emit_possible_fall()
        self.assertEqual(detector.poll_event().kind, EventKind.POSSIBLE_FALL)

    def test_start_is_idempotent_and_stop_preserves_pending_events(self):
        self.detector.emit_possible_fall()
        self.detector.start()
        self.detector.stop()
        self.detector.stop()
        self.assertEqual(self.detector.latest_preview().camera_status, CameraStatus.STOPPED)
        self.assertEqual(self.detector.poll_event().kind, EventKind.POSSIBLE_FALL)
        self.detector.start()
        self.assertIsNone(self.detector.poll_event())

