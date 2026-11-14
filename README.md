# Fall Detection Prototype

Computer-first, local fall detection project. Requirements are in
[AGENTS.md](AGENTS.md); ownership and tasks are in
[IMPLEMENTATION.md](IMPLEMENTATION.md).

## Current behavior

Person B's application works with the explicitly simulated detector: incident
states, countdown, cancellation, pause/resume, explicit rearming, an OpenCV status
window, asynchronous prompt playback, and asynchronous notification results.
Fake runs always use a fake notification sender, even if Telegram credentials
exist in the environment. No camera opens, no model runs, and no microphone is
captured. Live mode still exits with an explicit error until A's verified detector
is integrated. Nothing here establishes detection accuracy.

There is no prompt recording yet. A missing/unreadable prompt or failed playback
shows an audio error and leaves countdown/cancellation available. The Telegram
transport is implemented and tested with mocked HTTP; no real bot or destination
has been configured or contacted.

## Install and run

The headless simulation uses Python 3.11+ and the standard library:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
fall-detection --detector fake --countdown 0.2 --duration 1
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell.
`python -m fall_detection` is also supported. The console run defaults to one
second; it has no interactive cancellation controls. Use the window for controls:

```bash
python -m pip install -c requirements-device.txt -e '.[ui,audio]'
fall-detection --detector fake --ui --scenario idle
```

The window remains open until quit unless `--duration SECONDS` is provided.
Use `--scenario possible-fall` (the default) to inject a fall after 0.1 seconds,
or press F while idle. Countdown defaults to 20 seconds. Scenarios also include
`camera-failure`, `pose-missing`, and `fall-camera-failure`. `--event-after` changes
scenario timing. Run simulated failure cases without credentials or internet:

```bash
fall-detection --detector fake --ui --scenario fall-camera-failure --countdown 5 --notification-outcome failed
fall-detection --detector fake --countdown 0.2 --duration 1 --notification-outcome uncertain
```

All scenarios, camera statuses, scores, and delivery outcomes in fake mode are
simulated. A possible-fall event is evidence for incident handling, not a
validated detection or a caregiver alert by itself.

### Window controls and incident behavior

- C or Space, or **I'm okay / Cancel**: cancel before the countdown deadline.
- P or **Pause / Resume**: pause detection. An active countdown continues.
- R or **Reset / Rearm**: explicitly rearm after cancellation or a delivery
  result. Reset is refused while delivery is pending. Rearming clears old
  detector history and rejects events timestamped before or at reset.
- Q or Escape, or close the window: exit. An active countdown ends on exit;
  a pending delivery is reported as uncertain. There is no crash recovery.
- F: inject a fake fall. X: inject a fake camera failure. Fake fall injection
  is refused while paused or faulted. Restart the app to clear a latched fault.

Repeated detections do not extend an active countdown or create duplicate
incidents. Cancellation at or after the deadline cannot retract an alert.
There is one automatic notification attempt per incident, with no automatic
retry. Terminal incident status stays visible until explicit reset. The operator
should only rearm when ready to monitor a new incident; fresh detection evidence
can start another countdown after reset.

Camera failure, missing/unreliable pose, pause, detector errors, incident state,
audio status, and delivery are shown separately. Missing pose or score means
unknown. Loss of visibility alone does not start an incident. Detector exceptions
and stale live previews are displayed as unavailable; they do not cancel an
active incident. HighGUI runs on the main application thread; A's detector must
honor the nonblocking contract.

### Prerecorded prompt

When available, supply a local recording saying "Are you okay?":

```bash
fall-detection --detector fake --ui --prompt /absolute/path/are-you-okay.wav
```

Supported format: 16-bit PCM WAV, mono or stereo, at most 10 seconds. The prompt
plays once per incident, on a worker. Playback is cancelled when the incident is
cancelled or its response window ends. Prompt failures do not stop the safety
flow. No recording is included; speaker playback still needs a hardware check.
The application never opens a microphone. Optional Vosk voice cancellation is
not implemented.

### Telegram setup (later, separate from simulation)

