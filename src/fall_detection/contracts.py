"""Shared detector contract, version 1. No camera, AI, audio, or network imports.

All timestamps use time.monotonic() in seconds within the same process.
Preview frames use immutable, tightly packed uint8 BGR bytes, in HWC order.
Landmarks retain native MediaPipe indices and normalized image coordinates;
conversion to the classifier's feature layout belongs inside detection.

Frames and landmarks are local preview data. Never serialize or log previews.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Protocol, runtime_checkable

CONTRACT_VERSION = 1


class DetectionSource(StrEnum):
    SIMULATED = "simulated"
    LIVE = "live"


class CameraStatus(StrEnum):
    STARTING = "starting"
    AVAILABLE = "available"
    FAILED = "failed"
    PAUSED = "paused"
    STOPPED = "stopped"


class PoseStatus(StrEnum):
    UNKNOWN = "unknown"
    VALID = "valid"
    NOT_VISIBLE = "not_visible"
    UNRELIABLE = "unreliable"


class EventKind(StrEnum):
    POSSIBLE_FALL = "possible_fall"
    FAULT = "fault"


class FaultCode(StrEnum):
    CAMERA_FAILURE = "camera_failure"
    MODEL_FAILURE = "model_failure"
    EVENT_QUEUE_OVERFLOW = "event_queue_overflow"
    WORKER_FAILURE = "worker_failure"


def _check_score(score: float | None) -> None:
    if score is not None and (not isfinite(score) or not 0 <= score <= 1):
        raise ValueError("model_score must be None or a finite value from 0 to 1")


@dataclass(frozen=True, slots=True)
class BgrFrame:
    width: int
    height: int
    data: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("frame dimensions must be positive")
        if not isinstance(self.data, bytes):
            raise TypeError("frame data must be immutable bytes")
        if len(self.data) != self.width * self.height * 3:
            raise ValueError("expected tightly packed uint8 BGR frame data")


@dataclass(frozen=True, slots=True)
class Landmark:
    index: int
    x: float
    y: float
    visibility: float


@dataclass(frozen=True, slots=True)
class DetectionEvent:
    event_id: str
    timestamp: float
    source: DetectionSource
    kind: EventKind
    model_score: float | None = None
    fault_code: FaultCode | None = None

    def __post_init__(self) -> None:
        if not self.event_id or not isfinite(self.timestamp):
            raise ValueError("events require an ID and finite monotonic timestamp")
        _check_score(self.model_score)
        if (self.kind == EventKind.FAULT) != (self.fault_code is not None):
            raise ValueError("only fault events must carry a fault_code")


@dataclass(frozen=True, slots=True)
class Preview:
    timestamp: float
    source: DetectionSource
    camera_status: CameraStatus
    pose_status: PoseStatus = PoseStatus.UNKNOWN
    model_ready: bool = False
    model_score: float | None = None
    frame: BgrFrame | None = field(default=None, repr=False)
    landmarks: tuple[Landmark, ...] = field(default=(), repr=False)
    fault_code: FaultCode | None = None

    def __post_init__(self) -> None:
        if not isfinite(self.timestamp):
            raise ValueError("preview timestamp must be finite")
        _check_score(self.model_score)
        if not self.model_ready and self.model_score is not None:
            raise ValueError("an unready model cannot report a current score")


@runtime_checkable
class Detector(Protocol):
    """Nonblocking, thread-safe boundary between detection and the application.

    start/stop are idempotent; a new start after stop begins a fresh session.
    set_paused clears stale detection history, never an application's incident.
    poll_event consumes one FIFO event, or None. Overflow faults may take
    priority; overflow must be explicit and must never silently evict events.
    latest_preview returns a replaceable immutable snapshot without consuming
    events. A missing score means unknown, never a confirmed non-fall.
    stop releases resources; queued events remain readable until a new start.
    """

    @property
    def source(self) -> DetectionSource: ...

    def start(self) -> None: ...

    def set_paused(self, paused: bool) -> None: ...

    def poll_event(self) -> DetectionEvent | None: ...

    def latest_preview(self) -> Preview: ...

    def stop(self) -> None: ...

