from dataclasses import replace
from math import sin, pi
import unittest

from fall_detection.activity import ActivityTracker
from fall_detection.contracts import BgrFrame, CameraStatus, DetectionSource, FaultCode, Landmark, PoseStatus, Preview

FRAME = BgrFrame(640, 480, bytes(640 * 480 * 3))
POSITIONS = {
    'standing': ((.45, .2), (.45, .45), (.45, .67), (.45, .9)),
    'sitting': ((.35, .2), (.35, .45), (.62, .45), (.62, .75)),
    'bending': ((.65, .35), (.4, .5), (.4, .7), (.4, .9)),
    'lying': ((.75, .6), (.55, .6), (.35, .6), (.15, .6)),
}


def preview(posture='standing', t=10.0, *, model=True):
    points = []
    for indices, offset in [((11, 23, 25, 27), 0), ((12, 24, 26, 28), .06)]:
        points.extend(Landmark(index, x + offset, y, .95)
                      for index, (x, y) in zip(indices, POSITIONS[posture]))
    return Preview(t, DetectionSource.LIVE, CameraStatus.AVAILABLE, PoseStatus.VALID,
                   model, .3 if model else None, FRAME, tuple(points))


class ActivityTests(unittest.TestCase):
    def test_clear_postures_rank_from_measured_geometry(self):
        for posture in POSITIONS:
            with self.subTest(posture=posture):
                report = ActivityTracker().update(preview(posture))
                rows = report.payload(10)['rows']
                self.assertEqual(rows[0]['id'], posture)
                self.assertGreater(rows[0]['score'], .8)
                self.assertEqual(report.state, 'live')
                self.assertNotEqual(report.current, 'Unclear')

    def test_posture_is_available_during_fall_model_warmup(self):
        report = ActivityTracker().update(preview(model=False)).payload(10)
        self.assertEqual(report['current'], 'Standing')
        self.assertIsNone(report['fallScore'])

    def test_motion_needs_a_sequence_of_alternating_steps(self):
        tracker = ActivityTracker()
        for i in range(55):
            t = 10 + i / 30
            p = preview(t=t)
            step = .14 * sin(2 * pi * i / 18)
            points = tuple(replace(point, x=point.x + (step if point.index == 27 else -step))
                           if point.index in (27, 28) else point for point in p.landmarks)
            report = tracker.update(replace(p, landmarks=points))
        self.assertEqual(report.current, 'Walking')
        self.assertGreater(dict(report.scores)['walking'], .8)

    def test_small_landmark_jitter_is_not_walking(self):
        tracker = ActivityTracker()
        for i in range(60):
            p = preview(t=10 + i / 30)
            points = tuple(replace(point, y=point.y + .002 * (-1) ** i)
                           if point.index == 27 else point for point in p.landmarks)
            report = tracker.update(replace(p, landmarks=points))
        self.assertEqual(report.current, 'Standing')
        self.assertEqual(dict(report.scores)['walking'], 0)

    def test_duplicate_frames_do_not_create_movement_history(self):
        tracker = ActivityTracker()
        p = preview()
        for _ in range(100):
            report = tracker.update(p)
        self.assertIsNone(dict(report.scores)['walking'])

    def test_pose_loss_pause_stop_fault_and_simulation_clear_scores(self):
        for change, options in [
            ({'pose_status': PoseStatus.NOT_VISIBLE}, {}),
            ({'pose_status': PoseStatus.UNRELIABLE}, {}),
            ({}, {'paused': True}), ({}, {'running': False}),
            ({}, {'fault': 'camera_failure'}),
            ({'source': DetectionSource.SIMULATED}, {}),
            ({'fault_code': FaultCode.WORKER_FAILURE}, {}),
            ({'camera_status': CameraStatus.STARTING}, {}),
        ]:
            with self.subTest(change=change, options=options):
                tracker = ActivityTracker()
                tracker.update(preview())
                data = tracker.update(replace(preview(t=11), **change), **options).payload(11)
                self.assertEqual(data['current'], 'Unclear')
                self.assertTrue(all(row['score'] is None for row in data['rows']))
                self.assertIsNone(data['fallScore'])

    def test_stale_report_cannot_present_cached_scores_as_live(self):
        report = ActivityTracker().update(preview())
        for now in (9.9, 12.01):
            data = report.payload(now)
            self.assertEqual(data['state'], 'stale')
            self.assertTrue(all(row['score'] is None for row in data['rows']))
            self.assertIsNone(data['fallScore'])

    def test_feet_out_of_frame_do_not_guess_standing_or_sitting(self):
        p = preview()
        for points in [tuple(point for point in p.landmarks if point.index not in (27, 28)),
                       tuple(replace(point, y=1.1) if point.index in (27, 28) else point for point in p.landmarks),
                       tuple(replace(point, visibility=.1) if point.index in (25, 26) else point for point in p.landmarks)]:
            data = ActivityTracker().update(replace(p, landmarks=points)).payload(10)
            self.assertEqual(data['state'], 'partial')
            self.assertEqual(data['scope'], 'upper')
            self.assertEqual(data['current'], 'Upright')
            self.assertEqual(data['rows'][0]['id'], 'upright')
            self.assertNotIn('standing', [row['id'] for row in data['rows']])
            self.assertNotIn('sitting', [row['id'] for row in data['rows']])
            self.assertEqual(data['fallScore'], .3)  # Independent, real model output.

    def test_side_view_with_one_visible_leg_can_be_used(self):
        p = preview('sitting')
        p = replace(p, landmarks=tuple(point for point in p.landmarks if point.index % 2))
        self.assertEqual(ActivityTracker().update(p).current, 'Sitting')

    def test_pixel_geometry_is_invariant_to_resizing_and_canvas_aspect(self):
        p = preview('bending')
        expected = ActivityTracker().update(p)
        # Preserve pixel positions while widening the camera canvas.
        wider = replace(p, frame=BgrFrame(1280, 480, bytes(1280 * 480 * 3)),
                        landmarks=tuple(replace(point, x=point.x / 2) for point in p.landmarks))
        self.assertEqual(ActivityTracker().update(wider).scores, expected.scores)

    def test_degenerate_torso_is_unknown(self):
        p = preview()
        p = replace(p, landmarks=tuple(replace(point, y=.2) if point.index in (23, 24) else point for point in p.landmarks))
        report = ActivityTracker().update(p)
        self.assertEqual(report.current, 'Unclear')
        self.assertEqual(report.state, 'partial')

    def test_gap_resolution_change_and_time_reversal_reset_walking(self):
        tracker = ActivityTracker()
        for i in range(40):
            tracker.update(preview(t=10 + i / 30))
        self.assertIsNotNone(dict(tracker.update(preview(t=11.4)).scores)['walking'])
        self.assertIsNone(dict(tracker.update(preview(t=13)).scores)['walking'])
        self.assertIsNone(dict(tracker.update(preview(t=10)).scores)['walking'])
        p = preview(t=10.1)
        p = replace(p, frame=BgrFrame(800, 480, bytes(800 * 480 * 3)))
        self.assertIsNone(dict(tracker.update(p).scores)['walking'])


    def test_upper_body_only_classifies_torso_without_knees_or_feet(self):
        for posture, expected in [('standing', 'Upright'), ('sitting', 'Upright'),
                                  ('bending', 'Leaning'), ('lying', 'Horizontal torso')]:
            p = preview(posture)
            cropped = replace(p, landmarks=tuple(point for point in p.landmarks if point.index in (11, 12, 23, 24)))
            data = ActivityTracker().update(cropped).payload(10)
            self.assertEqual(data['current'], expected)
            self.assertEqual(data['scope'], 'upper')
            self.assertTrue(all(row['id'] not in ('standing', 'sitting', 'walking', 'lying') for row in data['rows']))

    def test_missing_hips_cannot_invent_an_upper_body_posture(self):
        p = preview()
        p = replace(p, landmarks=tuple(point for point in p.landmarks if point.index in (11, 12)))
        data = ActivityTracker().update(p).payload(10)
        self.assertEqual(data['current'], 'Unclear')
        self.assertTrue(all(row['score'] is None for row in data['rows']))

    def test_cropping_clears_full_body_scores_and_restoring_view_restarts_gait(self):
        tracker = ActivityTracker()
        for i in range(40):
            tracker.update(preview(t=10 + i / 30))
        p = preview(t=11.4)
        cropped = replace(p, landmarks=tuple(point for point in p.landmarks if point.index not in (27, 28)))
        upper = tracker.update(cropped).payload(11.4)
        self.assertEqual(upper['scope'], 'upper')
        restored = tracker.update(preview(t=11.5)).payload(11.5)
        self.assertEqual(restored['scope'], 'full')
        self.assertEqual(restored['current'], 'Standing')
        self.assertIsNone(next(row['score'] for row in restored['rows'] if row['id'] == 'walking'))
        self.assertNotIn('upright', [row['id'] for row in restored['rows']])

    def test_stale_partial_view_and_pose_loss_clear_upper_scores(self):
        tracker = ActivityTracker()
        p = preview()
        p = replace(p, landmarks=tuple(point for point in p.landmarks if point.index not in (27, 28)))
        upper = tracker.update(p)
        self.assertEqual(upper.current, 'Upright')
        self.assertTrue(all(row['score'] is None for row in upper.payload(13)['rows']))
        missing = tracker.update(replace(p, timestamp=10.1, pose_status=PoseStatus.NOT_VISIBLE)).payload(10.1)
        self.assertTrue(all(row['score'] is None for row in missing['rows']))
