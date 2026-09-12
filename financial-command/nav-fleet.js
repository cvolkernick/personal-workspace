/**
 * Deep-link FCC surfaces → Auto Fleet on the same HTTPS origin.
 *
 * Fleet is a nested aspect of FCC, not a merged UI. No iframe, no embed.
 * Installed PWA scope is "/": same-origin /fleet/ stays in the standalone window.
 * Direct :8796 remains a LAN-debug fallback (not linked from FCC).
 */
(function (global) {
  "use strict";

  function fleetHref() {
    return "/fleet/";
  }

  function wireFleetNav() {
    var href = fleetHref();
    var nodes = document.querySelectorAll("#nav-fleet, a[data-nav-fleet]");
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].setAttribute("href", href);
    }
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", wireFleetNav);
    } else {
      wireFleetNav();
    }
  }

  global.fccFleetHref = fleetHref;
})(typeof window !== "undefined" ? window : globalThis);
