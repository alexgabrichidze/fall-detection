const VERSION = 'fall-monitor-__BUILD_ID__';
const SHELL = ['/', '/monitor.js', '/monitor.css', '/geist.woff2', '/manifest.webmanifest',
  '/icons/192.png', '/icons/512.png', ...__ASSETS__];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(VERSION).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key.startsWith('fall-monitor-') && key !== VERSION).map(key => caches.delete(key)),
  )).then(() => self.clients.claim()));
});

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/telegram/')) return;
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() => caches.match('/')));
    return;
  }
  event.respondWith((async () => {
    const cache = await caches.open(VERSION);
    const saved = await cache.match(request);
    if (saved) return saved;
    const fetched = await fetch(request);
    if (fetched.ok) {
      try {
        await cache.put(request, fetched.clone());
      } catch {
        // A small storage quota can limit offline assets.
      }
    }
    return fetched;
  })());
});
