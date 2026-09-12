/**
 * Deep-link FCC surfaces → Horizon Macro on the same HTTPS origin.
 *
 * Horizon is a nested aspect of FCC, not a merged UI. No iframe, no embed.
 * Installed PWA scope is "/": same-origin /horizon/ stays in the standalone window.
 * Direct :8795 remains a LAN-debug fallback (not linked from FCC).
 */
(function (global) {
  "use strict";

  function horizonHref() {
    return "/horizon/";
  }

  function wireHorizonNav() {
    var href = horizonHref();
    var nodes = document.querySelectorAll("#nav-horizon, a[data-nav-horizon]");
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].setAttribute("href", href);
    }
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", wireHorizonNav);
    } else {
      wireHorizonNav();
    }
  }

  global.fccHorizonHref = horizonHref;
})(typeof window !== "undefined" ? window : globalThis);
