/**
 * Origin-scoped SW (same as FitDash). Manifest + SW must live at / so
 * Android install from http://host:8000/ is standalone, not a bookmark.
 * Unregister the old /financial-command/ SW if it is still controlling.
 */
(function () {
  "use strict";
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
            if (scope.indexOf("/financial-command/") !== -1) {
              return reg.unregister();
            }
            return Promise.resolve();
          })
        );
      })
      .then(function () {
        return navigator.serviceWorker.register("/sw.js");
      })
      .catch(function () {
        /* offline shell is best-effort */
      });
  });
})();
