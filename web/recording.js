// Overlapping recordings keep the fall in one complete playable container.
const MIME_TYPES = ['video/mp4;codecs=avc1', 'video/mp4', 'video/webm;codecs=vp8', 'video/webm'];
const MAX_BYTES = 4 * 1024 * 1024;

export class RollingRecordings {
  active = new Set();
  selected = null;
  interval = null;

  start(stream) {
    if (this.interval || typeof MediaRecorder === 'undefined') return;
    const mimeType = MIME_TYPES.find(type => MediaRecorder.isTypeSupported(type));
    if (!mimeType) return;
    const begin = () => {
      let recorder;
      try {
        recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 520000 });
        recorder.start(1000);
      } catch {
        return;
      }
      const parts = [];
      let size = 0;
      let done;
      const entry = {
        start: performance.now(),
        valid: true,
        clip: new Promise(resolve => { done = resolve; }),
        stop: () => {
          clearTimeout(entry.timer);
          if (recorder.state !== 'inactive') recorder.stop();
        },
      };
      this.active.add(entry);
      entry.timer = setTimeout(entry.stop, 13000);
      recorder.ondataavailable = event => {
        size += event.data.size;
        if (size > MAX_BYTES) {
          entry.valid = false;
          entry.stop();
        } else if (entry.valid && event.data.size) {
          parts.push(event.data);
        }
      };
      recorder.onerror = () => {
        entry.valid = false;
        entry.stop();
      };
      recorder.onstop = () => {
        this.active.delete(entry);
        done(entry.valid && parts.length ? new Blob(parts, { type: recorder.mimeType || mimeType }) : null);
      };
    };
    begin();
    this.interval = setInterval(begin, 5000);
  }

  mark() {
    const time = performance.now();
    this.selected = [...this.active].find(entry => time - entry.start <= 10000) || null;
    if (!this.selected) return;
    clearTimeout(this.selected.timer);
    this.selected.timer = setTimeout(this.selected.stop, 3000);
  }

  take() {
    const entry = this.selected;
    this.selected = null;
    entry?.stop();
    return entry?.clip || Promise.resolve(null);
  }

  discard() {
    if (!this.selected) return;
    this.selected.valid = false;
    this.selected.stop();
    this.selected = null;
  }

  stop() {
    clearInterval(this.interval);
    this.interval = null;
    for (const entry of this.active) {
      if (entry !== this.selected) entry.valid = false;
      entry.stop();
    }
  }
}
