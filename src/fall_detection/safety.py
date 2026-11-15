"""Clock-driven incident logic. No device, network, or model dependencies."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from time import monotonic
from uuid import uuid4

from .contracts import DetectionEvent, DetectionSource, EventKind


class SafetyState(StrEnum):
    MONITORING = "monitoring"
    SUSPECTED_FALL = "suspected_fall"
    AWAITING_RESPONSE = "awaiting_response"
    ALERTING = "alerting"
    CANCELLED = "cancelled"


class EffectKind(StrEnum):
    PROMPT = "prompt"
    REMINDER = "reminder"
    ALERT = "alert"


@dataclass(frozen=True, slots=True)
class Incident:
    incident_id: str
    source: DetectionSource
    started_at: float
    deadline: float


@dataclass(frozen=True, slots=True)
class Effect:
    kind: EffectKind
    incident: Incident


class SafetyController:
    """One incident at a time; explicit reset required after cancellation/alert.

    Detection faults and pause do not affect an existing incident. Call tick
    regularly even when detection is unavailable. Controls should be processed
    before tick; cancellation at or after the deadline cannot retract an alert.
    Reset establishes a timestamp barrier against queued, pre-reset evidence.
    """

    def __init__(self, countdown_seconds: float = 20, *, clock: Callable[[], float] = monotonic):
        if not isfinite(countdown_seconds) or countdown_seconds <= 0:
            raise ValueError("countdown must be finite and positive")
        self._clock = clock
        self._countdown = countdown_seconds
        self._armed_after = float("-inf")
        self._reminder_due = False
        self.state = SafetyState.MONITORING
        self.incident: Incident | None = None
        self.delivery = "none"

    def observe(self, event: DetectionEvent) -> bool:
        if (event.kind != EventKind.POSSIBLE_FALL
                or self.state != SafetyState.MONITORING
                or event.timestamp <= self._armed_after):
            return False
        now = self._clock()
        # A detector using another clock domain is not usable evidence.
        if event.timestamp > now:
            return False
        self.incident = Incident(str(uuid4()), event.source, now, now + self._countdown)
        self.state = SafetyState.SUSPECTED_FALL
        self._reminder_due = self._countdown > 10
        self.delivery = "none"
        return True

    def tick(self) -> tuple[Effect, ...]:
        effects = []
        if self.state == SafetyState.SUSPECTED_FALL:
            self.state = SafetyState.AWAITING_RESPONSE
            effects.append(Effect(EffectKind.PROMPT, self.incident))
        if self.state == SafetyState.AWAITING_RESPONSE:
            remaining = self.incident.deadline - self._clock()
            if remaining <= 0:
                self.state = SafetyState.ALERTING
                self.delivery = "pending"
                effects.append(Effect(EffectKind.ALERT, self.incident))
            elif self._reminder_due and remaining <= 10:
                self._reminder_due = False
                effects.append(Effect(EffectKind.REMINDER, self.incident))
        return tuple(effects)

    def remaining(self) -> float | None:
        if self.state not in (SafetyState.SUSPECTED_FALL, SafetyState.AWAITING_RESPONSE):
            return None
        return max(0.0, self.incident.deadline - self._clock())

    def cancel(self) -> bool:
        if self.state not in (SafetyState.SUSPECTED_FALL, SafetyState.AWAITING_RESPONSE):
            return False
        if self._clock() >= self.incident.deadline:
            return False
        self.state = SafetyState.CANCELLED
        return True

    def delivery_result(self, incident_id: str, outcome: str) -> bool:
        if (self.state == SafetyState.ALERTING and self.incident.incident_id == incident_id
                and self.delivery == "pending"):
            self.delivery = outcome
            return True
        return False

    def reset(self) -> bool:
        if self.state == SafetyState.CANCELLED or (
            self.state == SafetyState.ALERTING and self.delivery != "pending"
        ):
            self._armed_after = self._clock()
            self.state = SafetyState.MONITORING
            self.incident = None
            self.delivery = "none"
            return True
        return False
