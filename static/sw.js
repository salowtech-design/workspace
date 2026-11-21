// Salow Workspace Root-Scoped Service Worker for PWA compliance and safe offline fallback
const CACHE_NAME = 'salow-static-cache-v2';
const PRECACHE_ASSETS = [
  '/static/css/tokens.css',
  '/static/css/components.css',
  '/static/img/logo.jpg',
  '/static/offline.html'
];

// Sensitive or authenticated paths strictly excluded from caching
const SENSITIVE_PATHS = [
  '/auth/',
  '/iam/',
  '/attendance/',
  '/directory/',
  '/posts/',
  '/certificates/',
  '/verify/',
  '/security/',
  '/api/'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(PRECACHE_ASSETS);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Only handle GET requests
  if (event.request.method !== 'GET') {
    return;
  }

  const url = new URL(event.request.url);

  // Strictly exclude authenticated business and IAM paths from caching
  const isSensitive = SENSITIVE_PATHS.some((path) => url.pathname.startsWith(path));
  if (isSensitive) {
    return;
  }

  // Handle navigation requests: network-first with safe offline fallback
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => {
        return caches.match('/static/offline.html');
      })
    );
    return;
  }

  // Handle static assets: cache-first with network fallback
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then((cachedResponse) => {
        if (cachedResponse) {
          return cachedResponse;
        }
        return fetch(event.request).then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const responseToCache = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseToCache);
            });
          }
          return networkResponse;
        });
      })
    );
  }
});
