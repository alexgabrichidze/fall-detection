"""Local application integration. Fake mode never uses real notifications."""

import argparse
from collections.abc import Callable
from pathlib import Path
import sys
from time import monotonic, sleep

from .audio import PromptPlayer
from .config import ApplicationConfig, ScaffoldConfig
from .contracts import CameraStatus, Detector, DetectionSource, EventKind, PoseStatus, Preview
from .device import Command, ConsoleRenderer, OpenCVDevice, View
from .fake_detector import FakeDetector
from .notifier import (
    DeliveryStatus, FakeSender, NotificationWorker, TelegramConfig, TelegramSender,
)
from .safety import EffectKind, SafetyController


def consume_pending(detector: Detector, renderer: ConsoleRenderer) -> int:
    """Scaffold helper retained for simple contract consumers."""
    count = 0
    for _ in range(32):
        event = detector.poll_event()
        if event is None:
            break
        renderer.event(event)
        count += 1
    return count


class Application:
    """One nonblocking application tick; devices and clocks are injectable.

    Detection stays behind its contract. Notification and prompt workers expose
    submit/poll operations; neither can hold up a countdown. No incident history.
    """

    def __init__(self, detector: Detector, notifier, audio, *, config=ApplicationConfig(),
                 clock: Callable[[], float] = monotonic, event_sink=None):
        self.detector = detector
        self.notifier = notifier
        self.audio = audio
        self.config = config
        self.clock = clock
        self.safety = SafetyController(config.countdown_seconds, clock=clock)
        self.paused = False
        self.detector_error = None
        self.audio_status = "not requested"
        self.quit = False
        self._event_sink = event_sink
        self._preview = Preview(clock(), detector.source, CameraStatus.STARTING)

    def start(self):
        try:
            self.detector.start()
        except Exception:
            self.detector_error = "detector startup failed"

    def step(self, commands: tuple[Command, ...] = ()) -> View:
        for command in commands:
            if command == Command.QUIT:
                self.quit = True
            elif command == Command.CANCEL:
                if self.safety.cancel():
                    self.audio.cancel()
            elif command == Command.RESET:
                if self.safety.reset():
                    self.audio.cancel()
                    self.audio_status = "not requested"
                    # Discard the detector's pre-reset movement history too.
                    # Already queued events are rejected by the reset timestamp.
                    try:
                        self.detector.set_paused(True)
                        if not self.paused:
                            self.detector.set_paused(False)
                    except Exception:
                        self.detector_error = "detector rearm failed"
            elif command == Command.PAUSE:
                try:
                    self.detector.set_paused(not self.paused)
                    self.paused = not self.paused
                except Exception:
                    self.detector_error = "pause/resume failed"
            elif isinstance(self.detector, FakeDetector):
                try:
                    if command == Command.SIMULATE_FALL:
                        self.detector.emit_possible_fall()
                    elif command == Command.SIMULATE_CAMERA_FAILURE:
                        self.detector.fail_camera()
                except RuntimeError:
                    # The fake correctly refuses fall injection while paused/faulted.
                    pass
        try:
            # Events remain independent from the replaceable preview snapshot.
            for _ in range(32):
                event = self.detector.poll_event()
                if event is None:
                    break
                if event.source != self.detector.source:
                    self.detector_error = "detector source mismatch"
                    continue
                if self._event_sink:
                    self._event_sink(event)
                if event.kind == EventKind.FAULT:
                    self.detector_error = event.fault_code.value
                else:
                    self.safety.observe(event)
            preview = self.detector.latest_preview()
            if preview.source != self.detector.source:
                self.detector_error = "preview source mismatch"
            else:
                self._preview = preview
            if self.detector.source == DetectionSource.LIVE and not self.paused:
                age = self.clock() - self._preview.timestamp
                if age < 0 or age > self.config.preview_timeout_seconds:
                    self.detector_error = "preview unavailable or stale"
        except Exception:
            self.detector_error = "detector worker unavailable"
        # Faults above never suppress the active incident's clock.
        for effect in self.safety.tick():
            if effect.kind == EffectKind.PROMPT:
                self.audio_status = "playing"
                if not self.audio.play(effect.incident.incident_id):
                    self.audio_status = "error: audio worker unavailable"
            elif effect.kind == EffectKind.ALERT:
                self.audio.cancel()
                if not self.notifier.submit(effect.incident):
                    self.safety.delivery_result(effect.incident.incident_id, DeliveryStatus.FAILED.value)
        result = self.notifier.poll()
        if result:
            self.safety.delivery_result(result.incident_id, result.status.value)
        audio_result = self.audio.poll()
        if audio_result and self.safety.incident and audio_result.incident_id == self.safety.incident.incident_id:
            self.audio_status = audio_result.status
        return self.view()

    def view(self) -> View:
        return View(self._preview, self.safety.state, self.safety.remaining(), self.safety.delivery,
                    self.audio_status, self.paused, self.detector_error,
                    self.safety.incident.incident_id if self.safety.incident else None)

    def close(self) -> tuple[str, ...]:
        errors = []
        try:
            self.detector.stop()
        except Exception:
            errors.append("detector cleanup failed")
        if not self.audio.close():
            errors.append("audio worker still stopping")
        if not self.notifier.close():
            errors.append("notification worker still stopping; delivery may be uncertain")
        if self.safety.delivery == "pending":
            errors.append("pending alert at exit; delivery uncertain, no automatic retry")
        if self.safety.remaining() is not None:
            errors.append("active countdown ended by application exit")
        return tuple(errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detector", choices=("fake", "live"))
    parser.add_argument("--ui", action="store_true", help="OpenCV controls; otherwise finite console run")
    parser.add_argument("--scenario", choices=("possible-fall", "idle", "camera-failure", "pose-missing", "fall-camera-failure"), default="possible-fall")
    parser.add_argument("--duration", type=float, help="seconds; default console 1, UI until quit")
    parser.add_argument("--event-after", type=float, default=0.1)
    parser.add_argument("--countdown", type=float, default=20)
    parser.add_argument("--prompt", type=Path, help="prerecorded 16-bit PCM WAV, at most 10 seconds")
    parser.add_argument("--notification-outcome", choices=("simulated_accepted", "failed", "uncertain"), default="simulated_accepted")
    parser.add_argument("--test-telegram", action="store_true", help="send one labelled test using environment credentials")
    args = parser.parse_args(argv)
    if args.test_telegram:
        if args.detector or args.ui:
            parser.error("--test-telegram is a standalone setup command")
        try:
            sender = TelegramSender(TelegramConfig.from_environment())
        except ValueError as error:
            parser.error(str(error))
        status = sender.send_test()
        print(f"Telegram test: {status.value}")
        return 0 if status == DeliveryStatus.ACCEPTED else 1
    if args.detector is None:
        parser.error("--detector is required unless using --test-telegram")
    if args.detector == "live":
        parser.error("Live detection is not implemented; no simulated fallback is used. Await A's verified detector integration.")
    duration = args.duration if args.duration is not None else (None if args.ui else 1.0)
    try:
        # Reuse scaffold validation for finite smoke runs and scenario timing.
        timing = ScaffoldConfig(duration if duration is not None else max(1.0, args.event_after + 1), args.event_after)
        config = ApplicationConfig(countdown_seconds=args.countdown)
    except ValueError as error:
        parser.error(str(error))
    renderer = None
    app = None
    try:
        try:
            renderer = OpenCVDevice() if args.ui else ConsoleRenderer(sys.stdout)
        except Exception:
            print("UI unavailable: install the ui extra and run on a desktop display.", file=sys.stderr)
            return 2
        print("SIMULATION ONLY: no camera, AI inference, microphone, or real notifications.", flush=True)
        print("Local prompt audio enabled." if args.prompt else "Audio prompt missing; countdown and cancellation remain available.", flush=True)
        detector = FakeDetector()
        app = Application(detector, NotificationWorker(FakeSender(DeliveryStatus(args.notification_outcome))),
                          PromptPlayer(args.prompt), config=config,
                          event_sink=renderer.event if isinstance(renderer, ConsoleRenderer) else None)
        app.start()
        # Map the window before polling visibility or keyboard/mouse events.
        renderer.render(app.step())
        start = monotonic()
        injected = False
        camera_failed = False
        while not app.quit and (duration is None or monotonic() - start < duration):
            elapsed = monotonic() - start
            if not injected and elapsed >= timing.event_after_seconds:
                if not app.paused and app.detector_error is None:
                    if args.scenario in ("possible-fall", "fall-camera-failure"):
                        detector.emit_possible_fall()
                    elif args.scenario == "camera-failure":
                        detector.fail_camera()
                    elif args.scenario == "pose-missing":
                        detector.set_pose_status(PoseStatus.NOT_VISIBLE)
                injected = True
            if args.scenario == "fall-camera-failure" and injected and not camera_failed and elapsed >= timing.event_after_seconds + 0.1:
                detector.fail_camera()
                camera_failed = True
            renderer.render(app.step(renderer.poll_commands()))
            sleep(config.poll_interval_seconds)
    except KeyboardInterrupt:
        return 130
    finally:
        if app:
            for message in app.close():
                print(message, file=sys.stderr)
            if isinstance(renderer, ConsoleRenderer):
                renderer.preview(app.detector.latest_preview())
        if renderer:
            renderer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
