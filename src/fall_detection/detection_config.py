"""Camera settings and thresholds for the experimental fall detector."""

from pathlib import Path

DEFAULT_MODEL = Path("models/fall_detection_transformer.tflite")
MODEL_SHA256 = "c66659c3ac6d37c60469bee4d6f88cb2ac4976928bde6d46b19e7f851e8980b0"
SEQUENCE_LENGTH = 30
FEATURE_COUNT = 51
POSE_VISIBILITY = 0.3

CAMERA_FPS = 30
MIN_SAMPLE_INTERVAL = 0.85 / CAMERA_FPS  # Tolerate ordinary camera jitter.
MAX_SAMPLE_GAP = 0.2
MAX_SEQUENCE_SECONDS = 2.0

FALL_SCORE = 0.90
RECOVERY_SCORE = 0.50
CONFIRM_SECONDS = 0.18
MOTION_SECONDS = 2.5
UPRIGHT_COSINE = 0.86
LEANED_COSINE = 0.80
TORSO_DROP = 0.10  # Fractions of the frame height.
LARGE_TORSO_DROP = 0.22
EVENT_CAPACITY = 16
