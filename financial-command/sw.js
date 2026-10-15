/* FCC offline shell — caches static UI only, never API or treasury JSON. */
const CACHE_PREFIX = "fcc-shell-";
const CACHE = CACHE_PREFIX + "v8";
const PRECACHE = [
  "/",
  "/financial-command/",
  "/financial-command/index.html",
  "/manifest.webmanifest",
  "/sw.js",
  "/icon-192.png",
  "/icon-512.png",
  "/favicon.svg",
  "/favicon.ico",
  "/favicon-32.png",
  "/apple-touch-icon.png",
  "/pwa.js",
  "/nav-fleet.js",
  "/nav-horizon.js",
];

function isLiveMoney(url) {
  // Never cache /api/* or JSON snapshots (treasury_latest, spectrum, etc.)
  if (url.pathname.startsWith("/api/")) return true;
  if (url.pathname.startsWith("/fleet/api/")) return true;
  if (url.pathname.startsWith("/horizon/api/")) return true;
  if (url.pathname.endsWith(".json")) return true;
  return false;
}

function isSiblingApp(url) {
  // /horizon/ and /fleet/ are separate apps proxied under this origin. Horizon
  // registers its own worker at /horizon/ (#1119). FCC's root worker must not
  // answer or cache their pages, editions or APIs (daily-brief included).
  const p = url.pathname;
  return (
    p === "/horizon" ||
    p.startsWith("/horizon/") ||
    p === "/fleet" ||
    p.startsWith("/fleet/")
  );
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      // Only prune FCC's own old shells. Never touch horizon-shell-* or any
      // other app's caches on this shared origin.
      Promise.all(
        keys
          .filter((k) => k.startsWith(CACHE_PREFIX) && k !== CACHE)
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // Early return, no respondWith: the browser (or Horizon's own worker) handles it.
  if (isSiblingApp(url)) return;
  if (isLiveMoney(url)) return;
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
        caches.match(req).then((hit) => hit || caches.match("/") || caches.match("/financial-command/"))
      )
  );
});
