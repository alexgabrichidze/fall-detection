const element = (id) => document.getElementById(id);
const text = (id, value) => {
  const node = element(id);
  if (node.textContent !== value) node.textContent = value;
};
const visible = (id, show) => { element(id).hidden = !show; };

let state = null;
let monitorBusy = false;
let telegramBusy = false;
let voiceBusy = false;
let selectedBrowserCamera = null;
let cameraListBusy = false;
let cameraListReady = false;
let camerasRequested = false;
let frameUrl;
const dialog = element('link-dialog');
const cameraDialog = element('camera-dialog');
const browserMonitor = window.fallMonitorBrowser || null;

async function request(path, body) {
  if (browserMonitor) return browserMonitor.request(path, body);
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
  fitVideo();
}

// Fit the stage to the actual frame ratio, preserving the complete image.
function fitVideo() {
  const space = element('video-space');
  const image = element('preview');
  const aspect = !image.hidden && image.naturalWidth && image.naturalHeight
    ? image.naturalWidth / image.naturalHeight : image.videoWidth && image.videoHeight
      ? image.videoWidth / image.videoHeight : 4 / 3;
  const card = space.closest('.camera-card');
  let availableHeight = Infinity;
  if (window.matchMedia('(min-width: 881px)').matches) {
    const style = getComputedStyle(card);
    const bottom = element('dashboard').getBoundingClientRect().bottom;
    // Reserve only real controls below the image, never a stretched empty box.
    let trailing = 0;
    for (let node = space.nextElementSibling; node; node = node.nextElementSibling) {
      if (!node.hidden) {
        const css = getComputedStyle(node);
        trailing += node.getBoundingClientRect().height + parseFloat(css.marginTop) + parseFloat(css.marginBottom);
      }
    }
    availableHeight = Math.max(0, bottom - space.getBoundingClientRect().top
      - parseFloat(style.paddingBottom) - parseFloat(style.borderBottomWidth) - trailing);
  }
  const width = Math.min(space.clientWidth, availableHeight * aspect);
  const stage = element('stage');
  const nextWidth = `${width}px`;
  const nextHeight = `${width / aspect}px`;
  if (space.style.height !== nextHeight) space.style.height = nextHeight;
  if (stage.style.width !== nextWidth) stage.style.width = nextWidth;
  if (stage.style.height !== nextHeight) stage.style.height = nextHeight;
}
new ResizeObserver(fitVideo).observe(element('video-space'));
element('preview').addEventListener('load', fitVideo);
element('preview').addEventListener('loadedmetadata', fitVideo);
element('preview').addEventListener('resize', fitVideo);

function clearFrame() {
  if (frameUrl) {
    URL.revokeObjectURL(frameUrl);
    element('preview').removeAttribute('src');
    frameUrl = undefined;
  }
  showFrame(false);
}

function cameraState(view) {
  if (view.paused && !view.fault) return ['Paused', 'Resume monitoring', ''];
  if (!view.live) return ['Demo', 'Camera is off in demo mode', 'Use “Simulate a fall” to try the response countdown.'];
  if (view.permissionPending) return ['Starting', 'Starting camera…', 'Allow camera access if asked.'];
  if (view.fault === 'camera_permission_denied') {
    const detail = view.browser ? 'Allow camera access in your browser’s site settings, then try again.'
      : 'In System Settings → Privacy & Security → Camera, allow the app that launched Safe&Sound, then try again.';
    return ['Access off', 'Camera access is off', detail];
  }
  if (view.fault === 'browser_model_unavailable') {
    return ['Unavailable', 'The detection model could not start', 'Check your connection for the first download and try again.'];
  }
  if (view.fault === 'camera_permission_dependency') {
    return ['Unavailable', 'Camera support is missing', 'Install the live dependencies from the project README.'];
  }
  if (view.fault === 'browser_camera_missing') return ['Unavailable', 'No camera found', 'Connect a camera and try again.'];
  if (view.fault) return ['Unavailable', 'Camera preview unavailable', 'Check the camera connection and try starting it again.'];
  if (view.camera === 'available') return ['On', 'Camera is on', 'Waiting for a frame.'];
  if (view.browser && view.running) return ['Starting', 'Loading the fall detector', 'The models run on this device.'];
  if (view.running) return ['Starting', 'Starting the camera', 'The preview will appear when the camera is ready.'];
  return ['Off', 'Start camera', ''];
}

