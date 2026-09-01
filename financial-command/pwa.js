/**
 * Register the FCC service worker so Chromium can install a standalone app.
 * Script URL /financial-command/sw.js keeps scope under /financial-command/.
 */
(function () {
  "use strict";
  if (!("serviceWorker" in navigator)) return;
  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/financial-command/sw.js").catch(function () {
      /* offline shell is best-effort */
    });
  });
})();
