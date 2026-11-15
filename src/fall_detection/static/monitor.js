const element = (id) => document.getElementById(id);
const text = (id, value) => {
  const node = element(id);
  if (node.textContent !== value) node.textContent = value;
};
const visible = (id, show) => { element(id).hidden = !show; };

let state = null;
let monitorBusy = false;
let telegramBusy = false;
let cameraListBusy = false;
let cameraListReady = false;
let camerasRequested = false;
let frameUrl;
const dialog = element('link-dialog');

async function request(path, body) {
  const options = body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The monitor could not complete the request.');
  return result;
}

function feedback(id, message, error = false) {
  text(id, message);
  visible(id, Boolean(message));
  element(id).dataset.tone = error ? 'error' : 'neutral';
}

function showFrame(show) {
  visible('preview', show);
  visible('camera-placeholder', !show);
  element('stage').dataset.playing = show;
}

function clearFrame() {
  if (frameUrl) {
    URL.revokeObjectURL(frameUrl);
    element('preview').removeAttribute('src');
    frameUrl = undefined;
  }
  showFrame(false);
}

function cameraState(view) {
  if (!view.live) return ['Demo', 'Camera is off in demo mode', 'Use “Simulate a fall” to try the response countdown.'];
  if (view.permissionPending) return ['Permission', 'Allow camera access', 'Respond to the camera prompt from your computer.'];
  if (view.fault === 'camera_permission_denied') {
    return ['Access off', 'Camera access is off', 'In System Settings → Privacy & Security → Camera, allow the app that launched Fall Monitor, then try again.'];
  }
  if (view.fault === 'camera_permission_dependency') {
    return ['Unavailable', 'Camera support is missing', 'Install the live dependencies from the project README.'];
  }
  if (view.fault) return ['Unavailable', 'Camera preview unavailable', 'Check the camera connection and try starting it again.'];
  if (view.paused) return ['Paused', 'Monitoring is paused', 'Resume monitoring to see the live preview.'];
  if (view.camera === 'available') return ['On', 'Camera is on', 'Waiting for a frame.'];
  if (view.running) return ['Starting', 'Starting the camera', 'The preview will appear when the camera is ready.'];
  return ['Off', 'Camera is off', 'Start the camera to begin monitoring.'];
}

function renderCamera(view) {
  const [label, title, detail] = cameraState(view);
  text('camera-chip', label);
  text('camera-status', label);
  text('camera-message', title);
  text('camera-detail', detail);
  element('camera-chip').dataset.tone = view.fault ? 'warning' : label === 'On' ? 'steady' : 'neutral';
  const tracking = { valid: view.modelReady ? 'Tracking' : 'Warming up', unreliable: 'Unclear view', not_visible: 'Person out of view' };
  text('pose-status', view.paused ? 'Paused' : tracking[view.pose] || 'Waiting');

  const canStart = view.live && (!view.running || view.fault);
  const toggle = canStart ? view.fault ? 'Retry camera' : 'Start camera' : view.paused ? 'Resume monitoring' : 'Pause monitoring';
  text('camera-action-label', view.permissionPending ? 'Waiting for access' : toggle);
  element('camera-action-icon').classList.toggle('paused-shape', Boolean(!canStart && !view.paused));
  element('camera-action').disabled = monitorBusy || view.permissionPending || (!view.live && Boolean(view.fault));
  visible('stop-camera', view.live && view.running && !view.permissionPending);
  element('stop-camera').disabled = monitorBusy;
  visible('camera-input-controls', view.live);
  const input = element('camera-input');
  input.disabled = monitorBusy || cameraListBusy || !cameraListReady || view.running || view.permissionPending;
  element('refresh-cameras').disabled = monitorBusy || cameraListBusy || view.running || view.permissionPending;
  text('camera-input-help', view.running || view.permissionPending
    ? 'Stop the camera to change the input. An active incident countdown will continue.'
    : 'Choose a camera before starting. Refresh after connecting another camera.');
  if (cameraListReady && !monitorBusy && document.activeElement !== input) {
    const selected = String(view.cameraInput);
    if (!Array.from(input.options).some(option => option.value === selected)) {
      input.add(new Option(`Camera ${view.cameraInput}`, selected));
    }
    input.value = selected;
  }
  visible('simulate', !view.live);
  element('simulate').disabled = monitorBusy || view.paused || view.incident !== 'monitoring';
  if (!view.hasFrame) clearFrame();
}

