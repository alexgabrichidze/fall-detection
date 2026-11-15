from dataclasses import replace
import http.client
import json
from time import monotonic
import unittest

from fall_detection.contracts import PoseStatus
from fall_detection.device import View
from fall_detection.safety import SafetyState
from fall_detection.web_device import WebDevice
from tests.device.test_activity import preview


class ActivityPanelTests(unittest.TestCase):
    def setUp(self):
        self.device = WebDevice(0, live=True, countdown=20, telegram=None)
        self.addCleanup(self.device.close)

    def status(self):
        connection = http.client.HTTPConnection(self.device.host, timeout=2)
        try:
            connection.request('GET', '/api/status')
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            return json.loads(response.read())
        finally:
            connection.close()

    def view(self, posture='standing'):
        return View(preview(posture, monotonic()), SafetyState.MONITORING, None,
                    'none', 'not requested', False, None, None, True)

    def test_actual_scalar_scores_reach_status_without_frames_or_landmarks(self):
        view = self.view('sitting')
        self.device.render(view)
        result = self.status()
        self.assertEqual(result['activity']['current'], 'Sitting')
        self.assertEqual(result['activity']['fallScore'], .3)
        self.assertEqual(result['activity']['rows'][0]['id'], 'sitting')
        serialized = json.dumps(result)
        self.assertNotIn('landmarks', serialized)
        self.assertNotIn('frame.data', serialized)

    def test_paused_unknown_fault_and_stopped_states_clear_rankings(self):
        for modifier in [lambda v: replace(v, paused=True),
                         lambda v: replace(v, running=False),
                         lambda v: replace(v, detector_error='camera failure'),
                         lambda v: replace(v, preview=replace(v.preview, pose_status=PoseStatus.NOT_VISIBLE))]:
            self.device.render(self.view())
            self.device.render(modifier(self.view()))
            report = self.status()['activity']
            self.assertTrue(all(row['score'] is None for row in report['rows']))
            self.assertIsNone(report['fallScore'])

    def test_displayed_posture_and_fall_score_do_not_change_incident(self):
        view = self.view()
        view = replace(view, preview=replace(view.preview, model_score=.99),
                       state=SafetyState.AWAITING_RESPONSE, remaining=7.5, incident_id='existing')
        self.device.render(view)
        status = self.status()
        self.assertEqual(status['activity']['fallScore'], .99)
        self.assertEqual(status['incident'], 'awaiting_response')
        self.assertEqual(status['remaining'], 7.5)
        self.assertEqual(self.device.poll_commands(), ())


    def test_cropped_upper_body_reaches_the_live_panel(self):
        view = self.view()
        cropped = replace(view.preview, landmarks=tuple(point for point in view.preview.landmarks if point.index not in (27, 28)))
        self.device.render(replace(view, preview=cropped))
        report = self.status()['activity']
        self.assertEqual(report['scope'], 'upper')
        self.assertEqual(report['current'], 'Upright')
        self.assertEqual(len(report['rows']), 4)
        self.assertEqual(report['rows'][0]['id'], 'upright')
