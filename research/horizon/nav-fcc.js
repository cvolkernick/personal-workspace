/**
 * Deep-link Horizon Macro → FCC.
 *
 * FCC listens on 127.0.0.1:8000 unless FCC_BIND_HOST=0.0.0.0 (that restores
 * the old all-interfaces bind). Path /financial-command/index.html matches
 * financial-command/server.py. Do not invent a different port.
 *
 * Access paths:
 * 1. Same-origin PWA lens /horizon/* → /financial-command/index.html.
 *    Stays in the installed app.
 * 2. Tailscale Serve hostname (*.ts.net) → https origin, never :8000.
 * 3. Loopback :8795 → http://127.0.0.1:8000/financial-command/index.html.
 * 4. Any other page host → the same-origin FCC path. Do not build
 *    http://<page-host>:8000; FCC is not listening there.
 *
 * Direct LAN :8795 needs HORIZON_BIND_HOST=0.0.0.0.
 *
 * Do not write root-absolute href or fetch strings here: FCC's /horizon/
 * proxy rewrites those and would trap the back-link under the lens prefix.
 * Horizon is a nested aspect of FCC, not a merged UI. No iframe, no embed.
 */
(function (global) {
  "use strict";

  var FCC_PORT = 8000;
  var FCC_PATH = "/financial-command/index.html";

  function isLoopback(host) {
    var h = String(host || "").toLowerCase();
    return h === "127.0.0.1" || h === "localhost" || h === "[::1]" || h === "::1";
  }

  function fccHref(hostname) {
    var loc = global.location || {};
    var host = hostname || loc.hostname || "127.0.0.1";
    var path = hostname ? "/" : loc.pathname || "/";
    var protocol = hostname ? "http:" : loc.protocol || "http:";
    var onLens = path === "/horizon" || path.indexOf("/horizon/") === 0;
    var onTsNet = /\.ts\.net$/i.test(host);
    if (!hostname && (onLens || protocol === "https:" || onTsNet)) {
      if (protocol === "https:" || onTsNet) {
        return "https://" + host + FCC_PATH;
      }
      return FCC_PATH;
    }
    if (onTsNet) {
      return "https://" + host + FCC_PATH;
    }
    if (isLoopback(host)) {
      return "http://" + host + ":" + FCC_PORT + FCC_PATH;
    }
    return FCC_PATH;
  }

  function wireFccNav() {
    var href = fccHref();
    var nodes = document.querySelectorAll("#nav-fcc, a[data-nav-fcc]");
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].setAttribute("href", href);
    }
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", wireFccNav);
    } else {
      wireFccNav();
    }
  }

  global.horizonFccHref = fccHref;
})(typeof window !== "undefined" ? window : globalThis);
