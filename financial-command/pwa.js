/**
 * Origin-scoped SW. Canonical install origin is Tailscale HTTPS
 * (https://prism-gateway.tailb1085a.ts.net). Manifest + SW live at /
 * so Android/desktop install from that origin is standalone, not a bookmark.
 *
 * Plain-HTTP LAN (:8000) stays browse-only: do not register a SW or honor
 * beforeinstallprompt there (#749). An already-installed HTTP app (standalone
 * display-mode) is bounced to the HTTPS origin. Loopback is a secure context
 * and stays installable for local tests.
 *
 * Unregister the old /financial-command/ SW if it is still controlling.
 */
(function () {
  "use strict";

  var CANONICAL_HTTPS_ORIGIN = "https://prism-gateway.tailb1085a.ts.net";

  function hostname() {
    return String(location.hostname || "").toLowerCase();
  }

  function isLoopback() {
    var h = hostname();
    return h === "localhost" || h === "127.0.0.1" || h === "[::1]" || h === "::1";
  }

  function isInstallOrigin() {
    if (location.protocol === "https:") return true;
    return isLoopback();
  }

  function isStandalone() {
    try {
      if (
        window.matchMedia &&
        window.matchMedia("(display-mode: standalone)").matches
      ) {
        return true;
      }
    } catch (e) {}
    return window.navigator.standalone === true;
  }

  window.addEventListener("beforeinstallprompt", function (event) {
    if (isInstallOrigin()) return;
    event.preventDefault();
  });

  if (!isInstallOrigin() && isStandalone()) {
    location.replace(
      CANONICAL_HTTPS_ORIGIN +
        (location.pathname || "/") +
        (location.search || "") +
        (location.hash || "")
    );
    return;
  }

  if (!("serviceWorker" in navigator)) return;

  window.addEventListener("load", function () {
    var regs = navigator.serviceWorker.getRegistrations
      ? navigator.serviceWorker.getRegistrations()
      : Promise.resolve([]);
    regs
      .then(function (list) {
        return Promise.all(
          (list || []).map(function (reg) {
            var scope = String((reg && reg.scope) || "");
            if (!isInstallOrigin() || scope.indexOf("/financial-command/") !== -1) {
              return reg.unregister();
            }
            return Promise.resolve();
          })
        );
      })
      .then(function () {
        if (!isInstallOrigin()) return;
        return navigator.serviceWorker.register("/sw.js");
      })
      .catch(function () {
        /* offline shell is best-effort */
      });
  });
})();
