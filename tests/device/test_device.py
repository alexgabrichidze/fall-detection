import unittest

from fall_detection.contracts import CameraStatus, DetectionSource, PoseStatus, Preview
from fall_detection.device import View, status_lines
from fall_detection.safety import SafetyState


class DeviceStatusTests(unittest.TestCase):
    def test_pause_camera_fault_and_active_incident_are_all_visible(self):
        preview = Preview(10, DetectionSource.SIMULATED, CameraStatus.FAILED,
                          pose_status=PoseStatus.UNKNOWN)
        view = View(preview, SafetyState.AWAITING_RESPONSE, 4, "none", "error: missing WAV",
                    True, "camera_failure", "incident")
        text = "\n".join(status_lines(view))
        for value in ("SIMULATION ONLY", "PAUSED", "camera_failure", "Camera: failed",
                      "pose: unknown", "awaiting_response", "4.0s", "error: missing WAV"):
            self.assertIn(value, text)

    def test_mouse_and_keyboard_controls_use_main_window(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from fall_detection.device import Command, OpenCVDevice
        device = OpenCVDevice.__new__(OpenCVDevice)
        device.cv = SimpleNamespace(EVENT_LBUTTONUP=4, WND_PROP_VISIBLE=0,
                                    waitKey=Mock(return_value=ord('p')),
                                    getWindowProperty=Mock(return_value=1))
        device._commands = []
        device._mouse(4, 100, 680, 0, None)
        self.assertEqual(device.poll_commands(), (Command.CANCEL, Command.PAUSE))
        device.cv.waitKey.return_value = -1
        device.cv.getWindowProperty.return_value = 0
        self.assertEqual(device.poll_commands(), (Command.QUIT,))

    def test_rendering_native_bgr_snapshot_and_landmarks(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("optional UI packages not installed")
        from fall_detection.contracts import BgrFrame, Landmark
        from fall_detection.device import OpenCVDevice
        device = OpenCVDevice.__new__(OpenCVDevice)
        device.cv, device.np = cv2, np
        device._telegram_enabled = False
        frame = BgrFrame(2, 2, bytes([25, 50, 75] * 4))
        preview = Preview(10, DetectionSource.SIMULATED, CameraStatus.AVAILABLE,
                          PoseStatus.VALID, frame=frame,
                          landmarks=(Landmark(11, 0.2, 0.2, 1), Landmark(12, 0.8, 0.2, 1)))
        view = View(preview, SafetyState.MONITORING, None, "none", "not requested", False, None, None)
        canvas = device.compose(view)
        self.assertEqual(canvas.shape, (720, 960, 3))
        self.assertEqual(canvas.dtype, np.uint8)
        np.testing.assert_array_equal(canvas[200, 400], [25, 50, 75])
        self.assertTrue(np.any(np.all(canvas[:420] == [80, 220, 80], axis=2)))
