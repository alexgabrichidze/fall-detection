# Implementation Plan

Follow [AGENTS.md](AGENTS.md) for architecture, requirements, and scope. Target the development computer, not a Raspberry Pi. Check off each item only after completing and verifying it.

Model compatibility is the first checkpoint. Optional voice cancellation comes after the core flow works.

Live detection is integrated with the Part B application through contract version 1. The packaged build includes the pinned Transformer checkpoint and a local audio prompt. Live alerts use the existing Telegram worker when the operator explicitly enables `--telegram`.

Verification on Apple Silicon and Python 3.12 covered the published checkpoint, the training notebook's preprocessing on a public keypoint sample, and full application replays of four public clips. A live session confirmed webcam startup, and Telegram accepted one labeled alert after a 20-second replay countdown. See [README.md](README.md) and [models/README.md](models/README.md).

## Two-person work split

Suggested assignment: you take **A (detection)** and your teammate takes **B (application)**. Swap the labels if preferred; keep file ownership unchanged once work starts.

| Owner | Responsibility | Owned files and directories |
| --- | --- | --- |
| A | Camera capture, MediaPipe, model compatibility, preprocessing, movement buffer, fall rules, model evaluation | `detection.py`, `preprocessing.py`, `detection_config.py`, `models/`, `tools/detection/`, `tests/detection/` |
| B | Fake detector, countdown, cancellation, audio, notifications, status window, application startup, integration | `fake_detector.py`, `safety.py`, `device.py`, `audio.py`, `notifier.py`, `main.py`, `config.py`, `assets/prompts/`, `tests/safety/`, `tests/device/`, `tests/integration/` |
| B, with both reviewing changes | Shared interface, dependencies, setup, and documentation | `contracts.py`, `tests/test_contracts.py`, package metadata, dependency pins/lockfile, `.gitignore`, `AGENTS.md`, `IMPLEMENTATION.md`, `README.md` |

All application Python filenames above are relative to `src/fall_detection/`; other paths are relative to the repository root. A owns detection-specific configuration; B owns general application configuration. A reports required dependency changes to B instead of editing the shared dependency files independently.

### Agree on the interface first

The initial interface is implemented in `contracts.py`; both developers still need to review it before parallel implementation. Keep this file free of model-loading code so B can work without a functioning model. The README documents timestamp units, packed BGR preview bytes, event ordering, lifecycle, and overflow behavior.

- [x] Define detector operations: start, pause/resume, read pending events, read the latest preview, and stop.
- [x] Define detection events containing an event ID, monotonic timestamp, event kind, and optional model score. Include possible-fall and detector-fault events.
- [x] Define preview data containing the latest frame, frame timestamp, landmarks, camera status, pose status, model readiness, and optional model score. Frames remain in memory only.
- [x] Distinguish valid tracking, missing/unreliable pose, camera failure, and monitoring pause. A missing score must not mean "no fall."
- [x] Keep fall/fault events separate from the replaceable latest-preview buffer. Dropping an old preview must not drop a possible-fall event. Surface event-queue overflow as a fault rather than silently losing events.
- [x] Make A responsible for producing detection evidence; make B responsible for countdowns, incident IDs, cancellation, notification deduplication, and rendering.
- [x] Have pause/resume clear A's stale movement history while preserving B's active incident unless explicitly cancelled.
- [x] Define a fake detector with the same interface for B's integration tests. Clearly label simulated events and keep them separate from live detection mode.
- [ ] Agree that interface changes require both people to coordinate before implementation.

### Work without conflicting edits

- [ ] B completes the shared setup and interface first; both start from that same revision.
- [ ] Use separate Git branches and working copies. If using one computer, use separate worktrees rather than editing the same working directory.
- [ ] Edit owned files only. Request changes from the owner of another file.
- [ ] A works through detection tasks while B builds and tests the complete safety flow using the fake detector.
- [x] Keep the OpenCV window and keyboard/mouse handling in B's main application loop. A supplies frames and landmarks instead of creating a second application window.
- [ ] A reports completed detection tasks and blockers to B; B updates the shared checklist to avoid concurrent documentation edits.
- [x] B integrates the live detector after the input-compatibility checkpoint passes, with A checking preprocessing and timing.
- [ ] Both run the end-to-end checks before considering integration complete.

