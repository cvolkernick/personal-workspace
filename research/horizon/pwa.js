/**
 * Horizon install shell. Own file on purpose: FCC's pwa.js is origin-scoped
 * ("/") and is not touched here.
 *
 * Prefix-aware: the app base is the directory of this page's manifest link.
 * Behind FCC's /horizon lens that link is rewritten to /horizon/..., so the
 * worker registers at <base>sw.js with scope <base> ("/horizon/"), and FCC's
 * own root worker keeps everything else. Directly on :8795 the base is "/".
 *
 * Service workers need a secure context (HTTPS or loopback); plain-HTTP LAN
 * debug stays browse-only.
 *
 * Keep root-absolute href/src/fetch strings out of this file: the lens
 * rewrites those.
 */
(function () {
  "use strict";
  if (!("serviceWorker" in navigator)) return;
  if (window.isSecureContext === false) return;

  function appBase() {
    var link = document.querySelector('link[rel="manifest"]');
    var ref = (link && link.href) || document.baseURI || location.href;
    return new URL(".", ref).href;
  }

  window.addEventListener("load", function () {
    var base = appBase();
    navigator.serviceWorker
      .register(base + "sw.js", { scope: base })
      .catch(function () {
        /* install shell is best-effort */
      });
  });
})();
