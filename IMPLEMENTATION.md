# Implementation Plan

Follow [AGENTS.md](AGENTS.md) for architecture, requirements, and scope. Target the development computer, not a Raspberry Pi. Check off each item only after completing and verifying it.

Model compatibility is the first checkpoint. Optional voice cancellation comes after the core flow works.

The shared scaffold lives in `src/fall_detection/`, with contract version 1 in `contracts.py`. B's safety controller, OpenCV controls, audio worker, notification worker, and Telegram transport are implemented. The detector contract and A-owned files are unchanged. Fake mode tests the application without a camera, model, or real notification destination. Live CLI integration remains disabled pending A's verified detector. See [README.md](README.md) for commands, verification evidence, and remaining external checks.

B verification: standard-library tests and optional rendering/decoding tests pass; the actual OpenCV window completes a simulated countdown and simulated accepted delivery. Simulated camera failure and failed notification delivery remain visible while countdown handling continues. A prerecorded prompt, speaker playback, a Telegram bot/test chat, real delivery, and A's live detector remain pending. UI/audio versions are pinned separately in `requirements-device.txt`; inference-stack versions remain unverified. Checked boxes below describe implemented behavior verified through tests or simulation, not complete real-device validation.

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
- [ ] Make A responsible for producing detection evidence; make B responsible for countdowns, incident IDs, cancellation, notification deduplication, and rendering.
- [ ] Have pause/resume clear A's stale movement history while preserving B's active incident unless explicitly cancelled.
- [x] Define a fake detector with the same interface for B's integration tests. Clearly label simulated events and keep them separate from live detection mode.
- [ ] Agree that interface changes require both people to coordinate before implementation.

### Work without conflicting edits

- [ ] B completes the shared setup and interface first; both start from that same revision.
- [ ] Use separate Git branches and working copies. If using one computer, use separate worktrees rather than editing the same working directory.
- [ ] Edit owned files only. Request changes from the owner of another file.
- [ ] A works through detection tasks while B builds and tests the complete safety flow using the fake detector.
- [ ] Keep the OpenCV window and keyboard/mouse handling in B's main application loop. A supplies frames and landmarks instead of creating a second application window.
- [ ] A reports completed detection tasks and blockers to B; B updates the shared checklist to avoid concurrent documentation edits.
- [ ] B integrates the live detector after the input-compatibility checkpoint passes, with A checking preprocessing and timing.
- [ ] Both run the end-to-end checks before considering integration complete.

## 1. Set up the project (B leads; both verify dependencies)

- [x] Create a Python virtual environment.
- [ ] Install OpenCV, MediaPipe, NumPy, LiteRT (`ai-edge-litert`), and `sounddevice`.
- [ ] Confirm each dependency imports successfully on the development computer.
- [ ] Pin the working dependency versions.
- [x] Create the application package, `contracts.py`, and the application skeleton, including an explicit unimplemented live-detector entry point for A.
- [x] Create directories for model assets, recorded prompts, and tests.
- [x] Exclude credentials, temporary files, virtual environments, and local evaluation videos from Git.

The console simulation has no runtime dependencies and passes its checks with Python 3.14. UI/audio packages import successfully and are pinned separately in `requirements-device.txt`. The complete detection stack remains unchecked. Coordinate package versions before combining A's environment with B's; successful UI/audio imports do not establish inference compatibility or speaker playback.

## 2. Check the pretrained model (A)

- [ ] Download the exact `.tflite` checkpoint from https://github.com/punpayut/Fall-Detection.
- [ ] Record its source commit, checksum, and applicable license information.
- [ ] Load the checkpoint with LiteRT.
- [ ] Inspect the actual input shape, input type, and output shape.
- [ ] Run a correctly shaped sample to confirm inference executes. Treat this as a compatibility check, not evidence of accuracy.