function renderCamera(view) {
  const [label, title, detail] = cameraState(view);
  text('camera-chip', label);
  text('camera-message', title);
  text('camera-detail', detail);
  visible('camera-detail', Boolean(detail));
  element('camera-chip').dataset.tone = view.fault ? 'warning' : label === 'On' ? 'steady' : 'neutral';

  const canStart = view.live && (!view.running || view.fault);
  const toggle = canStart ? view.fault ? 'Retry camera' : 'Start camera' : view.paused ? 'Resume monitoring' : 'Pause monitoring';
  text('camera-action-label', view.permissionPending ? 'Waiting for access' : toggle);
  element('camera-action-icon').classList.toggle('paused-shape', Boolean(!canStart && !view.paused));
  element('camera-action').disabled = monitorBusy || view.permissionPending || (!view.live && Boolean(view.fault));
  element('camera-placeholder').disabled = monitorBusy || view.permissionPending || !view.live;
  element('camera-placeholder').setAttribute('aria-label', title);
  const voiceToggle = element('voice-toggle');
  const showVoiceToggle = Boolean(browserMonitor && view.running && view.remaining === null);
  text('voice-toggle', voiceBusy ? 'Setting up…' : view.voiceEnabled ? 'Voice on' : 'Enable voice');
  voiceToggle.setAttribute('aria-label', view.voiceEnabled ? 'Turn off voice cancellation' : 'Enable voice cancellation');
  voiceToggle.disabled = voiceBusy;
  visible('voice-toggle', showVoiceToggle);
  visible('stop-camera', view.live && view.running && !view.permissionPending);
  element('stop-camera').disabled = monitorBusy;
  visible('camera-input-controls', view.live);
  const input = element('camera-input');
  input.disabled = monitorBusy || cameraListBusy || !cameraListReady || view.running || view.permissionPending;
  element('refresh-cameras').disabled = monitorBusy || cameraListBusy || view.running || view.permissionPending;
  if (cameraListReady && !monitorBusy && document.activeElement !== input) {
    const selected = String(view.cameraInput ?? selectedBrowserCamera ?? "");
    if (selected && !Array.from(input.options).some(option => option.value === selected)) {
      input.add(new Option(`Camera ${view.cameraInput}`, selected));
    }
    input.value = selected;
  }
  visible('simulate', !view.live);
  element('simulate').disabled = monitorBusy || view.paused || view.incident !== 'monitoring';
  if (!view.hasFrame) clearFrame();
  else if (browserMonitor) showFrame(true);
}

function renderIncident(view) {
  const awaiting = view.remaining !== null;
  visible('incident-card', awaiting || ['cancelled', 'alerting'].includes(view.incident) || ['help', 'false_alarm'].includes(view.caregiver));
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
  if (view.caregiver === 'help') {
    title = 'Help is on the way';
    detail = 'Your caregiver acknowledged the alert.';
    tone = 'steady';
  } else if (view.caregiver === 'false_alarm') {
    title = 'False alarm confirmed';
    detail = 'Your caregiver marked the last alert as a false alarm.';
  }
  text('incident-status', title);
  text('incident-detail', detail);
  element('incident-panel').dataset.tone = tone;
  const wasAwaiting = element('dashboard').dataset.urgent === 'true';
  element('dashboard').dataset.urgent = awaiting;
  if (awaiting && !wasAwaiting) document.querySelector('.rail').scrollTop = 0;
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
  text('voice-status', view.listening ? 'Microphone active' : (view.voice || 'Disabled'));
  element('voice-status').title = view.voice || '';
  visible('voice-status', view.listening || (awaiting && !['disabled', 'ready', undefined].includes(view.voice)));
}

