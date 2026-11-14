"""Explicitly simulated detector for application development; no I/O."""

from collections import deque
from collections.abc import Callable
from dataclasses import replace
from threading import RLock
from time import monotonic
from uuid import uuid4

from .contracts import (
    CameraStatus,
    DetectionEvent,
    DetectionSource,
    EventKind,
    FaultCode,
    PoseStatus,
    Preview,
)


class FakeDetector:
    def __init__(
        self, *, clock: Callable[[], float] = monotonic, event_capacity: int = 16
    ) -> None:
        if event_capacity < 1:
            raise ValueError("event_capacity must be positive")
        self._clock = clock
        self._capacity = event_capacity
        self._lock = RLock()
        self._events: deque[DetectionEvent] = deque()
        self._overflow: DetectionEvent | None = None
        self._running = False
        self._paused = False
        self._preview = Preview(clock(), self.source, CameraStatus.STOPPED)

    @property
    def source(self) -> DetectionSource:
        return DetectionSource.SIMULATED

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._events.clear()
            self._overflow = None
            self._running = True
            self._paused = False
            self._preview = Preview(
                self._clock(), self.source, CameraStatus.AVAILABLE
            )

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._paused = False
            self._preview = Preview(
                self._clock(), self.source, CameraStatus.STOPPED
            )

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            if not self._running:
                raise RuntimeError("start the detector before changing pause state")
            self._paused = paused
            camera = self._preview.camera_status
            # A pause must not hide a failed camera or clear a latched fault.
            if camera != CameraStatus.FAILED:
                camera = CameraStatus.PAUSED if paused else CameraStatus.AVAILABLE
            self._preview = Preview(
                self._clock(), self.source, camera,
                fault_code=self._preview.fault_code,
            )

    def poll_event(self) -> DetectionEvent | None:
        with self._lock:
            if self._overflow is not None:
                event, self._overflow = self._overflow, None
                return event
            return self._events.popleft() if self._events else None

    def latest_preview(self) -> Preview:
        with self._lock:
            return self._preview

    def emit_possible_fall(self, model_score: float = 0.9) -> None:
        """Test hook. This is never evidence from a camera or trained model."""
        with self._lock:
            self._require_available()
            event = DetectionEvent(
                str(uuid4()), self._clock(), self.source,
                EventKind.POSSIBLE_FALL, model_score=model_score,
            )
            self._preview = replace(
                self._preview, timestamp=event.timestamp,
                pose_status=PoseStatus.VALID,
                model_ready=True, model_score=model_score,
            )
            self._enqueue(event)

    def set_pose_status(self, status: PoseStatus) -> None:
        with self._lock:
            self._require_available()
            self._preview = replace(
                self._preview, timestamp=self._clock(), pose_status=status,
                model_ready=False, model_score=None,
            )

    def fail_camera(self) -> None:
        with self._lock:
            if not self._running:
                raise RuntimeError("start the detector before injecting a fault")
            self._preview = Preview(
                self._clock(), self.source, CameraStatus.FAILED,
                fault_code=FaultCode.CAMERA_FAILURE,
            )
            self._enqueue(DetectionEvent(
                str(uuid4()), self._clock(), self.source, EventKind.FAULT,
                fault_code=FaultCode.CAMERA_FAILURE,
            ))

    def _require_available(self) -> None:
        if not self._running or self._paused or self._preview.fault_code is not None:
            raise RuntimeError("detector must be running, unpaused, and fault-free")

    def _enqueue(self, event: DetectionEvent) -> None:
        if len(self._events) < self._capacity:
            self._events.append(event)
            return
        # Preserve queued events and reserve a separate slot for an overflow
        # fault. The detector stays faulted until explicitly stopped/restarted.
        if self._preview.fault_code != FaultCode.EVENT_QUEUE_OVERFLOW:
            self._overflow = DetectionEvent(
                str(uuid4()), self._clock(), self.source, EventKind.FAULT,
                fault_code=FaultCode.EVENT_QUEUE_OVERFLOW,
            )
        self._preview = replace(
            self._preview, model_ready=False, model_score=None,
            fault_code=FaultCode.EVENT_QUEUE_OVERFLOW,
        )

