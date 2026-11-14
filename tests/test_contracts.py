import unittest

from fall_detection.contracts import (
    BgrFrame,
    CameraStatus,
    DetectionEvent,
    DetectionSource,
    EventKind,
    FaultCode,
    Preview,
)


class ContractTests(unittest.TestCase):
    def test_preview_does_not_leak_frame_bytes_in_repr(self):
        frame = BgrFrame(1, 1, b"abc")
        preview = Preview(
            1.0, DetectionSource.SIMULATED, CameraStatus.AVAILABLE, frame=frame
        )
        self.assertNotIn("abc", repr(frame))
        self.assertNotIn("abc", repr(preview))

    def test_mutable_or_misshaped_frame_buffers_are_rejected(self):
        with self.assertRaises(TypeError):
            BgrFrame(1, 1, bytearray(b"abc"))
        with self.assertRaises(ValueError):
            BgrFrame(2, 2, b"abc")

    def test_unknown_model_cannot_publish_a_current_score(self):
        with self.assertRaises(ValueError):
            Preview(
                1.0, DetectionSource.SIMULATED, CameraStatus.AVAILABLE,
                model_ready=False, model_score=0.0,
            )

    def test_faults_are_structured_and_scores_are_finite(self):
        with self.assertRaises(ValueError):
            DetectionEvent("a", 1.0, DetectionSource.SIMULATED, EventKind.FAULT)
        with self.assertRaises(ValueError):
            DetectionEvent(
                "a", 1.0, DetectionSource.SIMULATED, EventKind.POSSIBLE_FALL,
                fault_code=FaultCode.CAMERA_FAILURE,
            )
        with self.assertRaises(ValueError):
            DetectionEvent(
                "a", 1.0, DetectionSource.SIMULATED, EventKind.POSSIBLE_FALL,
                model_score=float("nan"),
            )

