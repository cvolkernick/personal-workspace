/* Horizon app shell worker. Caches static shell files only.
 *
 * - HTML (the Daily Brief editions, /model): network only, so an edition is
 *   never stale. Offline, a small "you're offline" page is shown instead of
 *   an old edition.
 * - api/, data/, daily-brief/api/ and any .json: never intercepted, never cached.
 * - Shell files (manifest, icons, scripts): network first, cache as fallback.
 *
 * Paths are relative to the registration scope ("/" or "/horizon/"). Keep
 * root-absolute fetch/src/href strings out of this file: FCC's lens rewrites them.
 */
const CACHE = "horizon-shell-v1";
const SHELL = [
  "manifest.webmanifest",
  "pwa.js",
  "nav-fcc.js",
  "icon-192.png",
  "icon-512.png",
  "icon-maskable-192.png",
  "icon-maskable-512.png",
  "apple-touch-icon.png",
  "favicon-32.png",
];

const OFFLINE_HTML =
  '<!doctype html><html lang="en"><head><meta charset="utf-8">' +
  '<meta name="viewport" content="width=device-width, initial-scale=1">' +
  '<meta name="theme-color" content="#060a10"><title>Horizon (offline)</title>' +
  "<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;" +
  "background:#060a10;color:#e8eef6;font:15px system-ui,-apple-system,sans-serif;text-align:center;padding:1.5rem}" +
  "p{color:#7f96ad}</style></head><body><div><h1>Horizon is offline</h1>" +
  "<p>Reconnect to load the latest edition.</p></div></body></html>";

function scopePath() {
  return new URL(self.registration.scope).pathname;
}

function relPath(url) {
  const base = scopePath();
  return url.pathname.startsWith(base) ? url.pathname.slice(base.length) : null;
}

function isLive(rel) {
  return (
    rel.startsWith("api/") ||
    rel.startsWith("data/") ||
    rel.startsWith("daily-brief/api/") ||
    rel.endsWith(".json")
  );
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL.map((p) => new URL(p, self.registration.scope).href)))
      .catch(() => undefined)
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((k) => k.startsWith("horizon-shell-") && k !== CACHE).map((k) => caches.delete(k))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const rel = relPath(url);
  if (rel === null || isLive(rel)) return;

  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(
        () =>
          new Response(OFFLINE_HTML, {
            status: 503,
            headers: { "Content-Type": "text/html; charset=utf-8" },
          })
      )
    );
    return;
  }

  if (SHELL.indexOf(rel) === -1) return;
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || Response.error()))
  );
});
