import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = {};
globalThis.document = { addEventListener() {}, hidden: false };
const { BrowserMonitor } = await import('./monitor.js');

function setup(t) {
  const realNow = Date.now, realInterval = globalThis.setInterval;
  const clock = { now: 10000 };
  Date.now = () => clock.now;
  globalThis.setInterval = () => 0;
  let app;
  try { app = new BrowserMonitor({}); } finally { globalThis.setInterval = realInterval; }
  t.after(() => { Date.now = realNow; });
  let resolve, reject;
  const completed = new Promise((yes, no) => { resolve = yes; reject = no; });
  const calls = { alerts: 0, cancels: 0, listens: 0, plays: [] };
  app.audio = { status: 'playing', play: name => { calls.plays.push(name); return completed; }, cancel: () => { calls.cancels++; } };
  app.voice = { enabled: true, status: 'ready', stop() {}, listen: () => { calls.listens++; } };
  app.recordings = { mark() {}, discard() {}, take: async () => null };
  app.telegram = { status: {}, alert: async () => { calls.alerts++; return 'failed'; } };
  return { app, clock, completed: { resolve, reject }, calls };
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

test('six seconds of warning are followed by twenty full response seconds', async t => {
  const { app, clock, completed, calls } = setup(t);
  app.fall();
  assert.equal(app.view.incident, 'announcing');
  assert.equal(app.deadline, null);
  clock.now = 16000; app.tick();
  assert.equal((await app.request('/api/status')).remaining, null);
  assert.equal(calls.alerts, 0);
  completed.resolve(true); await flush();
  assert.equal(app.view.incident, 'awaiting_response');
  assert.equal((await app.request('/api/status')).remaining, 20);
  assert.equal(app.deadline, 36000);
  assert.equal(calls.listens, 1);
  clock.now = 35999; app.tick();
  assert.equal(calls.alerts, 0);
  clock.now = 36000; app.tick(); await flush();
  assert.equal(calls.alerts, 1);
  app.tick(); assert.equal(calls.alerts, 1);
});

test('cancellation during warning prevents a late completion starting the timer', async t => {
  const { app, clock, completed, calls } = setup(t);
  app.fall(); clock.now = 16000; app.cancel();
  assert.equal(app.view.incident, 'cancelled');
  completed.resolve(true); await flush();
  assert.equal(app.view.incident, 'cancelled');
  assert.equal(app.deadline, null);
  assert.equal(calls.listens, 0);
  clock.now = 100000; app.tick(); assert.equal(calls.alerts, 0);
});

test('failed opening audio still starts a full response window', async t => {
  const { app, clock, completed, calls } = setup(t);
  app.fall(); clock.now = 13000;
  completed.reject(new Error('output unavailable')); await flush();
  assert.equal((await app.request('/api/status')).remaining, 20);
  assert.equal(app.deadline, 33000);
  assert.equal(calls.listens, 0);
});

test('stalled audio is cancelled before the fallback timer starts', async t => {
  const { app, clock, completed, calls } = setup(t);
  app.fall(); clock.now = 22000; app.tick();
  assert.equal(calls.cancels, 1);
  assert.equal((await app.request('/api/status')).remaining, 20);
  const deadline = app.deadline;
  clock.now = 24000; completed.resolve(true); await flush();
  assert.equal(app.deadline, deadline);
  assert.equal(calls.listens, 0);
});

test('reminder completion and repeated falls do not extend an active countdown', async t => {
  const { app, clock, completed, calls } = setup(t);
  app.fall(); clock.now = 16000; completed.resolve(true); await flush();
  const deadline = app.deadline, id = app.id;
  app.fall(); assert.equal(app.id, id);
  clock.now = 25999; app.tick(); assert(!calls.plays.includes('ten-seconds-left'));
  clock.now = 26000; app.tick(); await flush();
  assert(calls.plays.includes('ten-seconds-left'));
  assert.equal(app.deadline, deadline);
});
