"""Local controls and rendering. HighGUI calls belong to the main thread."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
import os
import sys
from typing import TextIO

from .contracts import DetectionEvent, DetectionSource, PoseStatus, Preview
from .safety import SafetyState


class Command(StrEnum):
    CANCEL = "cancel"
    PAUSE = "pause"
    RESET = "reset"
    QUIT = "quit"
    SIMULATE_FALL = "simulate_fall"
    SIMULATE_CAMERA_FAILURE = "simulate_camera_failure"


@dataclass(frozen=True, slots=True)
class View:
    preview: Preview
    state: SafetyState
    remaining: float | None
    delivery: str
    audio: str
    paused: bool
    detector_error: str | None
    incident_id: str | None


def status_lines(view: View) -> tuple[str, ...]:
    preview = view.preview
    source = "SIMULATION ONLY - no camera/model or real alerts" if preview.source == DetectionSource.SIMULATED else "LIVE - experimental prototype"
    countdown = "" if view.remaining is None else f" | {view.remaining:.1f}s remaining"
    score = "unknown" if preview.model_score is None else f"{preview.model_score:.3f} (not calibrated confidence)"
    fault = view.detector_error or (preview.fault_code.value if preview.fault_code else "none")
    return (
        source,
        f"Monitoring: {'PAUSED' if view.paused else 'enabled'} | detector fault: {fault}",
        f"Camera: {preview.camera_status.value} | pose: {preview.pose_status.value} | model ready: {preview.model_ready}",
        f"Incident: {view.state.value}{countdown}",
        f"Delivery: {view.delivery}",
        f"Audio: {view.audio}",
        f"Model score: {score}",
        "C/Space: cancel | P: pause/resume | R: reset/rearm | Q/Esc: quit",
        "Simulation keys: F fall, X camera failure" if preview.source == DetectionSource.SIMULATED else "Falls outside camera view cannot be detected.",
    )


class ConsoleRenderer:
    def __init__(self, output: TextIO):
        self._output = output
        self._last_status = None

    def preview(self, preview: Preview):
        print(
            f"[{preview.source.value.upper()}] camera={preview.camera_status.value} "
            f"pose={preview.pose_status.value} model_ready={preview.model_ready} "
            f"fault={preview.fault_code.value if preview.fault_code else 'none'}",
            file=self._output, flush=True,
        )

    def event(self, event: DetectionEvent):
        print(
            f"[{event.source.value.upper()}] consumed event={event.event_id} "
            f"kind={event.kind.value} score={event.model_score} "
            f"fault={event.fault_code.value if event.fault_code else 'none'}",
            file=self._output, flush=True,
        )

    def render(self, view: View):
        # Print transitions, not every video frame or countdown tick.
        status = (view.state, view.delivery, view.audio, view.paused, view.detector_error,
                  view.preview.camera_status, view.preview.pose_status, view.preview.fault_code)
        if status != self._last_status:
            self.preview(view.preview)
            print(f"[{view.preview.source.value.upper()}] incident={view.state.value} "
                  f"delivery={view.delivery} audio={view.audio} paused={view.paused} "
                  f"detector_error={view.detector_error or 'none'}", file=self._output, flush=True)
            self._last_status = status

    def poll_commands(self) -> tuple[Command, ...]:
        return ()

    def close(self):
        pass


# Native MediaPipe indices: torso and limbs. No MediaPipe import in the UI.
_CONNECTIONS = ((11, 12), (11, 13), (13, 15), (12, 14), (14, 16),
                (11, 23), (12, 24), (23, 24), (23, 25), (25, 27),
                (24, 26), (26, 28), (27, 29), (29, 31), (28, 30), (30, 32))
_BUTTONS = ((20, 660, 300, 710, Command.CANCEL, "I'm okay / Cancel"),
            (320, 660, 620, 710, Command.PAUSE, "Pause / Resume"),
            (640, 660, 940, 710, Command.RESET, "Reset / Rearm"))


class OpenCVDevice:
    title = "Fall detection prototype"

    def __init__(self):
        if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise RuntimeError("No desktop display; use console mode without --ui")
        import cv2
        import numpy as np
        self.cv = cv2
        self.np = np
        self._commands = []
        cv2.namedWindow(self.title, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(self.title, self._mouse)

    def _mouse(self, event, x, y, flags, userdata):
        if event == self.cv.EVENT_LBUTTONUP:
            for left, top, right, bottom, command, _ in _BUTTONS:
                if left <= x <= right and top <= y <= bottom:
                    self._commands.append(command)

    def compose(self, view: View):
        """Build one in-memory image. Never save previews or landmarks."""
        cv, np = self.cv, self.np
        canvas = np.zeros((720, 960, 3), dtype=np.uint8)
        preview = view.preview
        if preview.frame is not None:
            frame = preview.frame
            image = np.frombuffer(frame.data, dtype=np.uint8).reshape(frame.height, frame.width, 3)
            scale = min(960 / frame.width, 420 / frame.height)
            width, height = max(1, round(frame.width * scale)), max(1, round(frame.height * scale))
            image = cv.resize(image, (width, height))
            left, top = (960 - width) // 2, (420 - height) // 2
            canvas[top:top + height, left:left + width] = image
            if preview.pose_status == PoseStatus.VALID:
                points = {
                    landmark.index: (left + int(landmark.x * (width - 1)), top + int(landmark.y * (height - 1)))
                    for landmark in preview.landmarks
                    if isfinite(landmark.x) and isfinite(landmark.y) and landmark.visibility >= 0.5
                    and 0 <= landmark.x <= 1 and 0 <= landmark.y <= 1
                }
                for start, end in _CONNECTIONS:
                    if start in points and end in points:
                        cv.line(canvas, points[start], points[end], (80, 220, 80), 2)
                for point in points.values():
                    cv.circle(canvas, point, 3, (80, 220, 80), -1)
        else:
            cv.putText(canvas, "No camera preview" if preview.source == DetectionSource.LIVE else "SIMULATED - no camera opened",
                       (220, 190), cv.FONT_HERSHEY_SIMPLEX, 0.8, (180, 180, 180), 2)
        for index, line in enumerate(status_lines(view)):
            color = (80, 180, 255) if index in (0, 3, 4, 5) else (225, 225, 225)
            cv.putText(canvas, line, (20, 440 + 25 * index), cv.FONT_HERSHEY_SIMPLEX, 0.52, color, 1)
        for left, top, right, bottom, _, label in _BUTTONS:
            cv.rectangle(canvas, (left, top), (right, bottom), (75, 75, 75), -1)
            cv.putText(canvas, label, (left + 15, top + 31), cv.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        return canvas

    def render(self, view: View):
        self.cv.imshow(self.title, self.compose(view))

    def poll_commands(self) -> tuple[Command, ...]:
        key = self.cv.waitKey(1) & 0xFF
        mapping = {ord('c'): Command.CANCEL, 32: Command.CANCEL, ord('p'): Command.PAUSE,
                   ord('r'): Command.RESET, ord('q'): Command.QUIT, 27: Command.QUIT,
                   ord('f'): Command.SIMULATE_FALL, ord('x'): Command.SIMULATE_CAMERA_FAILURE}
        if key in mapping:
            self._commands.append(mapping[key])
        if self.cv.getWindowProperty(self.title, self.cv.WND_PROP_VISIBLE) < 1:
            self._commands.append(Command.QUIT)
        commands = tuple(self._commands)
        self._commands.clear()
        return commands

    def close(self):
        self.cv.destroyAllWindows()
