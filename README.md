# Fall Detection Prototype

Computer-first, local fall detection project. Requirements are in
[AGENTS.md](AGENTS.md); ownership and tasks are in
[IMPLEMENTATION.md](IMPLEMENTATION.md).

## What works now

A shared Python interface, an in-memory fake detector, and a console application
that consumes simulated events. No camera opens, no model runs, no audio plays,
and no notifications are sent. Countdown logic and the OpenCV window are not
implemented yet. Live detection exits with an explicit error.

## Install and run

The model-free scaffold uses Python 3.11 or newer and the standard library.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
fall-detection --detector fake
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell. The module
entry point also works: `python -m fall_detection --detector fake`.

The default run emits one labelled simulated fall event and stops after one
second. Other scenarios:

```bash
fall-detection --detector fake --scenario idle
fall-detection --detector fake --scenario camera-failure
fall-detection --detector fake --scenario pose-missing
fall-detection --detector fake --duration 3 --event-after 1
```

All output is labelled as simulated. The fake camera status describes a test
scenario, not the real computer's camera. A possible-fall event is a signal for
future safety handling, not an emergency alert or validated inference.

## Run checks

```bash
python -m unittest discover -s tests -v
```

Tests use no camera, network, microphone, model downloads, or real notifications.

## Optional packages for later work

Optional groups declare the planned stack, but their combined compatibility and
exact versions are not yet verified or locked:

```bash
python -m pip install -e '.[detection]'
python -m pip install -e '.[audio]'
python -m pip install -e '.[voice]'
```

Do not install these to run the fake application. Verify Python and platform
wheel support before choosing the live detection environment. The scaffold was
checked with Python 3.14; that does not establish AI-package compatibility.

## Shared contract

`src/fall_detection/contracts.py` defines contract version 1. Both developers
should review this baseline before splitting and coordinate interface changes.

- `Detector`: `start()`, `set_paused(bool)`, `poll_event()`,
  `latest_preview()`, and `stop()`, plus a `source` property.
- `DetectionEvent`: unique event ID, monotonic timestamp, explicit source,
  possible-fall/fault kind, optional score, and structured fault code.
- `Preview`: timestamp, source, camera/pose status, model readiness, optional
  score, optional BGR frame, landmarks, and optional fault code.
- `BgrFrame`: immutable packed uint8 BGR bytes, with width and height. OpenCV
  callers can supply `frame.tobytes()`; renderers can use
  `numpy.frombuffer(data, dtype=numpy.uint8).reshape(height, width, 3)`.
  Keep this data in memory; do not log or serialize previews.
- Landmarks use native MediaPipe indices and normalized image coordinates.
  The detector owns conversion to the classifier's verified feature layout.
- All timestamps use `time.monotonic()` seconds in the same process. The frame
  timestamp is the preview timestamp; never mix it with wall-clock time.
- Read events separately from previews. Replacing preview data cannot consume
  events. A score of `None` means unknown, not a non-fall prediction.
- Events are FIFO except that an overflow fault can take priority. The fake
  detector retains already queued events, reports overflow in a reserved slot,
  and latches a fault until stop/start. Future detectors must also make overflow
  explicit and prevent silent event loss.
- Pause/resume clears stale detection evidence, not application incidents.
  Camera failures remain visible across pause/resume. Stop keeps pending events
  readable; a subsequent start begins a fresh session.

For deterministic tests, construct `FakeDetector(clock=your_clock)`, then call
`start()`, `emit_possible_fall()`, `set_pose_status(...)`, or `fail_camera()`.
These injection hooks are fake-only; application code should consume the
`Detector` interface. Preview reads do not generate events.

## Work independently

Person A owns `detection.py`, preprocessing, model assets, and `tests/detection/`.
Person B owns `fake_detector.py`, the application, safety, controls, and shared
setup. Person B can test with the fake detector without AI dependencies.

Start from the same baseline in separate branches and working copies. A real
detector must implement the same contract; it must not silently replace failed
inference with simulated events. Keep fake runs on fake notification senders
when notifications are added.
