"""Webcam capture, pose tracking, and fall classification on one worker."""

from collections import deque
from hashlib import sha256
from math import isfinite
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from uuid import uuid4

from . import detection_config as cfg
from .contracts import (
    BgrFrame, CameraStatus, DetectionEvent, DetectionSource, EventKind, FaultCode,
    Landmark, PoseStatus, Preview,
)
from .preprocessing import PoseSample, prepare_pose


class FallClassifier:
    def __init__(self, checkpoint: bytes) -> None:
        import numpy as np
        from ai_edge_litert.interpreter import Interpreter

        self._np = np
        self._interpreter = Interpreter(model_content=checkpoint, num_threads=1)
        self._interpreter.allocate_tensors()
        self._input, = self._interpreter.get_input_details()
        self._output, = self._interpreter.get_output_details()
        if (tuple(self._input["shape"]) != (1, cfg.SEQUENCE_LENGTH, cfg.FEATURE_COUNT)
                or tuple(self._output["shape"]) != (1, 1)
                or self._input["dtype"] != np.float32 or self._output["dtype"] != np.float32):
            raise ValueError("unexpected fall classifier tensors")

    def score(self, sequence: deque[tuple[float, tuple[float, ...]]]) -> float:
        tensor = self._np.asarray([features for _, features in sequence], dtype=self._np.float32)
        self._interpreter.set_tensor(self._input["index"], tensor[None, :, :])
        self._interpreter.invoke()
        score = float(self._interpreter.get_tensor(self._output["index"])[0, 0])
        if not isfinite(score) or not 0 <= score <= 1:
            raise ValueError("invalid fall classifier score")
        return score


class FallWindow:
    def __init__(self, classifier: FallClassifier) -> None:
        self._classifier = classifier
        self._sequence: deque[tuple[float, tuple[float, ...]]] = deque(maxlen=cfg.SEQUENCE_LENGTH)
        self._upright: deque[tuple[float, float]] = deque()
        self._candidate_since: float | None = None
        self._reported = False

    def clear(self) -> None:
        self._sequence.clear()
        self._upright.clear()
        self._candidate_since = None

    def observe(self, now: float, sample: PoseSample | None) -> tuple[float | None, bool]:
        if self._sequence and now - self._sequence[-1][0] > cfg.MAX_SAMPLE_GAP:
            self.clear()
        if sample is None:
            self.clear()
            return None, False
        self._sequence.append((now, sample.features))
        while self._upright and now - self._upright[0][0] > cfg.MOTION_SECONDS:
            self._upright.popleft()
        if sample.upright >= cfg.UPRIGHT_COSINE:
            self._upright.append((now, sample.torso_y))
        if len(self._sequence) < cfg.SEQUENCE_LENGTH or now - self._sequence[0][0] > cfg.MAX_SEQUENCE_SECONDS:
            self._candidate_since = None
            return None, False

        score = self._classifier.score(self._sequence)
        if score < cfg.RECOVERY_SCORE and sample.upright >= cfg.UPRIGHT_COSINE:
            self._reported = False
        baseline = min((y for _, y in self._upright), default=sample.torso_y)
        drop = sample.torso_y - baseline
        low = drop >= cfg.TORSO_DROP and (sample.upright < cfg.LEANED_COSINE or drop >= cfg.LARGE_TORSO_DROP)
        if self._reported or score < cfg.FALL_SCORE or not low:
            self._candidate_since = None
            return score, False
        if self._candidate_since is None:
            self._candidate_since = now
        if now - self._candidate_since < cfg.CONFIRM_SECONDS:
            return score, False
        self._reported = True
        return score, True


