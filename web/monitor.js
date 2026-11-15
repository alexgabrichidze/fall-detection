import { PromptAudio } from './audio.js';
import { RollingRecordings } from './recording.js';
import { Telegram } from './telegram.js';
import { ActivityTracker } from './activity.js';

const COUNTDOWN = 20000;
const ACCEPTED = 'telegram_accepted_no_acknowledgement';
const MIN_FRAME_INTERVAL = 28;
const DELIVERY_PROMPTS = {
  [ACCEPTED]: 'caregiver-notified',
  failed: 'caregiver-alert-not-sent',
  simulated_accepted: 'caregiver-alert-not-sent',
  uncertain: 'caregiver-alert-uncertain',
};

async function cameraChoices(mediaDevices) {
  const devices = await mediaDevices.enumerateDevices?.().catch(() => []) || [];
  const cameras = new Map();
  for (const device of devices) {
    const id = device.deviceId;
    if (device.kind === 'videoinput' && id && id !== 'default' && !cameras.has(id)) {
      cameras.set(id, { id, name: device.label || `Camera ${cameras.size + 1}` });
    }
  }
  return [...cameras.values()];
}

export class BrowserMonitor {
  telegram = new Telegram();
  audio = new PromptAudio();
  recordings = new RollingRecordings();
  activity = new ActivityTracker();
  view = {
    live: true, browser: true, countdown: 20, running: false, permissionPending: false,
    cameraInput: null, camera: 'stopped', pose: 'unknown', modelReady: false, hasFrame: false,
    paused: false, fault: null, incident: 'monitoring', delivery: 'none', caregiver: null,
  };

