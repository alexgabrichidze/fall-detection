import { BrowserMonitor } from './monitor.js';

window.fallMonitorBrowser = new BrowserMonitor(document.getElementById('preview'));
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
await import('../src/fall_detection/static/monitor.js');
