# Fall Detection Prototype

Monitor one person with a webcam. A possible fall starts an “Are you okay?” prompt and a 20-second response window. Cancel locally or let the countdown expire to attempt one caregiver alert. Video and pose landmarks stay in memory on the device. Detection and cancellation work offline after setup.

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

The bundled prompt is a local 16-bit PCM WAV. Use `--prompt PATH` for your own mono or stereo WAV up to 10 seconds. The app uses speaker output only.

## Send real Telegram alerts

In the browser, click **Connect Telegram** and use the saved bot or enter a token from BotFather. Follow **Open Telegram**, tap **Start** from the chat that should receive alerts, then return to the monitor and click **Finish linking**. Click **Send test message** to verify delivery. The link expires after 10 minutes. The app saves the bot credential and paired recipient in a private local settings file, `.env.telegram.json`, in the project directory.

The native window can reuse the linked chat with `fall-detection --detector live --telegram`. To configure the native window from environment variables instead, follow these steps.

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
