/**
 * Deep-link FCC surfaces → Horizon Macro on the same host.
 *
 * Port comes from research/horizon/server.py DEFAULT_PORT (do not invent).
 * Horizon is a nested aspect of FCC, not a merged UI. No iframe, no embed.
 * LAN (192.168.x) and Tailscale (100.x) both work because the host comes
 * from window.location.hostname — never a hardcoded IP or public URL.
 */
(function (global) {
  "use strict";

  var HORIZON_PORT = 8795;

  function horizonHref(hostname) {
    var host = hostname || "127.0.0.1";
    return "http://" + host + ":" + HORIZON_PORT + "/";
  }

  function wireHorizonNav() {
    var host = (global.location && global.location.hostname) || "127.0.0.1";
    var href = horizonHref(host);
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
