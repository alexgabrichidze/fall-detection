"""Finite, explicitly simulated smoke run of the shared detector contract."""

import argparse
import sys
from time import monotonic, sleep

from .config import ScaffoldConfig
from .contracts import Detector, PoseStatus
from .device import ConsoleRenderer
from .fake_detector import FakeDetector


def consume_pending(detector: Detector, renderer: ConsoleRenderer) -> int:
    """Consume events separately from preview reads; never print frame data."""
    count = 0
    # Bound work per tick so a busy producer cannot starve controls/countdowns.
    for _ in range(32):
        event = detector.poll_event()
        if event is None:
            break
        renderer.event(event)
        count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detector", choices=("fake", "live"), required=True)
    parser.add_argument(
        "--scenario", choices=("possible-fall", "idle", "camera-failure", "pose-missing"),
        default="possible-fall",
    )
    parser.add_argument("--duration", type=float, default=1.0)
    parser.add_argument("--event-after", type=float, default=0.1)
    args = parser.parse_args(argv)
    if args.detector == "live":
        parser.error("Live detection is not implemented; no simulated fallback is used.")
    try:
        config = ScaffoldConfig(args.duration, args.event_after)
    except ValueError as error:
        parser.error(str(error))

    print("SIMULATION ONLY: no camera, AI inference, audio, or notifications.", flush=True)
    print("Countdown and local controls are not implemented yet.", flush=True)
    fake = FakeDetector()
    detector: Detector = fake
    renderer = ConsoleRenderer(sys.stdout)
    start = monotonic()
    injected = False
    try:
        detector.start()
        renderer.preview(detector.latest_preview())
        while monotonic() - start < config.duration_seconds:
            if not injected and monotonic() - start >= config.event_after_seconds:
                if args.scenario == "possible-fall":
                    fake.emit_possible_fall()
                elif args.scenario == "camera-failure":
                    fake.fail_camera()
                elif args.scenario == "pose-missing":
                    fake.set_pose_status(PoseStatus.NOT_VISIBLE)
                injected = True
                renderer.preview(detector.latest_preview())
            consume_pending(detector, renderer)
            sleep(config.poll_interval_seconds)
    except KeyboardInterrupt:
        return 130
    finally:
        detector.stop()
        renderer.preview(detector.latest_preview())
    return 0

