/* FitDash offline shell — caches static UI only, never API responses.
 * Versioned (?v=) assets: stale-while-revalidate (instant from cache, refreshed
 * in the background). Shell and unversioned files: network-first, cache fallback.
 */
const CACHE = "fitdash-shell-v154";
const PRECACHE = [
  "/",
  "/index.html",
  "/styles.css?v=goal3b-coach-1",
  "/meal-snapshot.js?v=recipe-planner-1288-2",
  "/trends-paired-avgs.js?v=paired-avgs-7",
  "/trends-azm.js?v=azm-90d-3",
  "/energy-weight-align.js?v=ewi-rolling7-1171",
  "/hidrate-bottle.js?v=bottle-charge-7",
  "/supergrok.js?v=connect-1",
  "/history-sets.js?v=tonnage-1025-1",
  "/calorie-rolling-avg.js?v=cal-7d-946-1",
  "/biomarkers.js?v=biomarkers-1",
  "/trends-steps.js?v=steps-2",
  "/app.js?v=hip-lifts-1",
  "/manifest.webmanifest",
  "/icon-192.png",
  "/favicon.svg",
  "/favicon.ico",
  "/favicon-32.png",
  "/apple-touch-icon.png",
  "/icon-512.png"
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

function putCopy(req, res) {
  if (res && res.ok) {
    const copy = res.clone();
    caches.open(CACHE).then((c) => c.put(req, copy));
  }
  return res;
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  // Never cache API — always network for live data
  if (url.pathname.startsWith("/api/")) return;
  if (url.origin !== self.location.origin) return;
  if (url.searchParams.has("v")) {
    // Stale-while-revalidate for versioned assets.
    event.respondWith(
      caches.open(CACHE).then((c) =>
        c.match(req).then((hit) => {
          const net = fetch(req).then((res) => putCopy(req, res));
          if (hit) {
            event.waitUntil(net.catch(() => undefined));
            return hit;
          }
          return net;
        })
      )
    );
    return;
  }
  // Shell + unversioned static: network-first, fall back to cache (offline).
  event.respondWith(
    fetch(req)
      .then((res) => putCopy(req, res))
      .catch(() => caches.match(req).then((hit) => hit || caches.match("/")))
  );
});
