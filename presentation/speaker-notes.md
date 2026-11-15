# Speaker notes

## 01 / Fall detection

Introduce this as an experimental computer-based prototype. The intent is to detect a possible fall locally, ask whether the person is okay, and notify one caregiver after a response window. The application workflow is implemented and tested with simulated inputs. Live model integration is pending. The drawing is a conceptual landmark diagram, not model evidence.

## 02 / Research question

Frame the engineering question rather than citing unverified medical statistics. Detection alone is only one part of the system. The response workflow must handle uncertainty, let a person cancel, and show whether a message was accepted. Hypothesis: local vision can be combined with a deterministic response workflow; detection performance still needs evaluation.

## 03 / Intended response flow

Walk from left to right. This is the intended complete flow, not a claim that live detection is already working. Twenty seconds is a configurable prototype default, not a validated clinical timing recommendation. Cancellation must occur before expiry. Telegram accepting a message does not mean the caregiver has responded. Voice cancellation is optional future work.

## 04 / Privacy boundary

Describe the target architecture. Frames and pose landmarks are local, transient data. The microphone is not used in the current version. Telegram requires internet access and receives only necessary alert text. Camera capture and local inference are A's pending integration work. There is no database or persistent alert history. This is not a claim that Telegram is a local service.

## 05 / Model verification gate

This entire live vision pipeline remains pending. A is evaluating the punpayut Fall-Detection Transformer checkpoint as a TFLite model with LiteRT. Before live decisions, establish training/runtime preprocessing, tensor shapes, landmark order, coordinates, normalization, cadence, sequence length, and output class meaning. A correctly shaped inference call proves compatibility only, not accuracy. Missing pose remains unknown and must not trigger an emergency by itself.

## 06 / Deterministic safety logic

A supplies detection evidence; B owns incidents and alert decisions. Countdown timing uses a monotonic clock. Tests cover cancellation immediately before expiry, exact-deadline behavior, repeated detections, reset barriers, camera/worker failure, and stalled transport. After cancellation or delivery, the operator must explicitly reset. No automatic notification retry occurs. Delivery acceptance is not caregiver acknowledgement.

## 07 / Simulated demo

Before presenting, run: .venv/bin/fall-detection --detector fake --ui --scenario idle. Press F to start the simulated incident. Press C or the cancellation button before expiry. Press R to rearm, then F again and allow the countdown to expire. The displayed outcome is simulated; this demonstration never sends a real message. There is no prerecorded prompt yet, so the app shows an audio error while the countdown continues. This slide is an interaction schematic, not an actual application screenshot.

## 08 / Evidence and limits

All 43 tests passed with optional UI/audio dependencies installed. In a fresh environment with no third-party packages, 41 passed and two optional decoding/rendering tests skipped. An actual OpenCV window completed a simulated countdown and simulated delivery. No real speaker playback, camera/model operation, or Telegram destination was tested. Do not translate this test count into a detection accuracy claim.

## 09 / Planned evaluation

These are planned experiments; no detection measurements are available yet. Use appropriately licensed existing fall clips and representative non-fall activities; do not stage dangerous falls or record monitored webcam footage. Track observed detections, misses, false alerts, and delay. Evaluate camera failure and offline delivery explicitly. A small evaluation cannot establish validated accuracy. Real prompt audio and a Telegram test bot/chat are still needed.

## 10 / Closing

Close with the central contribution: a privacy-first architecture paired with an explicit, testable incident workflow. The application layer is implemented; live detection and real-device verification remain. This is a laptop/desktop prototype, not a medically validated system or a substitute for emergency care. Raspberry Pi deployment is out of scope. Invite questions about the preprocessing gate, cancellation policy, and evaluation design.
