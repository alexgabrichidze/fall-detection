# Fall Detection Prototype

Monitor one person with a webcam. A possible fall starts a spoken check-in and a 20-second response window. The app says “Ten seconds left” at the halfway point. Cancel locally or let the countdown expire to attempt one caregiver alert. The app says “Caregiver notified” when Telegram accepts the message. Video and pose landmarks stay in memory on the device. Detection and cancellation work offline after setup.

## Run in your browser

Use Python 3.12 in a desktop session. The full project ZIP includes the fall model. For a Git checkout, download it once using the command in [models/README.md](models/README.md).

From the project directory, with a webcam positioned to show the person and floor, run

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[detection,audio]'
fall-detection --detector live --web
```

Open `http://127.0.0.1:8765` and click **Start camera**. Use **Stop camera** to release it. Leave the Terminal process running while you monitor. `--port` selects a different local port. The browser receives previews from the app on this computer.

Choose **Input camera** above the preview before starting. To change inputs,
click **Stop camera**, choose another camera, then click **Start camera**.
Use **Refresh** after connecting a new camera. Stopping or changing the camera
does not cancel an active incident countdown. Camera selection lasts for the
current session; `--camera N` still sets the initial input.

On the first Mac launch, allow camera access when macOS prompts. If access is off, enable it for the app you launched from (usually Terminal) in **System Settings → Privacy & Security → Camera**, then relaunch.

On Windows, create the environment with `py -3.12 -m venv .venv` and activate it with `.venv\Scripts\Activate.ps1`. Live mode was exercised on Apple Silicon with Python 3.12.14. MediaPipe 0.10.21's macOS universal2 wheel has an x86_64-only tag in its internal metadata, so `pip check` can flag it on Apple Silicon even though the arm64 build imports and runs.

`--camera 1` selects a different webcam. `--countdown SECONDS` changes the response window. `--model PATH` supplies the pinned checkpoint from a different location. `--headless` runs a finite console dry run. Launch without `--web` to use the native OpenCV window.

| Control | Action |
| --- | --- |
| **C**, **Space**, or **I'm okay / Cancel** | Cancel before the deadline. |
| **P** or **Pause / Resume** | Pause tracking and clear old movement evidence. An active countdown continues. |
| **R** or **Reset / Rearm** | Return to monitoring after cancellation or a delivery result. |
| **Q**, **Esc**, or close the window | Exit. The app reports pending delivery as uncertain on exit. |

The window shows camera, pose, classifier, audio, and delivery status. Missing or unreliable tracking clears the movement window and displays an unknown score. If the camera fails, restart the app to reopen it. The app keeps an active countdown running through camera loss or a failed prompt.

The voice prompts play locally through the speakers. Use `--prompt PATH` to replace the opening message with your own mono or stereo 16-bit PCM WAV up to 10 seconds long. Microphone capture is off unless you enable optional voice cancellation.

## Live activity panel

The browser monitor ranks **Standing**, **Walking**, **Sitting**, **Bending**, **Lying / horizontal**, and **Unclear / other** beside the video. The current posture and bars update from the latest local landmarks. No additional model download is required.

Posture scores are independent support values from 0 to 100. They compare visible body angles, knee flexion, and recent alternating foot movement; they are not trained activity probabilities or accuracy percentages. Walking needs a short sequence of visible steps. A separate **Possible fall** bar shows the existing fall model's actual score, with its 90/100 model threshold marked. The other fall detection rules still apply.

When knees or feet are cropped, the panel switches to **Upright**, **Leaning**, and **Horizontal torso**, using visible shoulders and hips. It does not guess standing, sitting, walking, or lying down from the upper body alone. Full-body labels return when usable leg geometry is visible; missing hips or ambiguous torso geometry shows **Unclear**. Ranked rows slide smoothly as support changes, with animation disabled when the system requests reduced motion. Missing tracking, pause, camera stop, faults, stale frames, and connection loss clear the displayed scores. The activity panel is display-only and does not change fall events, countdowns, cancellation, or notifications. On desktop, the video fills the main workspace while activity, incident, and Telegram cards form a compact right sidebar. The frame is fitted at its native aspect ratio without cropping or colored letterboxing. The incident card stays hidden during idle monitoring and appears for check-ins, cancellation, and alert results. On small screens, an active incident moves above the other panels.

## Optional offline voice cancellation

Vosk recognizes **“I'm okay”** or **“I am okay”** locally. Enable it explicitly:

```bash
python -m pip install -e '.[voice]'
curl -fL https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip -o /tmp/fall-voice-model.zip
python -m zipfile -e /tmp/fall-voice-model.zip models
fall-detection --detector live --web --voice
```

The model is already installed in this development checkout. Using its existing environment, run:

```bash
venv/bin/fall-detection --detector live --web --voice
```

