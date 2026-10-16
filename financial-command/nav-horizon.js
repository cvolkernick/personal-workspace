/**
 * Deep-link FCC surfaces → Horizon Macro on the same HTTPS origin.
 *
 * Horizon is a nested aspect of FCC, not a merged UI. No iframe, no embed.
 * Installed PWA scope is "/": same-origin /horizon/ stays in the standalone window.
 * Horizon listens on 127.0.0.1:8795. Direct LAN :8795 needs
 * HORIZON_BIND_HOST=0.0.0.0 (that restores the old all-interfaces bind).
 * FCC does not link to a LAN IP or :8795.
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