function renderIncident(view) {
  const awaiting = view.remaining !== null;
  let title = view.running && !view.paused && !view.fault ? 'Monitoring' : 'Ready to monitor';
  let detail = 'The response countdown will appear here.';
  let tone = 'steady';
  if (awaiting) {
    title = 'Possible fall detected';
    detail = 'If you’re okay, cancel before the countdown ends.';
    tone = 'warning';
  } else if (view.incident === 'cancelled') {
    title = 'Incident cancelled';
    detail = 'Rearm the monitor for the next incident.';
  } else if (view.incident === 'alerting') {
    const outcomes = {
      pending: ['Processing alert', 'Waiting for the delivery result.', 'warning'],
      telegram_accepted_no_acknowledgement: ['Alert sent', 'Telegram accepted the fall alert.', 'steady'],
      simulated_accepted: ['Dry run complete', 'The countdown finished and delivery was simulated.', 'steady'],
      failed: ['Alert failed', 'Delivery was rejected or could not start.', 'danger'],
      uncertain: ['Delivery unconfirmed', 'Check the linked chat for the alert.', 'warning'],
    };
    [title, detail, tone] = outcomes[view.delivery] || outcomes.pending;
  }
  text('incident-status', title);
  text('incident-detail', detail);
  element('incident-panel').dataset.tone = tone;
  element('dashboard').dataset.urgent = awaiting;
  visible('countdown-panel', awaiting);
  visible('cancel', awaiting);
  element('cancel').disabled = monitorBusy;
  visible('rearm', view.incident === 'cancelled' || (view.incident === 'alerting' && view.delivery !== 'pending'));
  element('rearm').disabled = monitorBusy;
  if (awaiting) {
    text('time-remaining', String(Math.ceil(view.remaining)));
    element('time-progress').max = view.countdown;
    element('time-progress').value = view.remaining;
  }
  text('window-length', `${view.countdown} seconds`);
  const audio = { 'not requested': 'Ready', playing: 'Playing', played: 'Played', cancelled: 'Stopped' };
  text('audio-status', audio[view.audio] || 'Unavailable');
  text('voice-status', view.listening ? `Microphone active — ${view.voice}` : (view.voice || 'Disabled'));
}

function renderTelegram(view) {
  const telegram = view.telegram;
  const linked = Boolean(telegram?.linked);
  text('telegram-chip', linked ? 'Linked' : view.live ? 'Not linked' : 'Demo');
  element('telegram-chip').dataset.tone = linked ? 'steady' : 'neutral';
  const bot = telegram?.bot ? ` via @${telegram.bot}` : '';
  if (linked) {
    text('telegram-description', `Alerts go to ${telegram.recipient || 'your Telegram chat'}${bot}.`);
  } else {
    text('telegram-description', view.live ? 'Link one chat to receive alerts when the countdown expires.' : 'Open live mode to link a Telegram chat.');
  }
  visible('open-link', !linked);
  element('open-link').disabled = !view.live || telegramBusy;
  visible('telegram-actions', linked);
  element('send-test').disabled = telegramBusy;
  element('disconnect').disabled = telegramBusy;
}

function render(view) {
  text('session-label', view.live ? 'Local session' : 'Simulation mode');
  element('session').dataset.tone = view.live ? 'steady' : 'neutral';
  renderCamera(view);
  renderIncident(view);
  renderTelegram(view);
}

async function refresh() {
  try {
    state = await request('/api/status');
    render(state);
    if (state.live && !camerasRequested) {
      camerasRequested = true;
      await loadCameras();
    }
  } catch {
    state = null;
    clearFrame();
    text('session-label', 'Connection lost');
    element('session').dataset.tone = 'warning';
    text('camera-message', 'Monitor unavailable');
    text('camera-detail', 'Keep Fall Monitor running to reconnect.');
    for (const id of ['camera-action', 'stop-camera', 'camera-input', 'refresh-cameras', 'simulate', 'cancel', 'rearm', 'open-link', 'send-test', 'disconnect']) {
      element(id).disabled = true;
    }
  }
}

async function loadCameras() {
  if (!state?.live || cameraListBusy) return;
  cameraListBusy = true;
  renderCamera(state);
  try {
    const result = await request('/api/cameras');
    const input = element('camera-input');
    input.replaceChildren();
    for (const camera of result.cameras) {
      input.add(new Option(camera.name, String(camera.index)));
    }
    input.value = String(result.selected);
    cameraListReady = true;
    feedback('notice', '');
  } catch (error) {
    feedback('notice', error.message);
  } finally {
    cameraListBusy = false;
    if (state) renderCamera(state);
  }
}

