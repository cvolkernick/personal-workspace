/**
 * Deep-link Horizon Macro → FCC.
 *
 * FCC bind (LAN-debug): port 8000, path /financial-command/index.html
 * (financial-command/server.py --port default). Do not invent.
 *
 * Access paths:
 * 1. Same-origin PWA lens https://<host>/horizon/* → /financial-command/index.html
 *    (or https://<host>/financial-command/index.html). Stays in the installed app.
 * 2. Tailscale Serve hostname (*.ts.net) → https origin, never :8000.
 * 3. LAN-debug :8795 → http://<host>:8000/financial-command/index.html
 *
 * Do not write root-absolute href or fetch strings here: FCC's /horizon/
 * proxy rewrites those and would trap the back-link under the lens prefix.
 * Horizon is a nested aspect of FCC, not a merged UI. No iframe, no embed.
 */
(function (global) {
  "use strict";

  var FCC_PORT = 8000;
  var FCC_PATH = "/financial-command/index.html";

  function fccHref(hostname) {
    var loc = global.location || {};
    if (hostname) {
      return "http://" + hostname + ":" + FCC_PORT + FCC_PATH;
    }
    var host = loc.hostname || "127.0.0.1";
    var path = loc.pathname || "/";
    var protocol = loc.protocol || "http:";
    var onLens = path === "/horizon" || path.indexOf("/horizon/") === 0;
    var onTsNet = /\.ts\.net$/i.test(host);
    if (onLens || protocol === "https:" || onTsNet) {
      if (protocol === "https:" || onTsNet) {
        return "https://" + host + FCC_PATH;
      }
      return FCC_PATH;
    }
    return "http://" + host + ":" + FCC_PORT + FCC_PATH;
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
