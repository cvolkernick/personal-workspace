/* FCC offline shell — caches static UI only, never API or treasury JSON. */
const CACHE = "fcc-shell-v1";
const PRECACHE = [
  "/financial-command/",
  "/financial-command/index.html",
  "/financial-command/manifest.webmanifest",
  "/financial-command/icon-192.png",
  "/financial-command/icon-512.png",
  "/financial-command/favicon.svg",
  "/financial-command/favicon.ico",
  "/financial-command/favicon-32.png",
  "/financial-command/apple-touch-icon.png",
  "/financial-command/pwa.js",
  "/financial-command/nav-fleet.js",
  "/financial-command/nav-horizon.js",
];

function isLiveMoney(url) {
  // Never cache /api/* or JSON snapshots (treasury_latest, spectrum, etc.)
  if (url.pathname.startsWith("/api/")) return true;
  if (url.pathname.endsWith(".json")) return true;
  return false;
}

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
  if (isLiveMoney(url)) return;
  if (url.origin === self.location.origin) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          if (res.ok && !isLiveMoney(url)) {
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() =>
          caches.match(req).then((hit) => hit || caches.match("/financial-command/"))
        )
    );
  }
});
