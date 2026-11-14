"""Application configuration owned by the application integrator."""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class ScaffoldConfig:
    duration_seconds: float = 1.0
    event_after_seconds: float = 0.1
    poll_interval_seconds: float = 0.01

    def __post_init__(self) -> None:
        if not isfinite(self.duration_seconds) or self.duration_seconds <= 0:
            raise ValueError("duration must be finite and positive")
        if not isfinite(self.event_after_seconds) or not (
            0 <= self.event_after_seconds < self.duration_seconds
        ):
            raise ValueError("event-after must be nonnegative and less than duration")
        if not isfinite(self.poll_interval_seconds) or self.poll_interval_seconds <= 0:
            raise ValueError("poll interval must be finite and positive")

