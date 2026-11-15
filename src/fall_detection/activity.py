"""Display-only posture evidence from local landmarks; never drives alerts.

Scores measure support for geometric rules, not trained class probabilities.
Only scalar results leave this module. No frames or pose history are persisted.
"""

from collections import deque
from dataclasses import dataclass
from math import acos, atan2, degrees, exp, hypot, isfinite

from .contracts import CameraStatus, PoseStatus, Preview

LABELS = (
    ('standing', 'Standing'), ('walking', 'Walking'), ('sitting', 'Sitting'),
    ('bending', 'Bending'), ('lying', 'Lying / horizontal'), ('unclear', 'Unclear / other'),
)
UPPER_LABELS = (
    ('upright', 'Upright'), ('leaning', 'Leaning'),
    ('horizontal', 'Horizontal torso'), ('unclear', 'Unclear / other'),
)
VISIBILITY = 0.55
MAX_AGE = 2.0
MAX_GAP = 0.3


def ramp(value, low, high):
    return max(0.0, min(1.0, (value - low) / (high - low)))


def angle(a, b, c):
    u, v = (a[0] - b[0], a[1] - b[1]), (c[0] - b[0], c[1] - b[1])
    length = hypot(*u) * hypot(*v)
    return degrees(acos(max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) / length)))) if length else None


@dataclass(frozen=True, slots=True)
class ActivityReport:
    timestamp: float
    state: str
    current: str
    detail: str
    scores: tuple[tuple[str, float | None], ...] = ()
    quality: float | None = None
    fall_score: float | None = None
    scope: str = "full"

    def payload(self, now: float) -> dict:
        if self.state in ('live', 'partial') and not 0 <= now - self.timestamp <= MAX_AGE:
            return unavailable(self.timestamp, 'stale', 'Waiting for a fresh frame.').payload(now)
        values = dict(self.scores)
        labels = UPPER_LABELS if self.scope == 'upper' else LABELS
        rows = [{'id': key, 'label': label, 'score': values.get(key)} for key, label in labels]
        rows.sort(key=lambda row: -(row['score'] if row['score'] is not None else -1))
        return {'state': self.state, 'scope': self.scope, 'current': self.current, 'detail': self.detail,
                'rows': rows, 'trackingQuality': self.quality, 'fallScore': self.fall_score}


def unavailable(timestamp, state, detail):
    return ActivityReport(timestamp, state, 'Unclear', detail)


