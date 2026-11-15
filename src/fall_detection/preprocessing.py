"""Convert one tracked pose to the published 30-frame classifier's layout."""

from dataclasses import dataclass
from math import hypot, isfinite

from .contracts import Landmark
from .detection_config import POSE_VISIBILITY

# Alphabetical keypoint names in the training notebook. Keep the face indices
# used by the training CSV extractor, including its mislabeled eyes and ears.
_TRAINING_INDICES = (27, 3, 13, 1, 23, 25, 11, 15, 0, 28, 4, 14, 2, 24, 26, 12, 16)


@dataclass(frozen=True, slots=True)
class PoseSample:
    features: tuple[float, ...]
    torso_y: float
    upright: float


def _visible_center(points: dict[int, Landmark], left: int, right: int) -> tuple[float, float] | None:
    visible = [points[index] for index in (left, right) if points[index].visibility > POSE_VISIBILITY]
    if not visible:
        return None
    return sum(point.x for point in visible) / len(visible), sum(point.y for point in visible) / len(visible)


def prepare_pose(landmarks: tuple[Landmark, ...], width: int, height: int) -> PoseSample | None:
    """Interleave (x, y, visibility) in training order, using pixel geometry."""
    points = {point.index: point for point in landmarks}
    if not all(index in points for index in _TRAINING_INDICES):
        return None
    selected = [points[index] for index in _TRAINING_INDICES]
    if any(not (isfinite(p.x) and isfinite(p.y) and 0 <= p.visibility <= 1) for p in selected):
        return None

    shoulder = _visible_center(points, 11, 12)
    hip = _visible_center(points, 23, 24)
    if shoulder is None or hip is None or any(not 0 <= v <= 1 for v in (*shoulder, *hip)):
        return None
    dx = (hip[0] - shoulder[0]) * width
    dy = (hip[1] - shoulder[1]) * height
    torso_length = hypot(dx, dy)
    if torso_length < 1:
        return None

    # The notebook subtracts the visible hip center and scales x and y by
    # vertical shoulder-to-hip distance. Visibility is passed through.
    scale = abs(dy) if abs(dy) >= 1e-5 else 1.0
    features = tuple(
        value
        for p in selected
        for value in ((p.x - hip[0]) * width / scale, (p.y - hip[1]) * height / scale, p.visibility)
    )
    return PoseSample(features, (shoulder[1] + hip[1]) / 2, dy / torso_length)
