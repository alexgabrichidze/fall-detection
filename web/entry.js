import { BrowserMonitor } from './monitor.js';

const monitor = new BrowserMonitor(document.getElementById('preview'));
window.fallMonitorBrowser = monitor;

const controls = document.createElement('div');
controls.className = 'pwa-controls';
const install = document.createElement('button');
install.className = 'button outline';
install.type = 'button';
install.textContent = 'Install app';
const voice = document.createElement('button');
voice.className = 'button outline';
voice.type = 'button';
voice.textContent = 'Enable voice cancellation';
voice.setAttribute('aria-pressed', 'false');
const note = document.createElement('p');
note.className = 'pwa-note';
note.setAttribute('role', 'status');
const instruction = 'Keep this screen open and the device awake while monitoring.';
note.textContent = instruction;
controls.append(install, voice, note);
document.getElementById('notice').before(controls);

const standalone = matchMedia('(display-mode: standalone)').matches || navigator.standalone;
const appleMobile = /iPhone|iPad|iPod/.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
let installer;
install.hidden = standalone || !appleMobile;
window.addEventListener('beforeinstallprompt', event => {
  event.preventDefault();
  installer = event;
  install.hidden = false;
});
window.addEventListener('appinstalled', () => { install.hidden = true; });
install.addEventListener('click', async () => {
  if (installer) {
    await installer.prompt();
    if ((await installer.userChoice).outcome === 'accepted') install.hidden = true;
    installer = null;
  } else {
    note.textContent = appleMobile ? 'In Safari, tap Share, then Add to Home Screen.'
      : 'Use Install app from the browser menu.';
  }
});
voice.addEventListener('click', async () => {
  voice.disabled = true;
  note.textContent = 'Preparing offline voice cancellation…';
  try {
    const enabled = await monitor.toggleVoice();
    voice.textContent = enabled ? 'Turn voice off' : 'Enable voice cancellation';
    voice.setAttribute('aria-pressed', String(enabled));
    note.textContent = instruction;
  } catch (error) {
    note.textContent = error.name === 'NotAllowedError' ? 'Allow microphone access to enable voice cancellation.' : error.message;
  } finally {
    voice.disabled = false;
  }
});

if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
await import('../src/fall_detection/static/monitor.js');
