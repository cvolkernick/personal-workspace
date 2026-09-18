/**
 * Platform-aware FCC top-nav Robinhood link (#782).
 *
 * Detection: UA (Android / iPhone|iPad|iPod) OR
 * navigator.userAgentData.mobile === true. Not viewport or touch —
 * desktop touch laptops and DevTools width would false-positive.
 * Brave desktop-mode UA can omit "Android"; Client Hints still report
 * mobile on a phone.
 *
 * Desktop: leave a#nav-robinhood as https://robinhood.com/agentic?classic=1
 * (target=_blank). That URL is the #769 agentic portfolio view.
 *
 * Mobile: rewrite the <a> href to the Android intent / iOS scheme and
 * drop target=_blank so the tap is a real user-gesture navigation.
 * Assigning intent:// to window.location.href from a click handler is
 * dropped by Brave PWA (PR #784); the fallback then looks like a plain
 * robinhood.com web page.
 *
 *   Android: intent://open + scheme=robinhood + package com.robinhood.android.
 *            com.robinhood.android's BROWSABLE App Links on robinhood.com
 *            only match /applink/, /stocks/, /crypto/, etc. — not `/`.
 *            PR #791's https App Link to the site root therefore did not
 *            resolve to the app; Chrome/Brave used S.browser_fallback_url
 *            and the tap landed on the marketing page. The app does
 *            register scheme=robinhood on DeeplinkResolverActivity
 *            (BROWSABLE). Same pattern as the FitDash Planet Fitness
 *            launcher (custom scheme, not https host).
 *            S.browser_fallback_url is the mobile website, not Play Store.
 *   iOS:     robinhood:// then 900ms fallback to https://robinhood.com/.
 *
 * Click is delegated on document so a later nav paint cannot drop the
 * listener. App-not-installed fallback is https://robinhood.com/ (mobile
 * web). The agentic URL is a desktop view and is the broken webview this
 * issue closes.
 */
(function (global) {
  "use strict";

  var DESKTOP_HREF = "https://robinhood.com/agentic?classic=1";
  var MOBILE_WEB_HREF = "https://robinhood.com/";
  var ANDROID_PACKAGE = "com.robinhood.android";
  var ANDROID_SCHEME = "robinhood";
  var IOS_SCHEME = "robinhood://";
  var IOS_FALLBACK_MS = 900;

  function desktopHref() {
    return DESKTOP_HREF;
  }

  function mobileWebHref() {
    return MOBILE_WEB_HREF;
  }

  function isMobileUa(ua) {
    var agent = ua || "";
    return /Android/i.test(agent) || /iPhone|iPad|iPod/i.test(agent);
  }

  function resolveNav(uaOrNav) {
    if (uaOrNav && typeof uaOrNav === "object") return uaOrNav;
    if (typeof uaOrNav === "string") return { userAgent: uaOrNav };
    if (typeof navigator !== "undefined" && navigator) return navigator;
    return { userAgent: "" };
  }

  function isMobileNav(nav) {
    var n = resolveNav(nav);
    var uad = n.userAgentData;
    if (uad && uad.mobile === true) return true;
    return isMobileUa(n.userAgent || "");
  }

  function androidIntentHref() {
    return (
      "intent://open#Intent;scheme=" +
      ANDROID_SCHEME +
      ";package=" +
      ANDROID_PACKAGE +
      ";S.browser_fallback_url=" +
      encodeURIComponent(MOBILE_WEB_HREF) +
      ";end"
    );
  }

  function launchHref(uaOrNav) {
    var nav = resolveNav(uaOrNav);
    var agent = nav.userAgent || "";
    var platform = "";
    if (nav.userAgentData && nav.userAgentData.platform) {
      platform = String(nav.userAgentData.platform);
    }
    if (/Android/i.test(agent) || /Android/i.test(platform)) {
      return androidIntentHref();
    }
    if (/iPhone|iPad|iPod/i.test(agent) || /iOS|iPhone/i.test(platform)) {
      return IOS_SCHEME;
    }
    if (nav.userAgentData && nav.userAgentData.mobile === true) {
      return androidIntentHref();
    }
    return "";
  }

  function defaultGo(url) {
    if (url && typeof window !== "undefined") window.location.href = url;
  }

  function defaultClock() {
    return {
      now: function () {
        return Date.now();
      },
      wait: function (fn, ms) {
        return window.setTimeout(fn, ms);
      },
      cancel: function (id) {
        window.clearTimeout(id);
      },
    };
  }

  function startIosFallback(go, clock) {
    var t = clock || defaultClock();
    var navFn = go || defaultGo;
    var started = t.now();
    var timer = t.wait(function () {
      if (t.now() - started < 2000) navFn(MOBILE_WEB_HREF);
    }, IOS_FALLBACK_MS);
    if (typeof document !== "undefined" && document.addEventListener) {
      document.addEventListener(
        "visibilitychange",
        function () {
          if (document.hidden && timer != null && t.cancel) t.cancel(timer);
        },
        { once: true }
      );
    }
    return timer;
  }

  function applyMobileAnchor(node, uaOrNav) {
    if (!node) return false;
    var nav = resolveNav(uaOrNav);
    if (!isMobileNav(nav)) return false;
    var href = launchHref(nav);
    if (!href) return false;
    if (node.setAttribute) node.setAttribute("href", href);
    else node.href = href;
    if (node.removeAttribute) node.removeAttribute("target");
    else node.target = "";
    return true;
  }

  function closestRobinhood(target) {
    if (!target) return null;
    if (typeof target.closest === "function") {
      return target.closest("#nav-robinhood");
    }
    var n = target;
    while (n) {
      if (n.id === "nav-robinhood") return n;
      n = n.parentNode || n.parentElement;
    }
    return null;
  }

  function openRobinhood(uaOrNav, go, clock, ev, node) {
    var nav = resolveNav(uaOrNav);
    if (!isMobileNav(nav)) return false;
    var href = launchHref(nav);
    if (!href) return false;
    if (node) applyMobileAnchor(node, nav);
    if (/^robinhood:/i.test(href)) startIosFallback(go, clock);
    return true;
  }

  function onDocumentClick(ev) {
    if (!ev) return;
    var node = closestRobinhood(ev.target);
    if (!node) return;
    openRobinhood(null, null, null, ev, node);
  }

  function wireRobinhoodNav() {
    if (typeof document === "undefined" || !document) return;
    var node = document.getElementById
      ? document.getElementById("nav-robinhood")
      : null;
    if (node) applyMobileAnchor(node);
    if (document.__fccRobinhoodClickWired) return;
    if (document.addEventListener) {
      document.addEventListener("click", onDocumentClick);
      document.__fccRobinhoodClickWired = true;
    }
  }

  if (typeof document !== "undefined" && document) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", wireRobinhoodNav);
    } else {
      wireRobinhoodNav();
    }
  }

  global.fccRobinhoodDesktopHref = desktopHref;
  global.fccRobinhoodMobileWebHref = mobileWebHref;
  global.fccRobinhoodIsMobileUa = isMobileUa;
  global.fccRobinhoodIsMobile = isMobileNav;
  global.fccRobinhoodLaunchHref = launchHref;
  global.fccApplyRobinhoodAnchor = applyMobileAnchor;
  global.fccOpenRobinhood = openRobinhood;
  global.fccWireRobinhoodNav = wireRobinhoodNav;
})(typeof window !== "undefined" ? window : globalThis);
