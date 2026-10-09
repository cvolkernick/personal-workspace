/**
 * Deep-link FCC surfaces → Horizon Macro on the same HTTPS origin.
 *
 * Horizon is a nested aspect of FCC, not a merged UI. No iframe, no embed.
 * Installed PWA scope is "/": same-origin /horizon/ stays in the standalone window.
 * Direct :8795 remains a LAN-debug fallback (not linked from FCC).
 */
(function (global) {
  "use strict";

  function horizonHref() {
    return "/horizon/";
  }

  function wireHorizonNav() {
    var href = horizonHref();
    var nodes = document.querySelectorAll("#nav-horizon, a[data-nav-horizon]");
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].setAttribute("href", href);
    }
  }

  /**
   * Daily Brief newspaper (#1091): same-origin /brief entry in the header nav
   * (every surface that loads this script) and in the index mobile tab bar.
   * A plain link, not a data-m-tab panel, so the tab-shell click handler ignores it.
   */
  function briefHref() {
    return "/brief";
  }

  function wireBriefNav() {
    var href = briefHref();
    var hz = document.getElementById("nav-horizon");
    if (hz && !document.getElementById("nav-brief") && hz.parentNode) {
      var a = document.createElement("a");
      a.className = hz.className || "btn";
      a.id = "nav-brief";
      a.href = href;
      a.title = "Daily Brief newspaper (morning + evening)";
      a.textContent = "Brief";
      hz.parentNode.insertBefore(a, hz);
    }
    var bar = document.getElementById("mobile-tabbar");
    if (bar && !document.getElementById("tab-brief")) {
      var tab = document.createElement("a");
      tab.className = "tab-btn";
      tab.id = "tab-brief";
      tab.href = href;
      tab.setAttribute("data-nav-brief", "");
      tab.title = "Daily Brief newspaper";
      tab.style.textDecoration = "none";
      tab.innerHTML =
        '<span class="tab-ico" aria-hidden="true">\u25A4</span><span class="tab-lbl">Brief</span>';
      var more = bar.querySelector('[data-m-tab="more"]');
      bar.insertBefore(tab, more || null);
    }
  }

  function wireAll() {
    wireHorizonNav();
    wireBriefNav();
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", wireAll);
    } else {
      wireAll();
    }
  }

  global.fccBriefHref = briefHref;

  global.fccHorizonHref = horizonHref;
})(typeof window !== "undefined" ? window : globalThis);