1. Create a dedicated bot through [@BotFather](https://t.me/BotFather), using
   `/newbot`. Follow [Telegram's official setup guide](https://core.telegram.org/bots/tutorial#obtain-your-bot-token).
2. Open a private chat with the bot from the intended test recipient's account
   and send a message. Obtain that chat's numeric `message.chat.id` with
   [getUpdates](https://core.telegram.org/bots/api#getupdates). Use a local API
   client that does not log the token or response. Choose exactly one destination.
3. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` locally. Do not put actual
   values in source code, shell history, logs, screenshots, or chat. On Bash,
   hidden input avoids putting credentials into command history:

```bash
read -rsp 'Bot token: ' TELEGRAM_BOT_TOKEN
export TELEGRAM_BOT_TOKEN
read -rp 'Test chat ID: ' TELEGRAM_CHAT_ID
export TELEGRAM_CHAT_ID
```

Only when ready to contact that destination, explicitly run:

```bash
fall-detection --test-telegram
```

This standalone command sends one clearly labelled test message. It is never
invoked during installation, tests, or fake runs. Environment files are ignored
by Git but are not automatically loaded by the application.

Incident messages contain only a possible-fall notice and an incident ID; no
video, landmarks, audio, model score, credentials, or personal history. Real
incident delivery is gated behind future live-detector integration. The real
transport rejects simulated incidents at its network boundary.

`telegram_accepted_no_acknowledgement` means Telegram accepted the message, not
that a caregiver responded. `failed` means a known rejection or no worker
submission. `uncertain` means receipt cannot be established, for example after a
timeout. No automatic retries occur. Network requests run on a bounded worker
with a five-second socket timeout. Shutdown waits only briefly; a stuck OS/DNS
operation can outlive that wait in a daemon thread, so shutdown cannot guarantee
delivery completion. A pending attempt at exit is reported as uncertain.

## Checks and verification limits

```bash
python -m unittest discover -s tests -v
python -m pip check
```

Tests use no camera, real microphone, real speaker playback, model downloads, or
real notification services. They cover timing boundaries, deduplication,
cancellation, rearming, camera/worker failure, missing pose, notification failure
and uncertainty, secret-safe transport results, bounded asynchronous delivery,
WAV decoding, rendering, and control routing. Optional image/audio decoding tests
skip when NumPy/OpenCV are absent; the safety and headless tests need no extras.
All 43 tests pass with UI/audio extras installed. In a fresh virtual environment
without third-party packages, 41 pass and the two optional tests skip.

Observed on the development computer: Python 3.14.10, NumPy 2.5.4, OpenCV
4.14.0.94 (module version 4.14.0), sounddevice 0.5.6, cffi 2.1.1, and pycparser
3.12 import successfully; `pip check` passes. The OpenCV window completes a
simulated countdown and simulated accepted delivery. A console scenario keeps
counting down through simulated camera loss and shows failed delivery. UI/audio
package pins are in `requirements-device.txt`; these are not inference-stack
pins or evidence of speaker compatibility. The desktop emitted a Wayland plugin
warning but opened successfully through the available backend.

Still unverified: recorded prompt playback on speakers, actual Telegram delivery
and offline behavior, live camera and model integration, representative falls,
normal activities, and physical camera disconnection. No detection accuracy or
end-to-end real-device reliability claims are made.

## Shared detector contract

`src/fall_detection/contracts.py` remains contract version 1, unchanged by B's
application work. Coordinate any interface changes before implementation.

- `Detector`: `start()`, `set_paused(bool)`, `poll_event()`, `latest_preview()`,
  `stop()`, and a `source` property. Operations are nonblocking and thread-safe.
- `DetectionEvent`: unique event ID, monotonic timestamp, source, possible-fall
  or fault kind, optional model score, and structured fault code.
- `Preview`: timestamp, source, camera/pose status, model readiness, optional
  score, optional BGR frame, landmarks, and optional fault code.
- `BgrFrame`: immutable packed uint8 BGR bytes and dimensions. NumPy rendering:
  `numpy.frombuffer(data, dtype=numpy.uint8).reshape(height, width, 3)`.
- Landmarks use native MediaPipe indices and normalized image coordinates. A
  owns conversion to the verified classifier feature layout.
- Timestamps are `time.monotonic()` seconds in the same process. Preview
  timestamp is frame timestamp. Never mix monotonic and wall-clock time.
- Events are separate from replaceable previews. Overflow must be explicit;
  preserve queued events and latch a fault instead of silently evicting them.
- Pause/resume clears stale evidence, not application incidents. Camera failures
  stay visible. Stop preserves queued events; start after stop begins a new session.

A still needs to establish score meaning/range and readiness semantics against
the actual checkpoint. B treats `None` as unknown and never uses the score as a
calibrated confidence or as a countdown threshold.

## Ownership and remaining integration

A owns detection, preprocessing, model assets, and `tests/detection/`.
B owns the application, fake detector, safety, audio, notifications, controls,
shared setup, and documentation. A's files and the detector contract have not
been changed by B's implementation.

A's detection extras remain unverified and unpinned. Before combining environments,
coordinate NumPy/OpenCV requirements and test MediaPipe/LiteRT imports; do not
assume Python 3.14 supports the inference stack. Do not install detection extras
to run this simulation. The verified preprocessing checkpoint must pass before
B wires a real detector and real incident notification transport into the CLI.
Use separate branches and working copies from the shared baseline.