Reference: [LiteRT migration and Python runtime](https://developers.google.com/edge/litert/migration).

## 3. Resolve the model's input requirements (A)

- [ ] Compare upstream training extraction, sequence preparation, normalization, and runtime code.
- [ ] Establish the exact landmark order, coordinate units, normalization, sequence length, and sampling rate.
- [ ] Investigate the known training/runtime mismatches. Do not change inference landmarks without establishing what the checkpoint was trained to receive.
- [ ] Implement one shared preprocessing function.
- [ ] Compare its output against a verified reference input and document the evidence.
- [ ] Add focused checks for landmark order, coordinate conversion, normalization, and invalid input handling.

**Checkpoint:** If the input requirements cannot be established, do not use this checkpoint for live decisions. Record the blocker. Application controls may still be tested with clearly labelled test events; do not silently substitute a different detector or present simulated events as model detections.

## 4. Get the webcam working (A captures; B displays)

- [ ] Open the camera selected in fixed local configuration.
- [ ] A exposes the latest frame through the agreed preview interface; B displays it in the OpenCV window.
- [ ] B adds a quit shortcut and calls A's cleanup operation on exit.
- [ ] Detect camera-open and frame-read failures.
- [ ] Release the camera and window cleanly on exit.
- [ ] Confirm captured frames remain in memory and are not recorded.

## 5. Add body tracking (A produces results; B renders)

- [ ] Initialize MediaPipe Pose using the version and model established by the compatibility checks.
- [ ] A processes frames and supplies landmarks; B draws the detected skeleton locally.
- [ ] Attach timestamps to pose results.
- [ ] Show separate statuses for a valid pose, unreliable pose, and no visible person.
- [ ] Keep camera failure separate from missing pose data.
- [ ] Handle delayed or dropped results without assuming constant frame delivery.

Reference: [MediaPipe Pose Landmarker](https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker).

## 6. Connect tracking to the fall model (A)

- [ ] Convert landmarks through the verified preprocessing function.
- [ ] Maintain a fixed-size movement buffer.
- [ ] Sample at the cadence established from the training pipeline. Do not assume a frame count equals a fixed duration.
- [ ] Clear stale history after long gaps, camera restart, or monitoring pause.
- [ ] Wait for a complete valid sequence before running inference.
- [ ] Verify the output's class meaning against the reference pipeline.
- [ ] Expose the model score through the preview interface so B can display it without presenting it as calibrated real-world confidence.

## 7. Add simple fall-decision rules (A)

- [ ] Combine the model score with posture and recent movement.
- [ ] Require sufficient consistent evidence to avoid triggering from one noisy result.
- [ ] Consider time spent near the floor.
- [ ] Avoid requiring every fall to be fast or completely horizontal.
- [ ] Do not trigger solely because tracking disappears.
- [ ] Keep thresholds in `detection_config.py` and document changes made while evaluating them.

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

- [ ] Add a recorded "Are you okay?" prompt.
- [ ] Play it once when the countdown starts.
- [x] Keep audio playback from blocking controls or the countdown.
- [x] Display remaining countdown time.
- [x] Add an on-screen cancellation button and a keyboard shortcut.
- [x] Add monitoring pause/resume controls.
- [x] Display camera status, tracking status, and incident status separately.
- [x] Show an audio error if prompt playback fails while keeping cancellation and countdown handling available.

## 10. Add Telegram notifications (B)

- [ ] Configure one bot and one caregiver destination.
- [x] Read credentials from environment variables and keep them out of source code and logs.
- [ ] Send a clearly labelled test message to the configured test destination.
- [x] Implement text-only incident alerts containing only necessary metadata.
- [x] Send requests through a background worker with a timeout.
- [x] Return delivery results to the application.
- [x] Show failed or uncertain delivery clearly.
- [x] Send only one automatic alert attempt per incident initially.
- [x] Describe successful sending as Telegram accepting the message, not caregiver acknowledgement.
- [x] Use a fake notification sender in automated tests so tests never contact a real caregiver.

## 11. Connect the complete application (B leads; A supports)

- [ ] Keep camera processing and inference from blocking the interface or countdown.
- [ ] Use bounded queues so slow processing cannot accumulate old frames indefinitely.
- [ ] Keep notification requests separate from detection and countdown handling.
- [ ] Surface worker errors as an unavailable status instead of continuing to display healthy monitoring.
- [ ] Shut down camera, audio, and workers cleanly.
- [ ] Confirm detection, controls, and countdowns continue without internet access.

## 12. Test the core flow (both)

- [ ] Use existing, appropriately licensed fall clips for positive cases; keep evaluation videos outside Git and do not record webcam footage.
- [ ] Test sitting, bending, and intentional lying down as non-fall cases.
- [ ] Test cancellation before countdown expiry.
- [ ] Test countdown expiry producing one alert attempt.
- [ ] Test repeated detections producing no duplicate alert.
- [ ] Disconnect the camera during monitoring and during an active countdown.
- [ ] Disable internet during notification delivery and confirm the failure is visible.
- [ ] Confirm no captured video, audio, or credentials appear in saved files or logs.
- [ ] Record observed detections, missed falls, false alerts, and alert delay without retaining personal alert history.
- [ ] Keep claims limited to the cases actually tested; a small evaluation does not establish validated accuracy.

## 13. Add voice cancellation only after the core works (B, optional)

- [ ] Install Vosk and a small local English model.
- [ ] Capture microphone input in memory only during the response window and display when listening is active.
- [ ] Accept clearly recognized cancellation phrases such as "I'm okay."
- [ ] Treat silence or unclear speech as no confirmed response.
- [ ] Prevent the application's own prompt from cancelling an incident.
- [ ] Stop listening when the incident is cancelled, the response window ends, or the application exits.
- [ ] Retain the button and keyboard controls when speech recognition fails.
- [ ] Repeat the core-flow checks with speech recognition enabled.

## 14. Write the run instructions (B writes; A verifies detection setup)

- [ ] Document installation, model downloads, and credential setup.
- [ ] Document camera selection, fixed configuration, and local controls.
- [ ] List unresolved model limitations and the scope of completed testing.
- [x] Provide one command to start the application (fake mode; live mode remains gated).
- [ ] Verify the instructions from a fresh environment on the development computer.