  constructor(video) {
    this.video = video;
    setInterval(() => this.tick(), 100);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && this.stream) {
        this.detector?.reset();
        this.keepAwake();
      }
    });
  }

  async request(path, body) {
    if (path === '/api/status') {
      return { ...this.view, activity: this.activity.snapshot(performance.now() / 1000), telegram: this.telegram.status,
        audio: this.audio.status, voice: this.voice?.status || 'Disabled',
        voiceEnabled: Boolean(this.voice?.enabled), listening: this.voice?.listening || false,
        voiceIssue: this.voiceIssue,
        remaining: this.view.incident === 'awaiting_response' ? Math.max(0, this.deadline - Date.now()) / 1000 : null };
    }
    if (path === '/api/cameras') return this.cameraInputs();
    if (path.startsWith('/api/telegram/')) {
      const result = await this.telegram.action(path.split('/').at(-1));
      this.syncRecording();
      return result;
    }
    if (path === '/api/command') {
      if (body.command === 'start') return this.start(body.cameraId);
      if (body.command === 'stop') this.stop();
      if (body.command === 'pause') this.pause();
      if (body.command === 'cancel') this.cancel();
      if (body.command === 'reset') this.reset();
      return {};
    }
    throw new Error('Unknown monitor action.');
  }

  async cameraInputs() {
    const cameras = navigator.mediaDevices ? await cameraChoices(navigator.mediaDevices) : [];
    return { cameras, selected: this.view.cameraInput };
  }

  async enableVoice() {
    if (this.voice?.enabled || this.voiceBusy) return;
    this.voiceBusy = true;
    this.voiceIssue = null;
    try {
      const { Voice } = await import('./voice.js');
      if (!this.stream) return;
      this.voice ??= new Voice();
      await this.voice.enable(this.audio.context);
    } catch (error) {
      this.voiceIssue = error.name === 'NotAllowedError'
        ? 'Allow microphone access in your browser settings to cancel by voice.'
        : 'Voice cancellation is unavailable. Use the on-screen button to cancel.';
    } finally {
      this.voiceBusy = false;
    }
  }

  async start(cameraId) {
    this.stop();
    this.view.fault = null;
    this.view.permissionPending = true;
    this.view.camera = 'starting';
    this.audio.unlock();
    let stream;
    try {
      const mediaDevices = navigator.mediaDevices;
      if (!mediaDevices?.getUserMedia) throw new Error('Open this site in a browser with HTTPS camera access.');
      if (!cameraId) {
        const cameras = await cameraChoices(mediaDevices);
        if (cameras.length > 1) {
          this.view.camera = 'stopped';
          return { cameras };
        }
      }
      const source = cameraId ? { deviceId: { exact: cameraId } } : { facingMode: { ideal: 'user' } };
      stream = await mediaDevices.getUserMedia({
        video: { ...source, width: { ideal: 640, max: 960 }, height: { ideal: 480, max: 960 }, frameRate: { ideal: 30 } },
        audio: false,
      });
      // Some browsers reveal the full list only after granting camera access.
      if (!cameraId) {
        const cameras = await cameraChoices(mediaDevices);
        if (cameras.length > 1) {
          stream.getTracks().forEach(track => track.stop());
          this.view.camera = 'stopped';
          return { cameras };
        }
      }
      this.stream = stream;
      this.video.srcObject = stream;
      await this.video.play();
    } catch (error) {
      if (stream && !this.stream) stream.getTracks().forEach(track => track.stop());
      this.stop();
      this.view.fault = {
        NotAllowedError: 'camera_permission_denied', NotFoundError: 'browser_camera_missing',
      }[error.name] || 'browser_camera_unavailable';
      return;
    } finally {
      this.view.permissionPending = false;
    }
    this.view.cameraInput = stream.getVideoTracks()[0].getSettings?.().deviceId || cameraId || null;
    this.view.running = true;
    this.view.hasFrame = true;
    stream.getVideoTracks()[0].addEventListener('ended', () => {
      if (this.stream !== stream) return;
      this.stop();
      this.view.fault = 'browser_camera_unavailable';
    });
    this.keepAwake();
    this.enableVoice();
    this.syncRecording();
    try {
      this.detector ??= await import('./detector.js').then(module => module.loadDetector());
      if (this.stream !== stream) return;
      this.detector.reset();
      this.view.camera = 'available';
      this.runCamera(stream);
    } catch {
      this.stop();
      this.view.fault = 'browser_model_unavailable';
    }
  }

  runCamera(stream) {
    let lastFrame = -Infinity;
    const schedule = this.video.requestVideoFrameCallback
      ? callback => this.video.requestVideoFrameCallback(callback)
      : callback => requestAnimationFrame(callback);
    const frame = async time => {
      if (this.stream !== stream) return;
      if (!this.view.paused && !document.hidden && time - lastFrame >= MIN_FRAME_INTERVAL && this.video.readyState >= 2) {
        lastFrame = time;
        try {
          const result = await this.detector.sample(this.video, time);
          if (this.stream !== stream) return;
          if (!this.view.paused) this.activity.update(result.points, this.video.videoWidth, this.video.videoHeight, time / 1000, result.status, result.score);
          this.view.pose = result.status;
          this.view.modelReady = result.score !== null;
          if (result.fall && !this.view.paused && this.view.incident === 'monitoring') this.fall();
        } catch {
          this.stop();
          this.view.fault = 'browser_model_unavailable';
          return;
        }
      }
      if (this.stream === stream) schedule(frame);
    };
    schedule(frame);
  }

  stop() {
    this.activity.clear('off', 'Start the camera to see live activity.');
    this.recordings.stop();
    if (this.stream) this.stream.getTracks().forEach(track => track.stop());
    this.stream = this.video.srcObject = null;
    this.wakeLock?.release().catch(() => {});
    this.wakeLock = null;
    Object.assign(this.view, { running: false, paused: false, hasFrame: false, camera: 'stopped',
      pose: 'unknown', modelReady: false });
  }

  async keepAwake() {
    try {
      if (!this.wakeLock || this.wakeLock.released) {
        const stream = this.stream;
        const lock = await navigator.wakeLock?.request('screen');
        if (this.stream !== stream) await lock?.release();
        else this.wakeLock = lock;
      }
    } catch {
      // Some installed browsers do not support the screen wake lock.
    }
  }

  pause() {
    if (!this.stream || !this.detector) return;
    this.view.paused = !this.view.paused;
    this.view.hasFrame = !this.view.paused;
    this.detector.reset();
    this.activity.clear(this.view.paused ? 'paused' : 'unknown', this.view.paused ? 'Resume monitoring to see live activity.' : 'Collecting fresh movement.');
    this.syncRecording();
  }

  syncRecording() {
    if (this.stream && this.telegram.status.linked && !this.view.paused) {
      this.recordings.start(this.stream);
    } else {
      if (!this.telegram.status.linked) this.recordings.discard();
      this.recordings.stop();
    }
  }

  fall() {
    if (this.view.incident !== 'monitoring') return;
    this.id = crypto.randomUUID();
    this.deadline = null;
    this.warningDeadline = Date.now() + 12000;
    this.reminded = false;
    this.view.caregiver = null;
    this.view.incident = 'announcing';
    this.recordings.mark();
    this.prompt(this.voice?.enabled ? 'fall-warning-voice-20' : 'fall-warning-20', this.id, true);
  }

  beginResponse(incident) {
    if (this.id !== incident || this.view.incident !== 'announcing') return false;
    this.deadline = Date.now() + COUNTDOWN;
    this.warningDeadline = null;
    this.view.incident = 'awaiting_response';
    return true;
  }

  async prompt(name, incident, opening = false) {
    this.voice?.stop();
    let played = false;
    try { played = await this.audio.play(name); } catch { /* Start the window if audio fails. */ }
    if (opening && !this.beginResponse(incident)) return;
    if (played && this.id === incident && this.view.incident === 'awaiting_response' && Date.now() < this.deadline) {
      this.voice?.listen(() => this.cancel());
    }
  }

  cancel() {
    if (!['announcing', 'awaiting_response'].includes(this.view.incident)
        || (this.view.incident === 'awaiting_response' && Date.now() >= this.deadline)) return;
    this.voice?.stop();
    this.recordings.discard();
    this.view.incident = 'cancelled';
    this.audio.play('alarm-cancelled');
  }

  reset() {
    if (this.view.incident !== 'cancelled'
        && (this.view.incident !== 'alerting' || this.view.delivery === 'pending')) return;
    this.id = null;
    this.deadline = this.warningDeadline = null;
    this.voice?.stop();
    this.audio.cancel();
    this.audio.status = 'not requested';
    this.detector?.reset();
    this.recordings.discard();
    Object.assign(this.view, { incident: 'monitoring', delivery: 'none', caregiver: null });
  }

  async sendAlert(id) {
    this.view.incident = 'alerting';
    this.view.delivery = 'pending';
    this.audio.cancel();
    this.voice?.stop();
    const delivery = await this.telegram.alert(id, await this.recordings.take());
    if (this.id !== id) return;
    this.view.delivery = delivery;
    if (DELIVERY_PROMPTS[delivery]) this.audio.play(DELIVERY_PROMPTS[delivery]);
    this.watchUntil = Date.now() + 30 * 60 * 1000;
    this.nextReply = Date.now();
  }

  tick() {
    const now = Date.now();
    if (this.view.incident === 'announcing') {
      if (now >= this.warningDeadline) {
        this.audio.cancel();
        this.beginResponse(this.id);
      }
    } else if (this.view.incident === 'awaiting_response') {
      if (now >= this.deadline) this.sendAlert(this.id);
      else if (!this.reminded && now >= this.deadline - 10000) {
        this.reminded = true;
        this.prompt('ten-seconds-left', this.id);
      }
    } else if (this.view.incident === 'alerting' && !this.view.caregiver
        && [ACCEPTED, 'uncertain'].includes(this.view.delivery) && now >= this.nextReply
        && now < this.watchUntil && !this.replyPending) {
      this.checkReply(this.id);
    }
  }

  async checkReply(id) {
    this.replyPending = true;
    this.nextReply = Date.now() + 2000;
    try {
      const reply = await this.telegram.reply(id);
      if (this.id !== id) return;
      if (reply === 'false_alarm') {
        this.reset();
        this.view.caregiver = reply;
        this.audio.play('caregiver-false-alarm');
      } else if (reply === 'help') {
        this.view.caregiver = reply;
        this.audio.play('caregiver-help-on-the-way');
      }
    } catch {
      // Polling resumes once the connection is available again.
    } finally {
      this.replyPending = false;
    }
  }
}