// Keep row nodes stable while ranking live scalar evidence; never store history.
const activityRows = new Map();
const activityMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
activityMotion.addEventListener('change', () => {
  if (activityMotion.matches) {
    for (const { item } of activityRows.values()) item.getAnimations().forEach(animation => animation.cancel());
  }
});
function renderActivity(activity) {
  const data = activity || { state: 'unknown', current: 'Unclear', detail: 'Waiting for activity data.', rows: [] };
  const labels = { live: 'Live', starting: 'Starting', partial: 'Partial view', paused: 'Paused', off: 'Off', demo: 'Demo', stale: 'Stale', fault: 'Unavailable', unknown: 'Waiting', disconnected: 'Offline' };
  text('activity-chip', data.scope === 'upper' ? 'Upper body' : labels[data.state] || 'Waiting');
  element('activity-chip').dataset.tone = data.state === 'live' ? 'steady' : 'neutral';
  text('activity-current', data.current);
  text('activity-detail', data.detail);
  const fall = Number.isFinite(data.fallScore) ? Math.round(data.fallScore * 100) : null;
  text('fall-score-value', fall === null ? '—' : `${fall}/100`);
  element('fall-score-bar').value = fall ?? 0;
  element('fall-score-bar').setAttribute('aria-valuetext', fall === null ? 'Unavailable' : `${fall} out of 100`);
  element('fall-score-row').dataset.tone = fall !== null && fall >= 90 ? 'warning' : 'neutral';
  const list = element('activity-scores');
  const rows = data.rows?.length ? data.rows : [
    ['standing', 'Standing'], ['walking', 'Walking'], ['sitting', 'Sitting'],
    ['bending', 'Bending'], ['lying', 'Lying / horizontal'], ['unclear', 'Unclear / other'],
  ].map(([id, label]) => ({ id, label, score: null }));
  const nextIds = rows.map(row => row.id);
  const previousIds = Array.from(list.children, item => item.dataset.activityId);
  const reordered = nextIds.join('|') !== previousIds.join('|');
  // FLIP: remember the current visual positions, including any in-flight motion.
  const previousPositions = new Map();
  if (reordered) {
    for (const item of list.children) previousPositions.set(item.dataset.activityId, item.getBoundingClientRect());
    for (const { item } of activityRows.values()) item.getAnimations().forEach(animation => animation.cancel());
  }
  const orderedItems = [];
  for (const [index, row] of rows.entries()) {
    let nodes = activityRows.get(row.id);
    if (!nodes) {
      const item = document.createElement('li'); item.dataset.activityId = row.id;
      const rank = document.createElement('span'); rank.className = 'activity-rank';
      const content = document.createElement('div'); content.className = 'activity-row-content';
      const heading = document.createElement('div'); heading.className = 'activity-score-heading';
      const label = document.createElement('span');
      const value = document.createElement('strong');
      const bar = document.createElement('progress'); bar.max = 100;
      heading.append(label, value); content.append(heading, bar); item.append(rank, content);
      nodes = { item, rank, label, value, bar }; activityRows.set(row.id, nodes);
    }
    const value = Number.isFinite(row.score) ? Math.round(row.score * 100) : null;
    nodes.rank.textContent = String(index + 1).padStart(2, '0');
    nodes.label.textContent = row.label;
    nodes.value.textContent = value === null ? '—' : `${value}/100`;
    nodes.bar.value = value ?? 0;
    nodes.bar.setAttribute('aria-label', `${row.label} support`);
    nodes.bar.setAttribute('aria-valuetext', value === null ? 'Unavailable' : `${value} out of 100`);
    nodes.item.dataset.leading = ['live', 'partial'].includes(data.state) && (row.label === data.current || (row.id === 'unclear' && data.current === 'Unclear'));
    nodes.item.dataset.unknown = value === null;
    orderedItems.push(nodes.item);
  }
  if (reordered) {
    list.replaceChildren(...orderedItems);
    if (!activityMotion.matches) {
      for (const item of orderedItems) {
        const previous = previousPositions.get(item.dataset.activityId);
        const current = item.getBoundingClientRect();
        const offset = previous ? previous.top - current.top : 0;
        if (previous && Math.abs(offset) > 0.5) {
          item.animate([{ transform: `translateY(${offset}px)` }, { transform: 'translateY(0)' }],
            { duration: 380, easing: 'cubic-bezier(.22,.8,.25,1)' });
        } else if (!previous) {
          item.animate([{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'translateY(0)' }],
            { duration: 220, easing: 'ease-out' });
        }
      }
    }
  }
}

