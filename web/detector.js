import { FilesetResolver, PoseLandmarker } from '@mediapipe/tasks-vision';
import { loadLiteRt, loadAndCompile, Tensor } from '@litertjs/core';

// Match the training notebook's landmark order, including its face index mapping.
const indices = [27, 3, 13, 1, 23, 25, 11, 15, 0, 28, 4, 14, 2, 24, 26, 12, 16];

function center(points, first, second) {
  const usable = [points[first], points[second]].filter(point => point.visibility > 0.3);
  if (!usable.length) return null;
  return [
    usable.reduce((total, point) => total + point.x, 0) / usable.length,
    usable.reduce((total, point) => total + point.y, 0) / usable.length,
  ];
}

export function preparePose(points, width, height) {
  if (!points?.length) return null;
  const shoulder = center(points, 11, 12);
  const hip = center(points, 23, 24);
  if (!shoulder || !hip || [...shoulder, ...hip].some(value => value < 0 || value > 1)) return null;
  const dx = (hip[0] - shoulder[0]) * width;
  const dy = (hip[1] - shoulder[1]) * height;
  const length = Math.hypot(dx, dy);
  if (length < 1) return null;
  const scale = Math.abs(dy) >= 1e-5 ? Math.abs(dy) : 1;
  const features = indices.flatMap(index => {
    const point = points[index];
    return [(point.x - hip[0]) * width / scale, (point.y - hip[1]) * height / scale, point.visibility];
  });
  if (!features.every(Number.isFinite)) return null;
  return { features, centerY: (shoulder[1] + hip[1]) / 2, upright: dy / length };
}

export async function loadDetector() {
  const [pose, model] = await Promise.all([
    FilesetResolver.forVisionTasks('/runtime/pose').then(files => PoseLandmarker.createFromOptions(files, {
      baseOptions: { modelAssetPath: '/models/pose.task', delegate: 'CPU' },
      runningMode: 'VIDEO',
      numPoses: 1,
    })),
    (async () => {
      await loadLiteRt('/runtime/litert/');
      return loadAndCompile('/models/fall.tflite', { accelerator: 'wasm' });
    })(),
  ]);
  let sequence = [];
  let upright = [];
  let candidate = null;
  let reported = false;

  function clear() {
    sequence = [];
    upright = [];
    candidate = null;
  }

  return {
    reset() {
      clear();
      reported = false;
    },
    async sample(video, time) {
      const points = pose.detectForVideo(video, time).landmarks[0];
      const sample = preparePose(points, video.videoWidth, video.videoHeight);
      const status = sample ? 'valid' : points ? 'unreliable' : 'not_visible';
      if (sequence.length && time - sequence.at(-1).time > 320) clear();
      if (!sample) {
        clear();
        return { status, points, score: null, fall: false };
      }
      sequence.push({ time, features: sample.features });
      if (sequence.length > 30) sequence.shift();
      upright = upright.filter(entry => time - entry.time <= 2500);
      if (sample.upright >= 0.86) upright.push({ time, y: sample.centerY });
      if (sequence.length < 30 || time - sequence[0].time > 2600) {
        candidate = null;
        return { status, points, score: null, fall: false };
      }
      const input = new Tensor(new Float32Array(sequence.flatMap(entry => entry.features)), [1, 30, 51]);
      let output;
      let score;
      try {
        output = await model.run([input]);
        score = (await output[0].data())[0];
      } finally {
        input.delete();
        output?.forEach(tensor => tensor.delete());
      }
      if (!Number.isFinite(score)) throw new Error('The fall model returned an invalid score.');
      if (score < 0.5 && sample.upright >= 0.86) reported = false;
      const drop = sample.centerY - Math.min(sample.centerY, ...upright.map(entry => entry.y));
      const low = drop >= 0.10 && (sample.upright < 0.80 || drop >= 0.22);
      if (reported || score < 0.9 || !low) {
        candidate = null;
        return { status, points, score, fall: false };
      }
      candidate ??= time;
      const fall = time - candidate >= 180;
      if (fall) reported = true;
      return { status, points, score, fall };
    },
  };
}
