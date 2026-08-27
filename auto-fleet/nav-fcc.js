/**
 * Deep-link Auto Fleet → FCC on the same host.
 *
 * Bind comes from deploy/endpoints.json financial-command
 * (port 8000, path /financial-command/index.html). Do not invent.
 * Fleet is a nested aspect of FCC, not a merged UI. No iframe, no embed.
 * LAN (192.168.x) and Tailscale (100.x) both work because the host comes
 * from window.location.hostname — never a hardcoded IP or public URL.
 */
(function (global) {
  "use strict";

  var FCC_PORT = 8000;
  var FCC_PATH = "/financial-command/index.html";

  function fccHref(hostname) {
    var host = hostname || "127.0.0.1";
    return "http://" + host + ":" + FCC_PORT + FCC_PATH;
  }

  function wireFccNav() {
    var host = (global.location && global.location.hostname) || "127.0.0.1";
    var href = fccHref(host);
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

  global.fleetFccHref = fccHref;
})(typeof window !== "undefined" ? window : globalThis);