function renderTelegram(view) {
  const telegram = view.telegram;
  const linked = Boolean(telegram?.linked);
  text('telegram-chip', linked ? 'Linked' : view.live ? 'Not linked' : 'Demo');
  element('telegram-chip').dataset.tone = linked ? 'steady' : 'neutral';
  const bot = telegram?.bot ? ` via @${telegram.bot}` : '';
  if (linked) {
    text('telegram-description', `Alerts go to ${telegram.recipient || 'your Telegram chat'}${bot} with a short, silent camera clip when available.`);
  } else {
    text('telegram-description', view.live ? 'Link one chat to receive fall alerts with a short camera clip.' : 'Open live mode to link a Telegram chat.');
  }
  visible('open-link', !linked);
  element('open-link').disabled = !view.live || telegramBusy;
  visible('telegram-actions', linked);
  element('send-test').disabled = telegramBusy;
  element('disconnect').disabled = telegramBusy;
}

function render(view) {
  renderCamera(view);
  renderIncident(view);
  renderTelegram(view);
  renderActivity(view.activity);
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
    renderActivity({ state: 'disconnected', current: 'Unclear', detail: 'Reconnect to see fresh activity.', rows: [] });
    text('camera-message', 'Monitor unavailable');
    text('camera-detail', 'Keep Safe&Sound running to reconnect.');
    for (const id of ['camera-action', 'camera-placeholder', 'voice-toggle', 'stop-camera', 'camera-input', 'refresh-cameras', 'simulate', 'cancel', 'rearm', 'open-link', 'send-test', 'disconnect']) {
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
    if (browserMonitor && result.selected == null) input.add(new Option('Choose when starting', ''));
    for (const camera of result.cameras) {
      input.add(new Option(camera.name, String(camera.id ?? camera.index)));
    }
    input.value = String(result.selected ?? selectedBrowserCamera ?? '');
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
  if (browserMonitor) {
    selectedBrowserCamera = element('camera-input').value || null;
    return;
  }
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

function chooseCamera(cameras) {
  const options = element('camera-options');
  options.replaceChildren();
  for (const camera of cameras) {
    const option = document.createElement('button');
    option.type = 'button';
    option.className = 'button outline';
    option.textContent = camera.name;
    option.addEventListener('click', () => {
      cameraDialog.close();
      monitor('start', camera);
    });
    options.append(option);
  }
  cameraDialog.showModal();
  options.firstElementChild.focus();
}

async function toggleVoice() {
  if (!browserMonitor || !state?.running || voiceBusy) return;
  voiceBusy = true;
  renderCamera(state);
  feedback('notice', '');
  try {
    await browserMonitor.toggleVoice();
  } catch (error) {
    const message = error.name === 'NotAllowedError' ? 'Allow microphone access to enable voice cancellation.' : error.message;
    feedback('notice', message, true);
  } finally {
    voiceBusy = false;
    await refresh();
  }
}

async function monitor(command, camera) {
  if (!state || monitorBusy) return;
  monitorBusy = true;
  render(state);
  feedback('notice', '');
  try {
    const result = await request('/api/command', { command, cameraId: camera?.id ?? selectedBrowserCamera ?? (browserMonitor ? state.cameraInput : undefined) });
    if (result?.cameras) chooseCamera(result.cameras);
    if (browserMonitor && command === 'start') camerasRequested = false;
  } catch (error) {
    feedback('notice', error.message);
  } finally {
    monitorBusy = false;
    await refresh();
  }
}

async function cameraFrame() {
  if (browserMonitor || !state?.hasFrame) return;
  try {
    const response = await fetch('/api/frame');
    if (response.status !== 200) return;
    const frame = await response.blob();
    if (browserMonitor || !state?.hasFrame) return;
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
element('camera-placeholder').addEventListener('click', () => {
  if (state) monitor(state.paused && !state.fault ? 'pause' : 'start');
});
element('voice-toggle').addEventListener('click', toggleVoice);
element('close-camera-dialog').addEventListener('click', () => cameraDialog.close());
cameraDialog.addEventListener('close', () => { if (!monitorBusy) element('camera-action').focus(); });
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
if (!browserMonitor) pollFrame();
