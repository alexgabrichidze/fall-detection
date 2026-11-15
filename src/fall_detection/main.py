"""Local application integration. Fake mode never uses real notifications."""

import argparse
from collections import deque
from collections.abc import Callable
from math import isfinite
from pathlib import Path
import sys
from time import monotonic, sleep

from .audio import PROMPTS, PromptPlayer
from .clips import FallClipBuffer
from .config import ApplicationConfig, ScaffoldConfig
from .contracts import CameraStatus, Detector, DetectionSource, EventKind, PoseStatus, Preview
from .detection import create_live_detector
from .detection_config import DEFAULT_MODEL
from .device import Command, ConsoleRenderer, OpenCVDevice, View, begin_camera_access, request_camera_access
from .fake_detector import FakeDetector
from .notifier import (
    CaregiverAction, CaregiverReply, DeliveryStatus, FakeSender, NotificationWorker, TelegramSender,
)
from .safety import EffectKind, SafetyController, SafetyState
from .voice import DEFAULT_VOICE_MODEL, VoiceListener
from .telegram_link import create_telegram_link, telegram_config


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
                 clock: Callable[[], float] = monotonic, event_sink=None, voice=None,
                 clips_enabled: Callable[[], bool] | None = None):
        self.detector = detector
        self.notifier = notifier
        self.audio = audio
        self.voice = voice
        self.config = config
        self.clock = clock
        self.safety = SafetyController(config.countdown_seconds, clock=clock)
        self.running = False
        self.paused = False
        self.detector_error = None
        self.audio_status = "not requested"
        self._clips = FallClipBuffer() if clips_enabled else None
        self._clips_enabled = clips_enabled
        self._pending_prompts: deque[tuple[str, Path | None]] = deque()
        self._audio_incident: str | None = None
        self._caregiver: CaregiverAction | None = None
        self.quit = False
        self._event_sink = event_sink
        self._preview = Preview(clock(), detector.source, CameraStatus.STOPPED)

    def start(self):
        if self.running:
            return
        try:
            self.detector.start()
        except Exception:
            self.detector_error = "detector startup failed"
        else:
            self.running = True
            self.detector_error = None

    def stop(self):
        if not self.running:
            return
        try:
            self.detector.stop()
        except Exception:
            self.detector_error = "detector cleanup failed"
        else:
            self.running = False
            self.paused = False
            self.detector_error = None

    def _stop_response(self):
        self.audio.cancel()
        self._pending_prompts.clear()
        if self.voice:
            self.voice.cancel()

    def _cancel_incident(self):
        if self.safety.cancel():
            self._stop_response()
            if self._clips:
                self._clips.discard()

    def _rearm(self):
        if not self.safety.reset():
            return
        self._stop_response()
        self.notifier.watch(None)
        self._caregiver = None
        self._audio_incident = None
        self.audio_status = "not requested"
        if self._clips:
            self._clips.discard()
            self._clips.clear_recent()
        if self.running:
            try:
                self.detector.set_paused(True)
                if not self.paused:
                    self.detector.set_paused(False)
            except Exception:
                self.detector_error = "detector rearm failed"

    def step(self, commands: tuple[Command, ...] = ()) -> View:
        for command in commands:
            if command == Command.QUIT:
                self.quit = True
            elif command == Command.STOP:
                self.stop()
            elif command == Command.CANCEL:
                self._cancel_incident()
            elif command == Command.RESET:
                self._rearm()
            elif command == Command.PAUSE and self.running:
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
        clips_enabled = self._clips_enabled() if self._clips_enabled else False
        try:
            # Events remain independent from the replaceable preview snapshot.
            for _ in range(32 if self.running else 0):
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
                elif self.safety.observe(event):
                    if self._caregiver is not None:
                        self._stop_response()
                        self._caregiver = None
                    if self._clips and clips_enabled:
                        self._clips.mark(event.timestamp)
            preview = self.detector.latest_preview()
            if preview.source != self.detector.source:
                self.detector_error = "preview source mismatch"
            else:
                self._preview = preview
        except Exception:
            self.detector_error = "detector worker unavailable"
        if self._clips:
            if clips_enabled and self.running and not self.paused and not self.detector_error and self._preview.frame:
                self._clips.observe(self._preview.frame, self._preview.timestamp)
            else:
                self._clips.clear_recent()
                if not clips_enabled:
                    self._clips.discard()
        if self.voice:
            response = self.voice.poll()
            if (response and self.safety.incident
                    and response.incident_id == self.safety.incident.incident_id):
                self._cancel_incident()
        # Faults above never suppress the active incident's clock.
        for effect in self.safety.tick():
            incident_id = effect.incident.incident_id
            if effect.kind == EffectKind.PROMPT:
                self._pending_prompts.append((incident_id, None))
            elif effect.kind == EffectKind.REMINDER:
                if self.voice:
                    self.voice.cancel()
                self._pending_prompts.append((incident_id, PROMPTS / "ten-seconds-left.wav"))
            elif effect.kind == EffectKind.ALERT:
                self._stop_response()
                frames = self._clips.take() if self._clips else ()
                submitted = self.notifier.submit(effect.incident, frames) if frames else self.notifier.submit(effect.incident)
                if not submitted:
                    self.safety.delivery_result(incident_id, DeliveryStatus.FAILED.value)
        result = self.notifier.poll()
        if result and self.safety.delivery_result(result.incident_id, result.status.value):
            if result.status == DeliveryStatus.ACCEPTED:
                self._pending_prompts.append((result.incident_id, PROMPTS / "caregiver-notified.wav"))
            if result.status in (DeliveryStatus.ACCEPTED, DeliveryStatus.UNCERTAIN):
                self.notifier.watch(result.incident_id)
        reply = self.notifier.poll_reply()
        if (isinstance(reply, CaregiverReply) and self.safety.state == SafetyState.ALERTING
                and reply.incident_id == self.safety.incident.incident_id
                and self.safety.delivery in (DeliveryStatus.ACCEPTED.value, DeliveryStatus.UNCERTAIN.value)):
            if reply.action == CaregiverAction.FALSE_ALARM:
                self._rearm()
                prompt = "caregiver-false-alarm.wav"
            else:
                prompt = "caregiver-help-on-the-way.wav"
            self._caregiver = reply.action
            self._pending_prompts.append((reply.incident_id, PROMPTS / prompt))
        audio_result = self.audio.poll()
        if audio_result and audio_result.incident_id == self._audio_incident:
            self.audio_status = audio_result.status
            if (self.voice and not self._pending_prompts and audio_result.status == "played"
                    and self.safety.incident and audio_result.incident_id == self.safety.incident.incident_id
                    and self.safety.state == SafetyState.AWAITING_RESPONSE):
                self.voice.start(audio_result.incident_id, self.safety.incident.deadline)
        # Speaker cues wait until the microphone worker has released input.
        if self._pending_prompts and (not self.voice or self.voice.idle):
            incident_id, path = self._pending_prompts[0]
            playing = self.audio.play(incident_id, path) if path else self.audio.play(incident_id)
            if playing:
                self._pending_prompts.popleft()
                self._audio_incident = incident_id
                self.audio_status = "playing"
            elif self.audio_status != "playing":
                self.audio_status = "error: audio worker unavailable"
        return self.view()

    def view(self) -> View:
        error = self.detector_error
        if error is None and self.running and self.detector.source == DetectionSource.LIVE and not self.paused:
            age = self.clock() - self._preview.timestamp
            if not 0 <= age <= self.config.preview_timeout_seconds:
                error = "preview unavailable or stale"
        voice_status, listening = self.voice.status if self.voice else ("disabled", False)
        if (self.voice and self.audio_status == "playing" and not listening
                and self.safety.state == SafetyState.AWAITING_RESPONSE):
            voice_status = "waiting for prompt"
        elif (self.voice and self.audio_status.startswith("error")
              and self.safety.state == SafetyState.AWAITING_RESPONSE):
            voice_status = "unavailable: prompt playback failed"
        return View(self._preview, self.safety.state, self.safety.remaining(), self.safety.delivery,
                    self.audio_status, self.paused, error,
                    self.safety.incident.incident_id if self.safety.incident else None, self.running,
                    voice_status, listening, self._caregiver)

    def close(self) -> tuple[str, ...]:
        errors = []
        try:
            self.detector.stop()
        except Exception:
            errors.append("detector cleanup failed")
        if self.voice and not self.voice.close():
            errors.append("voice worker still stopping")
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
    display = parser.add_mutually_exclusive_group()
    display.add_argument("--ui", action="store_true", help="open the control window (default in live mode)")
    display.add_argument("--web", action="store_true", help="serve the browser monitor on this computer")
    display.add_argument("--headless", action="store_true", help="run in the console without interactive controls")
    parser.add_argument("--port", type=int, default=8765, help="local browser monitor port (default 8765)")
    parser.add_argument("--camera", type=int, default=0, help="webcam index for live mode (default 0)")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL, help="path to the pinned fall classifier")
    parser.add_argument("--scenario", choices=("possible-fall", "idle", "camera-failure", "pose-missing", "fall-camera-failure"), default="possible-fall")
    parser.add_argument("--duration", type=float, help="seconds to run (window stays open by default)")
    parser.add_argument("--event-after", type=float, default=0.1)
    parser.add_argument("--countdown", type=float, default=20)
    parser.add_argument("--prompt", type=Path, help="prerecorded 16-bit PCM WAV, at most 10 seconds")
    parser.add_argument("--voice", action="store_true", help="enable offline I'm okay cancellation after the prompt")
    parser.add_argument("--voice-model", type=Path, default=DEFAULT_VOICE_MODEL, help="local Vosk English model directory")
    parser.add_argument("--microphone", type=int, help="sounddevice input index (default: system microphone)")
    parser.add_argument("--notification-outcome", choices=("simulated_accepted", "failed", "uncertain"), default="simulated_accepted")
    parser.add_argument("--telegram", action="store_true", help="enable real Telegram alerts in the live window")
    parser.add_argument("--test-telegram", action="store_true", help="send one labelled test using environment credentials")
    args = parser.parse_args(argv)
    if args.test_telegram:
        if args.detector or args.ui or args.web or args.headless or args.telegram:
            parser.error("--test-telegram is a standalone setup command")
        try:
            sender = TelegramSender(telegram_config())
        except ValueError as error:
            parser.error(str(error))
        status = sender.send_test()
        print(f"Telegram test: {status.value}")
        return 0 if status == DeliveryStatus.ACCEPTED else 1
    if args.detector is None:
        parser.error("--detector is required unless using --test-telegram")
    live = args.detector == "live"
    if args.voice and not live:
        parser.error("--voice requires --detector live; simulations never capture microphone audio")
    if args.microphone is not None and (not args.voice or args.microphone < 0):
        parser.error("--microphone requires --voice and a nonnegative input index")
    use_ui = args.ui or (live and not (args.headless or args.web))
    if args.telegram and not (live and use_ui):
        parser.error("--telegram is for the native live window. Link a chat in the page when using --web.")
    if args.web and not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    duration = args.duration
    if duration is None and not (use_ui or args.web):
        duration = 10.0 if live else 1.0
    if duration is not None and (not isfinite(duration) or duration <= 0):
        parser.error("duration must be finite and positive")
    try:
        config = ApplicationConfig(countdown_seconds=args.countdown)
        if live:
            if args.camera < 0:
                raise ValueError("camera index must be nonnegative")
            detector = create_live_detector(args.camera, args.model)
        else:
            timing = ScaffoldConfig(duration if duration is not None else max(1.0, args.event_after + 1), args.event_after)
            detector = FakeDetector()
        telegram = create_telegram_link() if args.web and live else None
        receive = None
        clips_enabled = None
        if telegram:
            sender = telegram.notify
            receive = telegram.poll_reply
            clips_enabled = lambda: telegram.status()["linked"]
        elif args.telegram:
            sender = TelegramSender(telegram_config(), replies=True)
            receive = sender.poll_reply
            clips_enabled = lambda: True
        else:
            sender = FakeSender(DeliveryStatus(args.notification_outcome))
    except ValueError as error:
        parser.error(str(error))
    prompt = args.prompt
    if prompt is None and (live or args.web):
        prompt = PROMPTS / ("fall-warning-20.wav" if args.countdown == 20 else "fall-warning.wav")
    renderer = None
    app = None
    try:
        if live and not args.web:
            try:
                camera_allowed = request_camera_access()
            except ImportError:
                print("Camera permission support is missing. Run python -m pip install -e '.[detection,audio]'.",
                      file=sys.stderr)
                return 2
            if not camera_allowed:
                print("Camera access is off. Open System Settings > Privacy & Security > Camera, "
                      "allow Terminal or the app you launched from, then run again.", file=sys.stderr)
                return 2
        if args.web:
            from .web_device import WebDevice

            try:
                renderer = WebDevice(
                    args.port, live=live, countdown=args.countdown, telegram=telegram,
                    camera_index=(lambda: detector.camera_index) if live else None,
                    select_camera=detector.select_camera if live else None,
                )
            except OSError:
                print(f"Could not open port {args.port}. Choose another port with --port.", file=sys.stderr)
                return 2
            print(f"Open Fall Monitor at {renderer.url}", flush=True)
        else:
            try:
                renderer = OpenCVDevice(telegram_enabled=args.telegram) if use_ui else ConsoleRenderer(sys.stdout)
            except Exception:
                print("UI unavailable. Install the detection or ui extra and run from a desktop session.", file=sys.stderr)
                return 2
            if live:
                notification_mode = "enabled" if args.telegram else "disabled (dry run)"
                print(f"LIVE webcam detection. Telegram {notification_mode}.", flush=True)
            else:
                print("SIMULATION ONLY: no camera, AI inference, microphone, or real notifications.", flush=True)
            print("Local prompt audio enabled." if prompt else "Audio prompt missing; countdown and cancellation remain available.", flush=True)
        app = Application(detector, NotificationWorker(sender, receive), PromptPlayer(prompt), config=config,
                          event_sink=renderer.event if isinstance(renderer, ConsoleRenderer) else None,
                          voice=VoiceListener(args.voice_model, args.microphone) if args.voice else None,
                          clips_enabled=clips_enabled)
        if not (args.web and live):
            app.start()
        # Map the window before polling visibility or keyboard/mouse events.
        renderer.render(app.step())
        start = monotonic()
        injected = False
        camera_failed = False
        permission_check = None
        while not app.quit and (duration is None or monotonic() - start < duration):
            if isinstance(detector, FakeDetector):
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
            commands = renderer.poll_commands()
            if args.web and live:
                if Command.START in commands and permission_check is None and (not app.running or app.view().detector_error):
                    app.stop()
                    app.detector_error = None
                    try:
                        permission_check = begin_camera_access()
                    except ImportError:
                        app.detector_error = "camera_permission_dependency"
                if permission_check is not None:
                    allowed = permission_check()
                    if allowed is not None:
                        permission_check = None
                        if allowed:
                            app.start()
                        else:
                            app.detector_error = "camera_permission_denied"
                renderer.permission_pending = permission_check is not None
            renderer.render(app.step(commands))
            sleep(config.poll_interval_seconds)
    except KeyboardInterrupt:
        return 130
    finally:
        if app:
            for message in app.close():
                print(message, file=sys.stderr)
            if live and not detector.wait_stopped():
                print("Camera worker still stopping.", file=sys.stderr)
            if isinstance(renderer, ConsoleRenderer):
                renderer.preview(app.detector.latest_preview())
        if renderer:
            renderer.close()
    return 2 if live and app.detector_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