The [small English model](https://alphacephei.com/vosk/models) is Apache-2.0 licensed. Its download SHA-256 is `30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498`. Keep the extracted directory outside Git. `--voice-model PATH` selects another local model directory. No network access is needed after installation.

The microphone opens after successful prompt playback and a 0.4-second echo guard, only while a response is pending. Listening pauses for the spoken countdown reminder and resumes after playback and the echo guard. Wait for **Microphone active** before speaking, then pause briefly so recognition can finish. Recognition uses a focused response vocabulary that includes negative phrases and unknown speech. Only a complete matching phrase (or repetitions of it) with every word confidence at least 0.8 cancels the incident; partial results, silence, and other recognized phrases do not. This threshold does not guarantee correct recognition. Button and keyboard cancellation remain available.

The **Voice response** row shows readiness, model loading, microphone activity, and failures. Input audio and recognized text remain in memory and are discarded. Listening stops on cancellation, countdown expiry, or exit. A failed prompt leaves voice capture off. A missing model or microphone failure leaves the countdown running. Simulation mode never opens the microphone.

Use the system microphone by default, or pass `--microphone N` with a sounddevice input index. List devices with `venv/bin/python -m sounddevice`. On macOS, allow microphone access for Terminal or the launching app in **Privacy & Security → Microphone**. The microphone belongs to the Python process, not the browser.

Automated checks cover response filtering, playback gating, microphone cleanup/failure, stale incident results, manual cancellation, and expiry. The installed Vosk model loads and rejects the bundled prompt as a cancellation. A live spoken “I am okay” response through the HyperX system-default microphone was accepted using the focused response vocabulary. The previous unrestricted recognizer rejected the same response at the word-confidence threshold. This is one observed successful response, not a recognition accuracy estimate.

## Send real Telegram alerts

Click **Connect Telegram** → **Open Telegram**. Tap **Start** from the Telegram account that should receive alerts, then return to the monitor and click **Finish linking**. The link expires after 10 minutes. Use **Send test message** to verify delivery to that chat. The demo connects to @falldetectionsuperbot through a temporary relay on the host Mac. The Mac and tunnel need to stay online.

### Set up the shared bot once

The owner runs the relay with the bot token on one computer. It reads `TELEGRAM_BOT_TOKEN` or the token in its existing private `.env.telegram.json` file. Run it from the project directory and put this relay behind an HTTPS proxy or tunnel.

```bash
python -m fall_detection.telegram_relay
```

The relay listens on `127.0.0.1:8767`. To point the browser monitor at another relay, set `FALL_MONITOR_RELAY_URL` to its public HTTPS address at launch. To share a preconfigured copy, put that address in `src/fall_detection/relay-url.txt` before sharing the source or building the package. Each installation pairs its own Telegram recipient. A temporary tunnel address lasts for the tunnel session.

### Use your own bot

To set up a private bot in the browser monitor, remove `src/fall_detection/relay-url.txt` and launch the monitor without setting `FALL_MONITOR_RELAY_URL`. **Connect Telegram** then accepts a token from BotFather and saves it in the private local file `.env.telegram.json`. The native window can reuse that pairing using `fall-detection --detector live --telegram`. To configure the native window from environment variables, follow these steps.

1. Create a dedicated bot with [BotFather](https://t.me/BotFather).
2. Have the intended recipient open the bot's private chat and send it one message. Find the numeric `message.chat.id` using [getUpdates](https://core.telegram.org/bots/api#getupdates).
3. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` locally. In Bash or Zsh, use the commands below to avoid putting their values in shell history.

```bash
printf 'Bot token: '
read -rs TELEGRAM_BOT_TOKEN
printf '\nChat ID: '
read -r TELEGRAM_CHAT_ID
export TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
```

`fall-detection --test-telegram` sends one labelled setup message to that chat. When the recipient confirms it arrived, start monitoring with

```bash
fall-detection --detector live --telegram
```

Alerts contain a possible-fall message and an incident ID. The app makes one attempt per incident. `telegram_accepted_no_acknowledgement` means Telegram accepted the message. `failed` means delivery was rejected or could not be started. `uncertain` means the app could not establish whether Telegram received it. Use the window to rearm after a delivery result.

## Demo without a camera

A console simulation works with the standard-library install (`python -m pip install -e .`). The window uses the `ui` extra and the prompt uses the `audio` extra. The live install already includes both dependencies.

```bash
fall-detection --detector fake --web --scenario idle --countdown 5
fall-detection --detector fake --countdown 0.2 --duration 1
fall-detection --detector fake --ui --scenario idle --countdown 5 --prompt assets/prompts/are-you-okay.wav
```

In the simulation window, press **F** to start a simulated fall or **X** to simulate camera failure. Every notification stays in simulation. Use `--notification-outcome failed` or `--notification-outcome uncertain` to demonstrate delivery failures. The `possible-fall`, `camera-failure`, `pose-missing`, and `fall-camera-failure` scenarios can inject an event automatically.

## Detector and verification

The detector tracks one person with MediaPipe Pose, runs the pinned Transformer with LiteRT, and combines its score with a sustained change in torso height and orientation. Expect a brief warm-up while the detector collects 30 reliable samples. The score is an experimental model output. See [models/README.md](models/README.md) for the checkpoint, input layout, and reference comparison.

The app was exercised with four upstream clips on Apple Silicon. Clips 1–3 each raised one possible fall and clip 4 raised none. Replay covered prompt playback, cancellation, and countdown expiry after loss of input. A live session confirmed webcam startup after macOS camera approval. In a separate 20-second prerecorded rehearsal, Telegram accepted one labeled test alert. Real-world detection accuracy remains unverified. Use this prototype for a supervised demo and evaluation.

The shared detector contract remains version 1 in `src/fall_detection/contracts.py`. The worker owns camera capture and inference. The application loop reads the latest in-memory preview and a separate bounded event queue. Audio and notification workers keep the controls and countdown responsive.
