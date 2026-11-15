// Browser counterpart of the desktop's display-only landmark estimator.
// Scores support geometric rules; they are not model class probabilities.
const LABELS = [['standing', 'Standing'], ['walking', 'Walking'], ['sitting', 'Sitting'],
  ['bending', 'Bending'], ['lying', 'Lying / horizontal'], ['unclear', 'Unclear / other']];
const UPPER = [['upright', 'Upright'], ['leaning', 'Leaning'],
  ['horizontal', 'Horizontal torso'], ['unclear', 'Unclear / other']];
const ramp = (value, low, high) => Math.max(0, Math.min(1, (value - low) / (high - low)));
const length = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const quality = side => Math.min(...side.map(p => p[2]));
const bestSide = sides => sides.sort((a, b) => quality(b) - quality(a))[0];
function angle(a, b, c) {
  const divisor = length(a, b) * length(c, b);
  return divisor ? Math.acos(Math.max(-1, Math.min(1,
    ((a[0] - b[0]) * (c[0] - b[0]) + (a[1] - b[1]) * (c[1] - b[1])) / divisor))) * 180 / Math.PI : null;
}

export class ActivityTracker {
  constructor() { this.clear('off', 'Start the camera to see live activity.'); }

  clear(state, detail) {
    this.history = [];
    this.smoothed = {};
    this.timestamp = null;
    this.size = null;
    this.scope = null;
    this.report = { state, scope: 'full', current: 'Unclear', detail,
      rows: LABELS.map(([id, label]) => ({ id, label, score: null })), trackingQuality: null, fallScore: null };
    return this.report;
  }

  snapshot(now) {
    if (['live', 'partial'].includes(this.report.state) && !(now >= this.timestamp && now - this.timestamp <= 2)) {
      return { state: 'stale', scope: 'full', current: 'Unclear', detail: 'Waiting for a fresh frame.',
        rows: LABELS.map(([id, label]) => ({ id, label, score: null })), trackingQuality: null, fallScore: null };
    }
    return this.report;
  }

  update(landmarks, width, height, timestamp, status, fallScore) {
    if (status !== 'valid' || !width || !height) {
      return this.clear('unknown', 'Keep shoulders and hips in view for posture estimates.');
    }
    const size = `${width}:${height}`;
    if (this.timestamp !== null && (timestamp < this.timestamp || timestamp - this.timestamp > .3 || size !== this.size)) {
      this.clear('unknown', 'Collecting fresh movement.');
    }
    if (timestamp === this.timestamp) return this.report;
    const previous = this.timestamp;
    this.timestamp = timestamp;
    this.size = size;
    const points = {};
    landmarks?.forEach((p, i) => {
      if (p && [p.x, p.y, p.visibility].every(Number.isFinite)
          && p.x >= 0 && p.x <= 1 && p.y >= 0 && p.y <= 1 && p.visibility >= .55 && p.visibility <= 1) {
        points[i] = [p.x * width, p.y * height, p.visibility];
      }
    });
    const complete = [[11, 23, 25, 27], [12, 24, 26, 28]].map(side => side.map(i => points[i])).filter(side => side.every(Boolean));
    const fall = Number.isFinite(fallScore) ? fallScore : null;
    if (!complete.length) return this.upper(points, timestamp, previous, fall);
    const [shoulder, hip, knee, ankle] = bestSide(complete);
    const torso = length(shoulder, hip), thigh = length(hip, knee), shin = length(knee, ankle);
    const kneeAngle = angle(hip, knee, ankle);
    if (torso < 8 || Math.min(thigh, shin) < torso * .25 || kneeAngle === null || hip[1] <= shoulder[1] - torso * .3) {
      return this.upper(points, timestamp, previous, fall);
    }
    const tilt = Math.atan2(Math.abs(hip[0] - shoulder[0]), hip[1] - shoulder[1]) * 180 / Math.PI;
    const thighDown = (knee[1] - hip[1]) / thigh, shinDown = (ankle[1] - knee[1]) / shin;
    const upright = 1 - ramp(tilt, 15, 45), straight = ramp(kneeAngle, 140, 170);
    const legsDown = Math.min(ramp(thighDown, .35, .85), ramp(shinDown, .35, .85));
    const bent = Math.min(ramp(kneeAngle, 35, 70), 1 - ramp(kneeAngle, 115, 150));
    const bodyLength = length(shoulder, ankle);
    const bodyHorizontal = bodyLength ? ramp(Math.abs(ankle[0] - shoulder[0]) / bodyLength, .70, .94) : 0;
    const horizontal = Math.min(ramp(tilt, 50, 80), 1 - ramp(tilt, 105, 130));
    const q = quality([shoulder, hip, knee, ankle]);
    const gait = this.gait(points, timestamp, torso);
    const scores = {
      standing: Math.min(upright, straight, legsDown) * q,
      sitting: Math.min(upright, bent, 1 - ramp(Math.abs(thighDown), .25, .75), ramp(shinDown, .35, .85)) * q,
      bending: Math.min(ramp(tilt, 25, 60), straight, legsDown) * q,
      lying: Math.min(horizontal, bodyHorizontal) * q,
      walking: gait === null ? null : Math.min(upright, gait) * q,
    };
    if (scores.walking !== null) scores.standing *= 1 - scores.walking;
    return this.finish(scores, timestamp, previous, q, fall, 'full');
  }

