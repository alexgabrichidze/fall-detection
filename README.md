# Safe&Sound

Safe&Sound uses a phone or computer camera to detect possible falls and alert a caregiver. Fall detection and voice recognition run on the device. The live activity panel shows posture estimates and the fall model score.

[Open the live demo](https://safe-n-sound.netlify.app) · [Watch the demo video](https://youtu.be/wLLTdW43fl8)

## Try it

1. Tap **Start camera** and allow camera and microphone access. Choose a camera if asked.
2. Select **Connect Telegram**. Tap **Start** in the bot chat, then return to Safe&Sound and select **Finish linking**. Use **Send test message** to confirm the connection.
3. Keep the app open and the person in view. After a possible fall, tap **I'm okay** during the warning or countdown, or say **“I'm okay”** after the spoken prompt.

The full 20-second countdown starts after the opening warning finishes. If the countdown expires, the caregiver gets a Telegram alert with a short, silent clip when available. **Help is on the way** plays an announcement. **False alarm** clears the incident and resumes monitoring.

On a phone, use your browser’s **Add to Home Screen** or **Install app** menu to install Safe&Sound.

## Run locally

Use Python 3.12 and download the [fall model](models/README.md) for live monitoring.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[detection,audio]'
fall-detection --detector live --web
```

Open the [local monitor](http://127.0.0.1:8765). The desktop version uses the same hosted Telegram bot through **Connect Telegram**, without a local bot token. Use `--private-bot` to connect your own bot instead; existing personal-bot settings are preserved. For local voice cancellation, install the `voice` extra, extract the [small English Vosk model](https://alphacephei.com/vosk/models) into `models/`, and add `--voice`.

For a camera-free demo, run `fall-detection --detector fake --web --scenario idle` and click **Simulate a fall**. Alerts are simulated.

## Deploy on Netlify

```sh
npm ci --prefix web
npm run --prefix web build
```

Netlify publishes `web/dist` and `web/functions` using `netlify.toml`. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_BOT_USERNAME` in Netlify’s environment settings. With the same token set locally, register the webhook.

```sh
npm run --prefix web telegram:webhook -- https://your-site.netlify.app
```

Safe&Sound uses MediaPipe Pose, LiteRT, Vosk, and a [pretrained fall classifier](models/README.md).
