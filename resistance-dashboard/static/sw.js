/* FitDash offline shell — caches static UI only, never API responses. */
const CACHE = "fitdash-shell-v64";
const PRECACHE = [
  "/",
  "/index.html",
  "/styles.css?v=bottle-inline-1",
  "/app.js?v=meal-gtasks-321-1",
  "/meal-snapshot.js?v=meal-slot-1",
  "/manifest.webmanifest",
  "/icon-192.png",
  "/favicon.svg",
  "/favicon.ico",
  "/favicon-32.png",
  "/apple-touch-icon.png",
  "/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  // Never cache API — always network for live data
  if (url.pathname.startsWith("/api/")) return;
  // Same-origin static: network-first, fall back to cache (shell works offline)
  if (url.origin === self.location.origin) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          if (res.ok && (url.pathname === "/" || !url.pathname.startsWith("/api"))) {
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => caches.match(req).then((hit) => hit || caches.match("/")))
    );
  }
});
