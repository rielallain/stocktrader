// StockTracker service worker.
// Caches the app shell so the UI loads instantly and works when the backend
// is waking up from cold sleep on Render's free tier. API calls always go
// network-first.

const VERSION = 'v1';
const SHELL_CACHE = `stocktracker-shell-${VERSION}`;
const SHELL_ASSETS = [
  '/',
  '/static/styles.css',
  '/static/app.js',
  '/manifest.webmanifest',
  '/icon-192.png',
  '/icon-512.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);

  // API calls -> always network, never cache
  if (url.pathname.startsWith('/api/')) {
    return;  // let browser do default
  }

  // Shell assets -> cache-first
  if (e.request.method === 'GET') {
    e.respondWith(
      caches.match(e.request).then((cached) => {
        if (cached) return cached;
        return fetch(e.request).then((resp) => {
          if (resp.ok && url.origin === self.location.origin) {
            const clone = resp.clone();
            caches.open(SHELL_CACHE).then((c) => c.put(e.request, clone));
          }
          return resp;
        });
      })
    );
  }
});