async function changeCamera() {
  if (!state?.live || monitorBusy || state.running || state.permissionPending) return;
  const index = Number(element('camera-input').value);
  monitorBusy = true;
  renderCamera(state);
  feedback('notice', '');
  try {
    await request('/api/camera', { index });
    clearFrame();
  } catch (error) {
    feedback('notice', error.message);
  } finally {
    monitorBusy = false;
    await refresh();
  }
}

async function monitor(command) {
  if (!state || monitorBusy) return;
  monitorBusy = true;
  render(state);
  feedback('notice', '');
  try {
    await request('/api/command', { command });
  } catch (error) {
    feedback('notice', error.message);
  } finally {
    monitorBusy = false;
    await refresh();
  }
}

async function cameraFrame() {
  if (!state?.hasFrame) return;
  try {
    const response = await fetch('/api/frame');
    if (response.status !== 200) return;
    const frame = await response.blob();
    if (!state?.hasFrame) return;
    if (frameUrl) URL.revokeObjectURL(frameUrl);
    frameUrl = URL.createObjectURL(frame);
    element('preview').src = frameUrl;
    showFrame(true);
  } catch {
    clearFrame();
  }
}

function showTokenForm() {
  visible('token-form', true);
  visible('pairing', false);
  element('bot-token').focus();
}

async function createPairing(token = '') {
  const managed = state?.telegram?.managed;
  visible('token-form', false);
  visible('pairing', false);
  element('bot-token').value = '';
  feedback('link-status', 'Creating your Telegram link…');
  try {
    const link = await request('/api/telegram/link', managed ? {} : { token });
    const url = new URL(link.url);
    if (url.origin !== 'https://t.me') throw new Error('Telegram returned an invalid link.');
    element('telegram-link').href = url.href;
    text('pair-code', link.command);
    visible('change-token', !managed);
    if (dialog.open) {
      visible('pairing', true);
      element('telegram-link').focus();
    }
    feedback('link-status', '');
  } catch (error) {
    if (dialog.open && !managed) showTokenForm();
    feedback('link-status', error.message, true);
  }
}

async function finishPairing() {
  const button = element('finish-link');
  button.disabled = true;
  feedback('link-status', 'Looking for your Telegram chat…');
  try {
    await request('/api/telegram/finish', {});
    dialog.close();
    await refresh();
    feedback('telegram-result', 'Chat linked. Send a test message to check delivery.');
  } catch (error) {
    feedback('link-status', error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function telegramAction(action) {
  if (!state || telegramBusy) return;
  telegramBusy = true;
  renderTelegram(state);
  feedback('telegram-result', action === 'test' ? 'Sending a test message…' : '');
  try {
    const result = await request(`/api/telegram/${action}`, {});
    feedback('telegram-result', result.message || 'Telegram chat disconnected.');
  } catch (error) {
    feedback('telegram-result', error.message, true);
  } finally {
    telegramBusy = false;
    await refresh();
  }
}

element('camera-action').addEventListener('click', () => {
  if (state) monitor(state.live && (!state.running || state.fault) ? 'start' : 'pause');
});
element('stop-camera').addEventListener('click', () => monitor('stop'));
element('camera-input').addEventListener('change', changeCamera);
element('refresh-cameras').addEventListener('click', loadCameras);
element('cancel').addEventListener('click', () => monitor('cancel'));
element('rearm').addEventListener('click', () => monitor('reset'));
element('simulate').addEventListener('click', () => monitor('simulate_fall'));
element('open-link').addEventListener('click', () => {
  if (!state?.telegram) return;
  feedback('link-status', '');
  dialog.showModal();
  if (state.telegram.managed || state.telegram.hasToken) createPairing();
  else showTokenForm();
});
element('close-link').addEventListener('click', () => dialog.close());
element('change-token').addEventListener('click', () => {
  feedback('link-status', '');
  showTokenForm();
});
element('token-form').addEventListener('submit', (event) => {
  event.preventDefault();
  createPairing(element('bot-token').value);
});
dialog.addEventListener('close', () => { element('bot-token').value = ''; });
element('finish-link').addEventListener('click', finishPairing);
element('send-test').addEventListener('click', () => telegramAction('test'));
element('disconnect').addEventListener('click', () => telegramAction('disconnect'));

async function pollStatus() {
  await refresh();
  setTimeout(pollStatus, 250);
}

async function pollFrame() {
  await cameraFrame();
  setTimeout(pollFrame, 100);
}

pollStatus();
pollFrame();
