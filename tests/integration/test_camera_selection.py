import http.client
import json
from threading import Event
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fall_detection.camera_inputs import list_camera_inputs
from fall_detection.config import ApplicationConfig
from fall_detection.contracts import CameraStatus, DetectionEvent, DetectionSource, EventKind, Preview
from fall_detection.detection import LiveDetector
from fall_detection.device import View
from fall_detection.main import Application
from fall_detection.safety import SafetyState
from fall_detection.web_device import WebDevice


class CameraSelectionTests(unittest.TestCase):
    def setUp(self):
        # No checkpoint loading, camera capture, audio, or notification network.
        self.detector = LiveDetector(0, b'')
        self.device = WebDevice(0, live=True, countdown=20, telegram=None,
                                camera_index=lambda: self.detector.camera_index,
                                select_camera=self.detector.select_camera)
        self.device.render(View(self.detector.latest_preview(), SafetyState.MONITORING,
                                None, 'none', 'not requested', False, None, None))
        self.discovery = patch('fall_detection.web_device.list_camera_inputs', return_value=[
            {'index': 0, 'name': 'Built-in camera'}, {'index': 2, 'name': 'USB camera'},
        ])
        self.discovery.start()
        self.addCleanup(self.discovery.stop)
        self.addCleanup(self.device.close)

    def request(self, path, body=None, origin=None):
        connection = http.client.HTTPConnection(self.device.host, timeout=2)
        try:
            headers = {'Origin': self.device.url if origin is None else origin}
            if body is not None:
                headers['Content-Type'] = 'application/json'
            connection.request('GET' if body is None else 'POST', path,
                               None if body is None else json.dumps(body), headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_list_and_selection_use_named_capture_indices(self):
        status, data = self.request('/api/cameras')
        self.assertEqual(status, 200)
        self.assertEqual(data['selected'], 0)
        self.assertEqual([camera['index'] for camera in data['cameras']], [0, 2])
        self.assertEqual(self.request('/api/camera', {'index': 2}), (200, {'selected': 2}))
        self.assertEqual(self.request('/api/status')[1]['cameraInput'], 2)
        self.assertEqual(self.detector.latest_preview().camera_status, CameraStatus.STOPPED)
        self.assertIsNone(self.detector.latest_preview().frame)
        self.assertIsNone(self.detector.poll_event())

    def test_invalid_and_disconnected_inputs_are_rejected(self):
        for index in (True, -1, 256, 1.5, '2', None, 99):
            with self.subTest(index=index):
                self.assertEqual(self.request('/api/camera', {'index': index})[0], 400)
                self.assertEqual(self.detector.camera_index, 0)

    def test_running_or_permission_pending_capture_cannot_change_input(self):
        stopped = self.device._view
        from dataclasses import replace
        self.device.render(replace(stopped, running=True))
        self.assertEqual(self.request('/api/camera', {'index': 2})[0], 400)
        self.device.render(stopped)
        self.device.permission_pending = True
        self.assertEqual(self.request('/api/camera', {'index': 2})[0], 400)
        self.assertEqual(self.detector.camera_index, 0)

    def test_detector_lock_rejects_selection_if_status_has_not_caught_up(self):
        # UI still says stopped, but the main loop already began capture.
        self.detector._session = Event()
        self.assertEqual(self.request('/api/camera', {'index': 2})[0], 400)
        self.assertEqual(self.detector.camera_index, 0)
        self.detector.stop()
        self.assertEqual(self.request('/api/camera', {'index': 2})[0], 200)

    def test_cross_origin_selection_is_rejected(self):
        self.assertEqual(self.request('/api/camera', {'index': 2}, origin='https://example.com')[0], 403)
        self.assertEqual(self.detector.camera_index, 0)

    def test_discovery_failure_is_visible_and_does_not_change_input(self):
        with patch('fall_detection.web_device.list_camera_inputs', side_effect=OSError()):
            status, body = self.request('/api/cameras')
        self.assertEqual(status, 400)
        self.assertIn('Could not list cameras', body['error'])
        self.assertEqual(self.detector.camera_index, 0)

    def test_initial_cli_input_is_preserved_when_not_detected(self):
        self.detector.select_camera(7)
        status, body = self.request('/api/cameras')
        self.assertEqual(status, 200)
        self.assertEqual(body['selected'], 7)
        self.assertIn({'index': 7, 'name': 'Camera 7 (not detected)'}, body['cameras'])

    def test_stop_select_restart_keeps_active_incident_and_deadline(self):
        clock = SimpleNamespace(now=monotonic())
        notifier, audio = Mock(), Mock()
        notifier.poll.return_value = audio.poll.return_value = None
        notifier.submit.return_value = audio.play.return_value = True
        app = Application(self.detector, notifier, audio, config=ApplicationConfig(countdown_seconds=5),
                          clock=lambda: clock.now)
        # Keep the real detector lifecycle, but do not launch hardware workers.
        with patch('fall_detection.detection.Thread'):
            app.start()
            self.detector._events.append(DetectionEvent('fall', clock.now, DetectionSource.LIVE,
                                                        EventKind.POSSIBLE_FALL))
            app.step()
            incident = app.safety.incident
            self.assertEqual(app.safety.state, SafetyState.AWAITING_RESPONSE)
            app.stop()
            self.device.render(app.step())
            self.assertEqual(self.request('/api/camera', {'index': 2})[0], 200)
            app.start()
            self.assertEqual(app.safety.incident, incident)
            clock.now = incident.deadline
            app.step()
            notifier.submit.assert_called_once_with(incident)
            self.assertEqual(app.safety.state, SafetyState.ALERTING)
            app.close()

    def test_simulation_has_no_hardware_selection(self):
        self.device.live = False
        self.assertEqual(self.request('/api/cameras'), (200, {'cameras': [], 'selected': None}))
        self.assertEqual(self.request('/api/camera', {'index': 2})[0], 400)


class CameraDiscoveryTests(unittest.TestCase):
    def test_names_and_indices_use_the_capture_backend_without_starting_streams(self):
        enumerate_cameras = Mock(return_value=[SimpleNamespace(index=2, name='USB camera')])
        with patch.dict('sys.modules', {'cv2_enumerate_cameras': SimpleNamespace(enumerate_cameras=enumerate_cameras)}):
            with patch('fall_detection.camera_inputs.camera_backend', return_value=200):
                self.assertEqual(list_camera_inputs(), [{'index': 2, 'name': 'USB camera'}])
        enumerate_cameras.assert_called_once_with(200)
