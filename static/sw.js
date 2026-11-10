// Service worker: makes the dashboard installable as a PWA.
// API calls always go to the network (live data, auth). Static assets are
// served network-first with a cache fallback so the shell still opens offline.
const CACHE = 'tgs-shell-v2';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(
  caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim())
));

self.addEventListener('fetch', e => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  e.respondWith(
    fetch(req).then(res => {
      // Key by path only: each ?v= bump used to add a new entry that was never pruned.
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(url.pathname, copy)); }
      return res;
    }).catch(() => caches.match(url.pathname).then(r => r || caches.match('/')))
  );
});
