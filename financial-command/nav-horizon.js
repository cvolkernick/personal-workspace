/**
 * Deep-link FCC surfaces → Horizon Macro.
 *
 * Port comes from research/horizon/server.py DEFAULT_PORT (do not invent).
 * Horizon listens on 127.0.0.1:8795 (research/horizon/server.py DEFAULT_BIND;
 * --host 0.0.0.0 restores the old all-interfaces bind), so:
 * 1. Loopback page host (127.0.0.1 / localhost) → http://<host>:8795/.
 * 2. Any other page host (tailnet HTTPS origin, MagicDNS, LAN) → the
 *    same-origin /horizon/ path. Never http://<page-host>:8795/: Horizon is
 *    not listening there. Installed PWA scope is "/", so /horizon/ stays in
 *    the standalone window.
 *
 * Horizon is a nested aspect of FCC, not a merged UI. No iframe, no embed.
 * Never a hardcoded IP or public URL.
 */
(function (global) {
  "use strict";

  var HORIZON_PORT = 8795;
  var HORIZON_PATH = "/horizon/";

  function isLoopback(host) {
    var h = String(host || "").toLowerCase();
    return h === "127.0.0.1" || h === "localhost" || h === "[::1]" || h === "::1";
  }

  function horizonHref(hostname) {
    var host = hostname || "127.0.0.1";
    if (isLoopback(host)) {
      return "http://" + host + ":" + HORIZON_PORT + "/";
    }
    return HORIZON_PATH;
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
