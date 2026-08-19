/**
 * Shared Vercel read-only banner for FCC surfaces.
 * Hidden on Mac/Pi (health.role !== vercel-preview). Always-on on Vercel.
 * Does not remove panels. Marks stale if snapshot as_of is older than 6h.
 */
(function (global) {
  "use strict";

  var STALE_MS = 6 * 3600 * 1000;

  function applyStale(data) {
    var banner = document.getElementById("fcc-vercel-banner");
    var staleEl = document.getElementById("fcc-vercel-banner-stale");
    if (!banner || !document.body.classList.contains("fcc-vercel-preview")) return;
    var iso = data && data.snapshot && data.snapshot.as_of;
    if (!iso && data) iso = data.as_of || data.watchlist_as_of;
    var stale = true;
    if (iso) {
      var t = Date.parse(iso);
      if (!Number.isNaN(t)) stale = Date.now() - t > STALE_MS;
    }
    if (data && data.stale === true) stale = true;
    if (data && data.stale === false && iso) {
      stale = Date.now() - Date.parse(iso) > STALE_MS;
    }
    banner.classList.toggle("is-stale", stale);
    if (staleEl) staleEl.hidden = !stale;
  }

  async function init() {
    try {
      var res = await fetch("/api/health?ts=" + Date.now(), { cache: "no-store" });
      var j = await res.json();
      if (!(j && j.role === "vercel-preview")) return;
      document.body.classList.add("fcc-vercel-preview");
      var banner = document.getElementById("fcc-vercel-banner");
      if (banner) banner.hidden = false;
      if (global.__treasury) applyStale(global.__treasury);
      else applyStale(j);
    } catch (_) {
      /* Mac/Pi or offline: stay hidden */
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  global.applyFccVercelBanner = applyStale;
  global.initFccVercelBanner = init;
})(typeof window !== "undefined" ? window : globalThis);