## 1. Set up the project (B leads; both verify dependencies)

- [x] Create a Python virtual environment.
- [x] Install OpenCV, MediaPipe, NumPy, LiteRT (`ai-edge-litert`), and `sounddevice`.
- [x] Confirm each dependency imports successfully on the development computer.
- [x] Pin the working dependency versions.
- [x] Create the application package, `contracts.py`, and the application skeleton, including an explicit unimplemented live-detector entry point for A.
- [x] Create directories for model assets, recorded prompts, and tests.
- [x] Exclude credentials, temporary files, virtual environments, and local evaluation videos from Git.

The standard-library console simulation remains available. The live pins are in the `detection` and `audio` extras in `pyproject.toml`. The simulation's newer UI environment has separate pins in `requirements-device.txt`.

## 2. Check the pretrained model (A)

- [x] Download the exact `.tflite` checkpoint from https://github.com/punpayut/Fall-Detection.
- [x] Record its source commit, checksum, and applicable license information.
- [x] Load the checkpoint with LiteRT.
- [x] Inspect the actual input shape, input type, and output shape.
- [x] Run a correctly shaped sample to confirm inference executes. Treat this as a compatibility check, not evidence of accuracy.

Reference: [LiteRT migration and Python runtime](https://developers.google.com/edge/litert/migration).

## 3. Resolve the model's input requirements (A)

- [x] Compare upstream training extraction, sequence preparation, normalization, and runtime code.
- [x] Establish the landmark order, coordinate units, normalization, and sequence length. The training extractor uses source FPS without a fixed rate.
- [x] Investigate the known training/runtime mismatches. Do not change inference landmarks without establishing what the checkpoint was trained to receive.
- [x] Implement one shared preprocessing function.
- [x] Compare its output against a verified reference input and document the evidence.
- [ ] Add focused checks for landmark order, coordinate conversion, normalization, and invalid input handling.

**Checkpoint:** If the input requirements cannot be established, do not use this checkpoint for live decisions. Record the blocker. Application controls may still be tested with clearly labelled test events; do not silently substitute a different detector or present simulated events as model detections.

## 4. Get the webcam working (A captures; B displays)

- [ ] Open the camera selected in fixed local configuration.
- [x] A exposes the latest frame through the agreed preview interface; B displays it in the OpenCV window.
- [x] B adds a quit shortcut and calls A's cleanup operation on exit.
- [x] Surface capture-open and frame-read failures. The replay verified the frame-read failure path.
- [x] Release the capture object and window on exit.
- [x] Confirm captured frames remain in memory and are not recorded.

## 5. Add body tracking (A produces results; B renders)

- [x] Initialize MediaPipe Pose using the version and model established by the compatibility checks.
- [x] A processes frames and supplies landmarks; B draws the detected skeleton locally.
- [x] Attach timestamps to pose results.
- [x] Show separate statuses for a valid pose, unreliable pose, and no visible person.
- [x] Keep camera failure separate from missing pose data.
- [x] Handle delayed or dropped results without assuming constant frame delivery.

Reference: [MediaPipe Pose Landmarker](https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker).

## 6. Connect tracking to the fall model (A)

- [x] Convert landmarks through the verified preprocessing function.
- [x] Maintain a fixed-size movement buffer.
- [x] Sample at the cadence established from the training pipeline. Do not assume a frame count equals a fixed duration.
- [x] Clear stale history after long gaps, camera restart, or monitoring pause.
- [x] Wait for a complete valid sequence before running inference.
- [x] Verify the output's class meaning against the reference pipeline.
- [x] Expose the model score through the preview interface so B can display it without presenting it as calibrated real-world confidence.

## 7. Add simple fall-decision rules (A)

- [x] Combine the model score with posture and recent movement.
- [x] Require sufficient consistent evidence to avoid triggering from one noisy result.
- [x] Consider time spent near the floor.
- [x] Avoid requiring every fall to be fast or completely horizontal.
- [x] Do not trigger solely because tracking disappears.
- [x] Keep thresholds in `detection_config.py` and document changes made while evaluating them.

## 8. Build the countdown logic (B)

- [x] Implement monitoring, suspected fall, awaiting response, and alerting states.
- [x] Assign an identifier to each incident.
- [x] Start one countdown when a suspected fall becomes an incident.
- [x] Use a monotonic clock so system-clock changes cannot alter the countdown.
- [x] Allow explicit cancellation.
- [x] Prevent repeated detections from restarting the countdown or creating duplicate incidents.
- [x] Keep an active incident pending if the camera fails or monitoring pauses; do not silently cancel it.
- [x] Define an explicit incident reset and rearming behavior so the same event does not immediately trigger again.
- [x] Test transitions with a controllable test clock, including cancellation immediately before expiry.

## 9. Add the prompt and local controls (B)

- [x] Bundle a local "Are you okay?" WAV prompt.
- [x] Play it once when the countdown starts.
- [x] Keep audio playback from blocking controls or the countdown.
- [x] Display remaining countdown time.
- [x] Add an on-screen cancellation button and a keyboard shortcut.
- [x] Add monitoring pause/resume controls.
- [x] Display camera status, tracking status, and incident status separately.
- [x] Show an audio error if prompt playback fails while keeping cancellation and countdown handling available.

## 10. Add Telegram notifications (B)

- [ ] Configure one bot and one caregiver destination.
- [x] Read credentials from environment variables and keep them out of source code and logs.
- [x] Send a clearly labelled test message to the configured test destination.
- [x] Implement text-only incident alerts containing only necessary metadata.
- [x] Send requests through a background worker with a timeout.
- [x] Return delivery results to the application.
- [x] Show failed or uncertain delivery clearly.
- [x] Send only one automatic alert attempt per incident initially.
- [x] Describe successful sending as Telegram accepting the message, not caregiver acknowledgement.
- [x] Use a fake notification sender in automated tests so tests never contact a real caregiver.

## 11. Connect the complete application (B leads; A supports)

- [x] Keep camera processing and inference from blocking the interface or countdown.
- [x] Use bounded queues so slow processing cannot accumulate old frames indefinitely.
- [x] Keep notification requests separate from detection and countdown handling.
- [x] Surface worker errors as an unavailable status instead of continuing to display healthy monitoring.
- [x] Shut down camera, audio, and workers cleanly.
- [ ] Confirm detection, controls, and countdowns continue without internet access.

## 12. Test the core flow (both)

- [x] Use existing, appropriately licensed fall clips for positive cases; keep evaluation videos outside Git and do not record webcam footage.
- [ ] Test sitting, bending, and intentional lying down as non-fall cases.
- [x] Exercise cancellation before expiry in a public replay.
- [x] Exercise countdown expiry producing one dry-run alert.
- [ ] Test repeated detections producing no duplicate alert.
- [ ] Disconnect the camera during monitoring and during an active countdown.
- [ ] Disable internet during notification delivery and confirm the failure is visible.
- [ ] Confirm no captured video, audio, or credentials appear in saved files or logs.
- [ ] Record observed detections, missed falls, false alerts, and alert delay without retaining personal alert history.
- [x] Keep claims limited to the cases actually tested; a small evaluation does not establish validated accuracy.

## 13. Add voice cancellation only after the core works (B, optional)

- [x] Install Vosk and a small local English model.
- [x] Capture microphone input in memory only during the response window and display when listening is active.
- [x] Accept clearly recognized cancellation phrases such as "I'm okay."
- [x] Treat silence or unclear speech as no confirmed response.
- [x] Prevent the application's own prompt from cancelling an incident.
- [x] Stop listening when the incident is cancelled, the response window ends, or the application exits.
- [x] Retain the button and keyboard controls when speech recognition fails.
- [x] Run automated core-flow checks with speech recognition enabled and load the real local model.
- [x] Recognize a live spoken response through the development computer microphone in a voice-only check.

Voice capture starts only after successful prompt playback plus a 0.4-second echo guard.
Automated checks cover positive/negative phrases, input failures, stale results, and
expiry. A live HyperX response was accepted with a focused response grammar;
the previous unrestricted recognizer rejected the same phrase at the confidence
threshold. Setup and launch flags are documented in README.md.

## 14. Write the run instructions (B writes; A verifies detection setup)

- [x] Document installation, model downloads, and credential setup.
- [x] Document camera selection, fixed configuration, and local controls.
- [x] List unresolved model limitations and the scope of completed testing.
- [x] Provide launch commands for live mode, Telegram alerts, and simulation.
- [ ] Verify the instructions from a fresh environment on the development computer.