class ActivityTracker:
    """Small timestamped movement window, independent of the fall classifier."""

    def __init__(self):
        self._history = deque(maxlen=120)
        self._smoothed = {}
        self._timestamp = None
        self._size = None
        self._scope = None
        self._report = unavailable(0.0, 'off', 'Start the camera to see live activity.')

    def clear(self, timestamp, state, detail):
        self._history.clear()
        self._smoothed.clear()
        self._timestamp = None
        self._size = None
        self._scope = None
        self._report = unavailable(timestamp, state, detail)
        return self._report

    def update(self, preview: Preview, *, running=True, paused=False, fault=None):
        t = preview.timestamp
        if preview.source.value != 'live':
            return self.clear(t, 'demo', 'Activity estimates appear with a live camera.')
        if paused or preview.camera_status == CameraStatus.PAUSED:
            return self.clear(t, 'paused', 'Resume monitoring to see live activity.')
        if fault or preview.fault_code is not None or preview.camera_status == CameraStatus.FAILED:
            return self.clear(t, 'fault', 'Tracking unavailable. Check the camera.')
        if not running or preview.camera_status == CameraStatus.STOPPED:
            return self.clear(t, 'off', 'Start the camera to see live activity.')
        if preview.camera_status != CameraStatus.AVAILABLE:
            return self.clear(t, 'starting', 'Waiting for camera frames.')
        if preview.pose_status != PoseStatus.VALID or preview.frame is None:
            return self.clear(t, 'unknown', 'Keep shoulders and hips in view for posture estimates.')
        size = (preview.frame.width, preview.frame.height)
        if self._timestamp is not None and (t < self._timestamp or t - self._timestamp > MAX_GAP or size != self._size):
            self.clear(t, 'unknown', 'Collecting fresh movement.')
        if t == self._timestamp:
            return self._report
        previous_time = self._timestamp
        self._timestamp, self._size = t, size
        points = {p.index: (p.x * size[0], p.y * size[1], p.visibility)
                  for p in preview.landmarks if all(isfinite(v) for v in (p.x, p.y, p.visibility))
                  and 0 <= p.x <= 1 and 0 <= p.y <= 1 and VISIBILITY <= p.visibility <= 1}
        sides = [[points.get(i) for i in indices] for indices in ((11, 23, 25, 27), (12, 24, 26, 28))]
        complete = [side for side in sides if all(p is not None for p in side)]
        fall = preview.model_score if preview.model_ready else None
        if not complete:
            return self._upper_body(t, points, fall, previous_time)
        # Use the best visible side; a side view need not expose both knees.
        shoulder, hip, knee, ankle = max(complete, key=lambda side: min(p[2] for p in side))
        torso = hypot(hip[0] - shoulder[0], hip[1] - shoulder[1])
        thigh = hypot(knee[0] - hip[0], knee[1] - hip[1])
        shin = hypot(ankle[0] - knee[0], ankle[1] - knee[1])
        quality = min(p[2] for p in (shoulder, hip, knee, ankle))
        knee_angle = angle(hip, knee, ankle)
        if torso < 8 or min(thigh, shin) < torso * 0.25 or knee_angle is None or hip[1] <= shoulder[1] - torso * 0.3:
            return self._upper_body(t, points, fall, previous_time)
        tilt = degrees(atan2(abs(hip[0] - shoulder[0]), hip[1] - shoulder[1]))
        thigh_down = (knee[1] - hip[1]) / thigh
        shin_down = (ankle[1] - knee[1]) / shin
        upright = 1 - ramp(tilt, 15, 45)
        straight = ramp(knee_angle, 140, 170)
        legs_down = min(ramp(thigh_down, 0.35, 0.85), ramp(shin_down, 0.35, 0.85))
        bent_knee = min(ramp(knee_angle, 35, 70), 1 - ramp(knee_angle, 115, 150))
        thigh_across = 1 - ramp(abs(thigh_down), 0.25, 0.75)
        body_dx, body_dy = ankle[0] - shoulder[0], ankle[1] - shoulder[1]
        body_length = hypot(body_dx, body_dy)
        body_horizontal = ramp(abs(body_dx) / body_length, 0.70, 0.94) if body_length else 0
        horizontal_torso = min(ramp(tilt, 50, 80), 1 - ramp(tilt, 105, 130))
        gait = self._gait(t, points, torso)
        scores = {
            'standing': min(upright, straight, legs_down) * quality,
            'sitting': min(upright, bent_knee, thigh_across, ramp(shin_down, 0.35, 0.85)) * quality,
            'bending': min(ramp(tilt, 25, 60), straight, legs_down) * quality,
            'lying': min(horizontal_torso, body_horizontal) * quality,
            'walking': min(upright, gait) * quality if gait is not None else None,
        }
        if scores['walking'] is not None:
            scores['standing'] *= 1 - scores['walking']
        return self._finish(t, scores, quality, fall, previous_time)

    def _upper_body(self, timestamp, points, fall, previous_time):
        # Legs may be missing, out of frame, or too foreshortened to use. Do not
        # infer standing, sitting, walking, or lying down from a torso alone.
        self._history.clear()
        sides = [(points.get(a), points.get(b)) for a, b in ((11, 23), (12, 24))]
        visible = [side for side in sides if all(p is not None for p in side)]
        if visible:
            shoulder, hip = max(visible, key=lambda side: min(p[2] for p in side))
            length = hypot(hip[0] - shoulder[0], hip[1] - shoulder[1])
            if length >= 8 and hip[1] >= shoulder[1] - length * 0.3:
                tilt = degrees(atan2(abs(hip[0] - shoulder[0]), hip[1] - shoulder[1]))
                quality = min(shoulder[2], hip[2])
                scores = {
                    'upright': (1 - ramp(tilt, 15, 45)) * quality,
                    'leaning': min(ramp(tilt, 15, 45), 1 - ramp(tilt, 65, 85)) * quality,
                    'horizontal': min(ramp(tilt, 55, 80), 1 - ramp(tilt, 105, 130)) * quality,
                }
                return self._finish(timestamp, scores, quality, fall, previous_time, scope='upper')
        self._smoothed.clear()
        self._scope = None
        self._report = ActivityReport(timestamp, 'partial', 'Unclear',
                                      'Show shoulders and hips to estimate upper-body posture.', fall_score=fall)
        return self._report

    def _finish(self, timestamp, scores, quality, fall, previous_time, *, scope='full'):
        # Never carry a full-body score across a cropped view, or vice versa.
        if self._scope != scope:
            self._smoothed.clear()
        self._scope = scope
        alpha = 1 - exp(-(timestamp - previous_time) / 0.20) if previous_time is not None else 1
        for key, value in scores.items():
            if value is None:
                self._smoothed.pop(key, None)
            else:
                self._smoothed[key] = self._smoothed.get(key, value) * (1 - alpha) + value * alpha
        values = {key: self._smoothed.get(key) for key in scores}
        best = max((key for key in values if values[key] is not None), key=lambda key: values[key])
        values['unclear'] = 1 - values[best]
        labels = UPPER_LABELS if scope == 'upper' else LABELS
        current = dict(labels)[best] if values[best] >= 0.5 else 'Unclear'
        detail = ('Upper body only · no leg visibility needed.' if scope == 'upper'
                  else 'Live posture estimates · scores out of 100.')
        self._report = ActivityReport(timestamp, 'partial' if scope == 'upper' else 'live', current, detail,
                                      tuple(values.items()), quality, fall, scope)
        return self._report

    def _gait(self, timestamp, points, torso):
        if not all(i in points for i in (27, 28)):
            self._history.clear()
            return None
        left, right = points[27], points[28]
        self._history.append((timestamp, (left[0] - right[0]) / torso, (left[1] - right[1]) / torso))
        while self._history and timestamp - self._history[0][0] > 1.8:
            self._history.popleft()
        if len(self._history) < 8 or timestamp - self._history[0][0] < 0.8:
            return None
        def support(axis, deadband, low, high):
            sign = 0
            changes = 0
            last_change = float('-inf')
            for t, x, y in self._history:
                value = (x, y)[axis]
                current = 1 if value > deadband else -1 if value < -deadband else 0
                if current and current != sign and t - last_change >= 0.15:
                    if sign:
                        changes += 1
                    sign, last_change = current, t
            values = [row[axis + 1] for row in self._history]
            return min(changes / 2, 1) * ramp(max(values) - min(values), low, high)
        return max(support(0, 0.10, 0.3, 0.8), support(1, 0.07, 0.15, 0.4))