class LiveDetector:
    source = DetectionSource.LIVE

    def __init__(self, camera: int, checkpoint: bytes) -> None:
        self._camera = camera
        self._checkpoint = checkpoint
        self._lock = Lock()
        self._session: Event | None = None
        self._thread: Thread | None = None
        self._revision = 0
        self._paused = False
        self._events: deque[DetectionEvent] = deque()
        self._overflow: DetectionEvent | None = None
        self._preview = Preview(monotonic(), self.source, CameraStatus.STOPPED)

    def start(self) -> None:
        with self._lock:
            if self._session is not None:
                return
            self._session = Event()
            self._events.clear()
            self._overflow = None
            self._paused = False
            self._preview = Preview(monotonic(), self.source, CameraStatus.STARTING)
            if self._thread is None:
                thread = Thread(target=self._run, name="fall-detector", daemon=True)
                thread.start()
                self._thread = thread

    def stop(self) -> None:
        with self._lock:
            if self._session is not None:
                self._session.set()
                self._session = None
                self._preview = Preview(monotonic(), self.source, CameraStatus.STOPPED)

    def wait_stopped(self, timeout: float = 0.5) -> bool:
        """Give the worker time to release the camera after the UI exits."""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return thread is None or not thread.is_alive()

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            if self._session is None:
                raise RuntimeError("start the detector before changing pause state")
            self._paused = paused
            self._revision += 1
            camera = self._preview.camera_status
            fault = self._preview.fault_code
            if fault is None:
                camera = CameraStatus.PAUSED if paused else CameraStatus.STARTING
            self._preview = Preview(monotonic(), self.source, camera, fault_code=fault)

    def latest_preview(self) -> Preview:
        with self._lock:
            return self._preview

    def poll_event(self) -> DetectionEvent | None:
        with self._lock:
            if self._overflow is not None:
                event, self._overflow = self._overflow, None
                return event
            return self._events.popleft() if self._events else None

    def _run(self) -> None:
        # A rapid stop/start may request a new session while the old camera
        # is releasing. Reuse the same worker so two sessions never overlap.
        while True:
            with self._lock:
                session = self._session
                if session is None:
                    self._thread = None
                    return
            try:
                self._capture(session)
            except Exception:
                self._fault(session, FaultCode.WORKER_FAILURE)
            with self._lock:
                if self._session is session or self._session is None:
                    self._thread = None
                    return

    def _capture(self, session: Event) -> None:
        import cv2

        try:
            import mediapipe as mp

            classifier = FallClassifier(self._checkpoint)
            pose = mp.solutions.pose.Pose(model_complexity=1)
        except Exception:
            self._fault(session, FaultCode.MODEL_FAILURE)
            return
        with pose:
            if session.is_set():
                return
            camera = None
            try:
                camera = cv2.VideoCapture(self._camera)
                if not camera.isOpened():
                    self._fault(session, FaultCode.CAMERA_FAILURE)
                    return
                camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                camera.set(cv2.CAP_PROP_FPS, cfg.CAMERA_FPS)
                self._monitor(session, camera, cv2, pose, classifier)
            except cv2.error:
                self._fault(session, FaultCode.CAMERA_FAILURE)
            finally:
                if camera is not None:
                    camera.release()

    def _monitor(self, session, camera, cv2, pose, classifier) -> None:
        revision = -1
        last_sample = float("-inf")
        while not session.is_set():
            with self._lock:
                current_revision, paused = self._revision, self._paused
            ok, image = camera.read()
            timestamp = monotonic()
            if not ok or image is None:
                self._fault(session, FaultCode.CAMERA_FAILURE)
                return
            if paused:
                continue
            if revision != current_revision:
                revision = current_revision
                window = FallWindow(classifier)
                last_sample = float("-inf")
            if timestamp - last_sample < cfg.MIN_SAMPLE_INTERVAL:
                continue
            last_sample = timestamp
            height, width = image.shape[:2]
            try:
                result = pose.process(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
                landmarks = tuple(
                    Landmark(index, point.x, point.y, point.visibility)
                    for index, point in enumerate(result.pose_landmarks.landmark)
                ) if result.pose_landmarks else ()
                sample = prepare_pose(landmarks, width, height) if landmarks else None
                score, fall = window.observe(timestamp, sample)
            except Exception:
                self._fault(session, FaultCode.MODEL_FAILURE)
                return
            status = PoseStatus.VALID if sample else (PoseStatus.UNRELIABLE if landmarks else PoseStatus.NOT_VISIBLE)
            preview = Preview(timestamp, self.source, CameraStatus.AVAILABLE, status,
                              score is not None, score, BgrFrame(width, height, image.tobytes()), landmarks)
            with self._lock:
                if self._session is not session or revision != self._revision:
                    continue
                self._preview = preview
                if fall and not self._enqueue(DetectionEvent(str(uuid4()), timestamp, self.source,
                                                            EventKind.POSSIBLE_FALL, model_score=score)):
                    return

    def _fault(self, session: Event, fault: FaultCode) -> None:
        with self._lock:
            if self._session is not session:
                return
            now = monotonic()
            camera = CameraStatus.FAILED if fault == FaultCode.CAMERA_FAILURE else CameraStatus.STOPPED
            self._preview = Preview(now, self.source, camera, fault_code=fault)
            self._enqueue(DetectionEvent(str(uuid4()), now, self.source, EventKind.FAULT, fault_code=fault))

    def _enqueue(self, event: DetectionEvent) -> bool:
        if len(self._events) < cfg.EVENT_CAPACITY:
            self._events.append(event)
            return True
        fault = FaultCode.EVENT_QUEUE_OVERFLOW
        self._overflow = DetectionEvent(str(uuid4()), event.timestamp, self.source, EventKind.FAULT, fault_code=fault)
        camera = CameraStatus.FAILED if self._preview.camera_status == CameraStatus.FAILED else CameraStatus.STOPPED
        self._preview = Preview(event.timestamp, self.source, camera, fault_code=fault)
        return False


def create_live_detector(camera: int = 0, model_path: Path = cfg.DEFAULT_MODEL) -> LiveDetector:
    try:
        checkpoint = model_path.read_bytes()
    except OSError as error:
        raise ValueError(f"cannot read fall model at {model_path}. See models/README.md or pass --model.") from error
    if sha256(checkpoint).hexdigest() != cfg.MODEL_SHA256:
        raise ValueError("fall model checksum mismatch. Download the pinned checkpoint from models/README.md.")
    return LiveDetector(camera, checkpoint)
