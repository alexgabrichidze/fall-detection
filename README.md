# Safe&Sound

Monitor one person with a webcam. A possible fall plays a spoken check-in, then starts a full 20-second response window after the warning finishes. Tap **I'm okay** on screen or say **“I'm okay”** out loud when voice cancellation is enabled. Safe&Sound calls out ten seconds remaining, confirms cancellation, and announces whether the alert was sent. If the countdown expires, the linked caregiver receives a short, silent clip when available. Detection and cancellation work offline after setup.

## Use Safe&Sound on a phone

Open [Safe&Sound](https://safe-n-sound.netlify.app) in Safari on iPhone or Chrome on Android. Tap the camera preview and allow camera access. If multiple cameras are available, choose one when prompted. To install it, use **Share → Add to Home Screen** in Safari or the **Install app** option in Chrome's browser menu.

The browser runs the fall model on that device. Keep the app open with the screen awake while monitoring. Once the camera is running, use **Enable voice** in the camera controls to download the offline speech model and grant microphone access. The hosted app uses Netlify Functions to deliver Telegram alerts and receive caregiver replies.

### Build and publish the browser version

```bash
npm ci --prefix web
npm run --prefix web build
```

Deploy with the project's `netlify.toml` so Netlify publishes `web/dist` and `web/functions` together. In the site's production settings, add `TELEGRAM_BOT_TOKEN` as a secret and `TELEGRAM_BOT_USERNAME` as the bot username without the @. Include Functions if your plan offers scope selection. After deploying, register the HTTPS webhook with the same token in your local environment.

```bash
npm run --prefix web telegram:webhook -- https://your-site.netlify.app
```

## Run the desktop monitor in your browser

Use Python 3.12 in a desktop session. The full project ZIP includes the fall model and the optional offline English speech model. For a Git checkout, download the fall model using the command in [models/README.md](models/README.md).

From the project directory, with a webcam positioned to show the person and floor, run

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[detection,audio]'
fall-detection --detector live --web
```

Open `http://127.0.0.1:8765` and click the camera preview. Leave the Terminal process running while you monitor. `--port` selects a different local port. The browser receives previews from the app on this computer.

If multiple cameras are available, pick one when prompted. Click **Stop** and then click the preview again to switch cameras. An active incident countdown continues while you change cameras.

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

The voice prompts play locally through the speakers. The response countdown starts after the opening warning finishes; the ten-second reminder uses that new deadline. Cancellation remains available while the warning plays. If playback fails, the full response window still starts. A stalled opening warning is cancelled after 12 seconds before the countdown begins. Use `--prompt PATH` to replace the opening message with your own mono or stereo 16-bit PCM WAV up to 10 seconds long. Microphone capture is off unless you enable optional voice cancellation.

## Live activity panel

The browser monitor ranks **Standing**, **Walking**, **Sitting**, **Bending**, **Lying / horizontal**, and **Unclear / other** beside the video. The current posture and bars update from the latest local landmarks. No additional model download is required.

Posture scores are independent support values from 0 to 100. They compare visible body angles, knee flexion, and recent alternating foot movement; they are not trained activity probabilities or accuracy percentages. Walking needs a short sequence of visible steps. A separate **Possible fall** bar shows the existing fall model's actual score, with its 90/100 model threshold marked. The other fall detection rules still apply.

When knees or feet are cropped, the panel switches to **Upright**, **Leaning**, and **Horizontal torso**, using visible shoulders and hips. It does not guess standing, sitting, walking, or lying down from the upper body alone. Full-body labels return when usable leg geometry is visible; missing hips or ambiguous torso geometry shows **Unclear**. Ranked rows slide smoothly as support changes, with animation disabled when the system requests reduced motion. Missing tracking, pause, camera stop, faults, stale frames, and connection loss clear the displayed scores. The activity panel is display-only and does not change fall events, countdowns, cancellation, or notifications. On desktop, the video fills the main workspace while activity, incident, and Telegram cards form a compact right sidebar. The frame is fitted at its native aspect ratio without cropping or colored letterboxing. The incident card stays hidden during idle monitoring and appears for check-ins, cancellation, and alert results. On small screens, an active incident moves above the other panels.

## Optional offline voice cancellation

Vosk recognizes **“I'm okay”** or **“I am okay”** locally. Install the voice extra and launch with `--voice`.

```bash
python -m pip install -e '.[voice]'
fall-detection --detector live --web --voice
```

For a Git checkout, download the offline model before starting voice mode.

```bash
curl -fL https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip -o /tmp/fall-voice-model.zip
python -m zipfile -e /tmp/fall-voice-model.zip models
```

The [small English model](https://alphacephei.com/vosk/models) is Apache-2.0 licensed. Its download SHA-256 is `30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498`. Keep the extracted directory outside Git. `--voice-model PATH` selects another local model directory. No network access is needed after installation. On macOS, the voice extra installs Vosk 0.3.44 for Apple Silicon support.

The microphone opens after successful prompt playback and a 0.4-second echo guard, only while a response is pending. Listening pauses for the spoken countdown reminder and resumes after playback and the echo guard. Wait for **Microphone active** before saying “I'm okay,” then pause briefly so recognition can finish. The focused vocabulary includes negative phrases and unknown speech. Cancellation requires a complete matching phrase or repetitions of it, with mean word confidence of at least 0.65 and each word at least 0.45. Button and keyboard cancellation remain available.

During a check-in, the voice status shows model loading, microphone activity, and failures. In the installable app, the camera controls show whether voice cancellation is enabled. Input audio and recognized text remain in memory and are discarded. Listening stops on cancellation, countdown expiry, or exit. A failed prompt leaves voice capture off. A missing model or microphone failure leaves the countdown running. Simulation mode never opens the microphone.

Use the system microphone by default, or pass `--microphone N` with a sounddevice input index. List devices with `python -m sounddevice`. On macOS, allow microphone access for Terminal or the launching app in **Privacy & Security → Microphone**. The Python process opens the microphone.

Automated checks cover response filtering, playback gating, microphone cleanup/failure, stale incident results, manual cancellation, and expiry. The installed Vosk model loads and rejects the bundled prompt as a cancellation. A live spoken “I am okay” response through the HyperX system-default microphone was accepted using the focused response vocabulary. The previous unrestricted recognizer rejected the same response at the word-confidence threshold. This is one observed successful response, not a recognition accuracy estimate.

## Send real Telegram alerts

Click **Connect Telegram** → **Open Telegram**. Tap **Start** from the Telegram account that should receive alerts, then return to the monitor and click **Finish linking**. The link expires after 10 minutes. Use **Send test message** to verify delivery to that chat. The hosted app uses the bot configured on Netlify.

While monitoring with a linked chat, the app keeps a rolling camera buffer in memory. An alert that goes out after the countdown includes up to 10 seconds before detection and 3 seconds after. The clip has no microphone audio. Cancelling during the countdown discards the clip without uploading it. If no clip is available, the app sends the alert as text.

The caregiver can tap **Help is on the way** to play a spoken acknowledgment at the monitor or **False alarm** to announce the decision and rearm it automatically. On the hosted app, the first reply within 30 minutes applies to the alert. Netlify forwards the clip in memory and stores temporary pairing, delivery, and reply records. Browsers that record WebM send the clip as a Telegram file.

### Connect Telegram on a desktop install

The local browser monitor now uses the same hosted bot as the installable app. Start it normally, click **Connect Telegram**, open the link, tap **Start** in Telegram, then click **Finish linking**. No BotFather token is needed on this computer. Alerts, silent clips, and caregiver replies use the hosted service at `https://safe-n-sound.netlify.app`.

Each local installation pairs its own caregiver chat. Its chat-scoped grant is stored privately in `.env.telegram-hosted.json`, outside Git; the shared bot token stays on Netlify. Existing personal-bot settings in `.env.telegram.json` are preserved. `SAFE_SOUND_TELEGRAM_URL` can select another HTTPS deployment of the same service. Service failures show an error and do not fall back to asking for a private token.

To use your own bot instead, launch with `fall-detection --detector live --web --private-bot`. **Connect Telegram** then accepts a BotFather token and uses `.env.telegram.json`. Use a dedicated desktop bot for local pairing and reply buttons: the shared bot's incoming updates belong to its hosted webhook. The native window keeps its existing personal-bot behavior through `fall-detection --detector live --telegram` or environment variables.

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

Alerts contain a possible-fall message and an incident ID. A rejected video upload falls back to text. `telegram_accepted_no_acknowledgement` means Telegram accepted the message. `failed` means delivery was rejected or could not be started. `uncertain` means the app could not establish whether Telegram received it. Check the linked chat after an uncertain result. Use the window to rearm manually after a delivery result.

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
