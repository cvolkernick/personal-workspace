/**
 * Deep-link FCC surfaces → Auto Fleet on the same host, port 8796.
 *
 * Fleet is a nested aspect of FCC, not a merged UI. No iframe, no embed.
 * LAN (192.168.x) and Tailscale (100.x) both work because the host comes
 * from window.location.hostname — never a hardcoded IP.
 */
(function (global) {
  "use strict";

  var FLEET_PORT = 8796;

  function fleetHref(hostname) {
    var host = hostname || "127.0.0.1";
    return "http://" + host + ":" + FLEET_PORT + "/";
  }

  function wireFleetNav() {
    var host = (global.location && global.location.hostname) || "127.0.0.1";
    var href = fleetHref(host);
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