  upper(points, timestamp, previous, fall) {
    this.history = [];
    const sides = [[11, 23], [12, 24]].map(side => side.map(i => points[i])).filter(side => side.every(Boolean));
    if (sides.length) {
      const [shoulder, hip] = bestSide(sides), torso = length(shoulder, hip);
      if (torso >= 8 && hip[1] >= shoulder[1] - torso * .3) {
        const tilt = Math.atan2(Math.abs(hip[0] - shoulder[0]), hip[1] - shoulder[1]) * 180 / Math.PI;
        const q = quality([shoulder, hip]);
        return this.finish({
          upright: (1 - ramp(tilt, 15, 45)) * q,
          leaning: Math.min(ramp(tilt, 15, 45), 1 - ramp(tilt, 65, 85)) * q,
          horizontal: Math.min(ramp(tilt, 55, 80), 1 - ramp(tilt, 105, 130)) * q,
        }, timestamp, previous, q, fall, 'upper');
      }
    }
    this.smoothed = {};
    this.scope = null;
    this.report = { state: 'partial', scope: 'full', current: 'Unclear',
      detail: 'Show shoulders and hips to estimate upper-body posture.',
      rows: LABELS.map(([id, label]) => ({ id, label, score: null })), trackingQuality: null, fallScore: fall };
    return this.report;
  }

  finish(scores, timestamp, previous, q, fall, scope) {
    if (this.scope !== scope) this.smoothed = {};
    this.scope = scope;
    const alpha = previous === null ? 1 : 1 - Math.exp(-(timestamp - previous) / .20);
    for (const [key, value] of Object.entries(scores)) {
      if (value === null) delete this.smoothed[key];
      else this.smoothed[key] = (this.smoothed[key] ?? value) * (1 - alpha) + value * alpha;
    }
    const values = Object.fromEntries(Object.keys(scores).map(key => [key, this.smoothed[key] ?? null]));
    const best = Object.keys(values).filter(key => values[key] !== null).sort((a, b) => values[b] - values[a])[0];
    values.unclear = 1 - values[best];
    const labels = scope === 'upper' ? UPPER : LABELS;
    this.report = { state: scope === 'upper' ? 'partial' : 'live', scope,
      current: values[best] >= .5 ? labels.find(([id]) => id === best)[1] : 'Unclear',
      detail: scope === 'upper' ? 'Upper body only · no leg visibility needed.' : 'Live posture estimates · scores out of 100.',
      rows: labels.map(([id, label]) => ({ id, label, score: values[id] ?? null }))
        .sort((a, b) => (b.score ?? -1) - (a.score ?? -1)), trackingQuality: q, fallScore: fall };
    return this.report;
  }

  gait(points, timestamp, torso) {
    if (!points[27] || !points[28]) { this.history = []; return null; }
    this.history.push([timestamp, (points[27][0] - points[28][0]) / torso, (points[27][1] - points[28][1]) / torso]);
    this.history = this.history.filter(row => timestamp - row[0] <= 1.8).slice(-120);
    if (this.history.length < 8 || timestamp - this.history[0][0] < .8) return null;
    const support = (axis, deadband, low, high) => {
      let sign = 0, changes = 0, lastChange = -Infinity;
      for (const row of this.history) {
        const value = row[axis + 1], current = value > deadband ? 1 : value < -deadband ? -1 : 0;
        if (current && current !== sign && row[0] - lastChange >= .15) {
          if (sign) changes++;
          sign = current; lastChange = row[0];
        }
      }
      const values = this.history.map(row => row[axis + 1]);
      return Math.min(changes / 2, 1) * ramp(Math.max(...values) - Math.min(...values), low, high);
    };
    return Math.max(support(0, .10, .3, .8), support(1, .07, .15, .4));
  }
}
