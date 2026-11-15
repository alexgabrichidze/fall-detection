import test from 'node:test';
import assert from 'node:assert/strict';
import { ActivityTracker } from './activity.js';

const shapes = {
  standing: [[.45, .2], [.45, .45], [.45, .67], [.45, .9]],
  sitting: [[.35, .2], [.35, .45], [.62, .45], [.62, .75]],
  bending: [[.65, .35], [.4, .5], [.4, .7], [.4, .9]],
  lying: [[.75, .6], [.55, .6], [.35, .6], [.15, .6]],
};
function points(shape = 'standing', cropped = false) {
  const result = new Array(33);
  for (const [indices, offset] of [[[11, 23, 25, 27], 0], [[12, 24, 26, 28], .06]]) {
    indices.forEach((index, i) => {
      if (!cropped || i < 2) result[index] = { x: shapes[shape][i][0] + offset, y: shapes[shape][i][1], visibility: .95 };
    });
  }
  return result;
}
for (const shape of Object.keys(shapes)) test(`recognizes ${shape} from complete geometry`, () => {
  const report = new ActivityTracker().update(points(shape), 640, 480, 10, 'valid', .3);
  assert.equal(report.rows[0].id, shape);
  assert(report.rows[0].score > .8);
  assert.equal(report.fallScore, .3);
});
for (const [shape, label] of [['standing', 'Upright'], ['sitting', 'Upright'], ['bending', 'Leaning'], ['lying', 'Horizontal torso']]) {
  test(`cropped ${shape} gives upper-body orientation`, () => {
    const tracker = new ActivityTracker();
    const report = tracker.update(points(shape, true), 640, 480, 10, 'valid', null);
    assert.equal(report.current, label);
    assert.equal(report.scope, 'upper');
    assert.equal(report.rows.length, 4);
    assert.equal(report.fallScore, null);
  });
}
test('real stepping evidence needs a movement sequence; static frames are not walking', () => {
  const moving = new ActivityTracker(), still = new ActivityTracker();
  for (let i = 0; i < 55; i++) {
    const body = points(), step = .14 * Math.sin(2 * Math.PI * i / 18);
    body[27].x += step; body[28].x -= step;
    moving.update(body, 640, 480, 10 + i / 30, 'valid', .3);
    still.update(points(), 640, 480, 10 + i / 30, 'valid', .3);
  }
  assert.equal(moving.report.current, 'Walking');
  assert.equal(still.report.current, 'Standing');
  assert.equal(still.report.rows.find(row => row.id === 'walking').score, 0);
});
test('pose loss, stopped capture, and stale data clear scores', () => {
  const tracker = new ActivityTracker();
  tracker.update(points(), 640, 480, 10, 'valid', .3);
  const stale = tracker.snapshot(13);
  assert.equal(stale.state, 'stale');
  assert(stale.rows.every(row => row.score === null));
  assert.equal(stale.fallScore, null);
  tracker.update([], 640, 480, 13, 'not_visible', null);
  assert(tracker.report.rows.every(row => row.score === null));
  tracker.clear('off', 'Stopped');
  assert.equal(tracker.report.state, 'off');
  assert.equal(tracker.report.fallScore, null);
});
test('cropping and resolution changes restart walking evidence', () => {
  const tracker = new ActivityTracker();
  for (let i = 0; i < 40; i++) tracker.update(points(), 640, 480, 10 + i / 30, 'valid', .3);
  assert.equal(tracker.update(points('standing', true), 640, 480, 11.4, 'valid', .3).scope, 'upper');
  const restored = tracker.update(points(), 640, 480, 11.5, 'valid', .3);
  assert.equal(restored.scope, 'full');
  assert.equal(restored.rows.find(row => row.id === 'walking').score, null);
  const resized = tracker.update(points(), 800, 480, 11.6, 'valid', .3);
  assert.equal(resized.rows.find(row => row.id === 'walking').score, null);
});
