# Fall Detection Prototype

## Goal

Build a privacy-first fall detection prototype on a laptop or desktop using a webcam and speakers. A microphone is optional for local voice response recognition. Complete the core flow: detect a possible fall, ask whether the person is okay, allow cancellation, and notify one caregiver if the countdown expires.

Raspberry Pi deployment and testing are outside the current scope. Pi compatibility and performance remain unverified.

Implementation steps and progress are tracked in [IMPLEMENTATION.md](IMPLEMENTATION.md).
Setup, simulation commands, and the implemented shared contract are documented in [README.md](README.md).

For two-person development, follow the file ownership and shared interface in that plan. One person owns detection; the other owns application integration, safety, and controls. Coordinate changes to shared files before editing them.

## Technology Stack

- **Language:** Python, with three modules in one application.
- **Camera:** OpenCV for webcam capture and frame processing.
- **Body tracking:** MediaPipe Pose for body landmarks.
- **Fall classification:** The pretrained punpayut Transformer checkpoint, evaluated locally with LiteRT (`ai-edge-litert`) using its `.tflite` file.
- **Audio:** Play recorded prompts through `sounddevice`. No cloud speech service is required.
- **Optional speech recognition:** Vosk with a small local English model for responses such as "I'm okay." An on-screen cancellation button is sufficient for the initial version.
- **Notifications:** Telegram Bot API for text alerts to one caregiver. Send only necessary alert metadata, never video or audio. Telegram requires internet access.
- **Local controls:** An OpenCV status window with on-screen cancellation and keyboard shortcuts for cancellation and monitoring pause.
- **State and configuration:** In-memory state and fixed local configuration. No database or persistent alert history.
- **Compatibility:** Pin dependency versions after verifying them on the development computer. The upstream fall repository uses older MediaPipe APIs; verify preprocessing and model outputs before adopting newer APIs.

## Architecture

Keep three modules with clear interfaces inside one Python application:

1. **Detection:** Capture frames, extract MediaPipe landmarks, maintain a short timestamped movement history, and combine the fall classifier with simple safety rules. Discard frames after processing. Evaluate https://github.com/punpayut/Fall-Detection and reconcile its training/runtime preprocessing before relying on its output.
2. **Safety:** On a suspected fall, play "Are you okay?" and start a countdown. Allow cancellation with the on-screen button or keyboard; local voice cancellation is optional. Send one caregiver alert if the countdown expires. Send notifications in a background worker so network delays do not block detection, controls, or the countdown.
3. **Device controls:** Show monitoring, countdown, alert delivery, camera failure, and pause status. Handle local controls and fixed configuration.

## Requirements

- Process video and any captured voice locally. Do not save camera frames or microphone recordings, or include them in logs or analytics.
- Keep Telegram credentials outside source code and logs. Send only necessary alert text.
- Detection and countdown handling must work without internet access or cloud AI subscriptions. Show notification delivery failures; a successful send does not prove a caregiver has responded.
- Display actual monitoring status. Clearly distinguish camera failure, monitoring pause, and a missing or unreliable pose. If microphone input is enabled, indicate when it is active.
- Prefer existing pretrained models over training a new model. Match the selected checkpoint's landmark order, coordinates, normalization, sampling rate, and sequence length.
- Test the basic flow with representative falls and normal activities such as sitting, bending, and intentional lying down. Test cancellation, countdown expiry, camera disconnection, and notification failure.
- Treat missing or unreliable pose data as unknown, not evidence that the person is safe.
- Falls outside the camera view cannot be detected. Loss of visibility alone must not automatically trigger an emergency alert.
- Use explicit states for monitoring, suspected fall, awaiting response, and alerting. Avoid duplicate alerts for one event.
- Verify the complete flow on the development computer. Record observed detections, missed falls, false alerts, and alert delay without presenting a small test as validated accuracy.
- This is an experimental prototype with no process crash isolation or guarantee of continuous availability. Do not claim medical-grade accuracy or suitability as a sole emergency response system.

## Out of Scope

Raspberry Pi integration, physical controls, always-listening manual help commands, multiple caregivers, acknowledgement/escalation, a dashboard/settings UI, persistent history, separate services, automatic process recovery, automatic updates, remote administration, and extensive endurance testing. Revisit deployment security and reliability before real-world use.
