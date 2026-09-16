/**
 * Platform-aware FCC top-nav Robinhood link (#782).
 *
 * Detection: UA (Android / iPhone|iPad|iPod). Not viewport or touch —
 * desktop touch laptops and DevTools width would false-positive. The
 * reported failure is Brave on an Android phone (FCC PWA / Tailscale HTTPS).
 *
 * Desktop: leave a#nav-robinhood as https://robinhood.com/agentic?classic=1
 * (target=_blank). That URL is the #769 agentic portfolio view.
 *
 * Mobile: intercept the click and hand off to the native app. Plain https
 * with target=_blank stays in Brave/PWA as a webview; /agentic is not an
 * iOS Universal Link (AASA paths omit / and /agentic).
 *
 *   Android: intent:// + https://robinhood.com/ + package com.robinhood.android
 *            (assetlinks.json handle_all_urls). S.browser_fallback_url is the
 *            mobile website, not Play Store.
 *   iOS:     robinhood:// then 900ms fallback to https://robinhood.com/.
 *
 * App-not-installed fallback is https://robinhood.com/ (mobile web). The
 * agentic URL is a desktop view and is the broken webview this issue closes.
 */
(function (global) {
  "use strict";

  var DESKTOP_HREF = "https://robinhood.com/agentic?classic=1";
  var MOBILE_WEB_HREF = "https://robinhood.com/";
  var ANDROID_PACKAGE = "com.robinhood.android";
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

  function androidIntentHref() {
    return (
      "intent://robinhood.com/#Intent;scheme=https;package=" +
      ANDROID_PACKAGE +
      ";S.browser_fallback_url=" +
      encodeURIComponent(MOBILE_WEB_HREF) +
      ";end"
    );
  }

  function launchHref(ua) {
    var agent = ua || "";
    if (/Android/i.test(agent)) return androidIntentHref();
    if (/iPhone|iPad|iPod/i.test(agent)) return IOS_SCHEME;
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

  function openRobinhood(ua, nav, clock, ev) {
    var agent =
      ua ||
      (typeof navigator !== "undefined" && navigator.userAgent) ||
      "";
    if (!isMobileUa(agent)) return false;
    if (ev) {
      if (ev.preventDefault) ev.preventDefault();
      if (ev.stopPropagation) ev.stopPropagation();
    }
    var href = launchHref(agent);
    var go = nav || defaultGo;
    if (!href) return false;
    if (/^robinhood:/i.test(href)) {
      var t = clock || defaultClock();
      var started = t.now();
      var timer = t.wait(function () {
        if (t.now() - started < 2000) go(MOBILE_WEB_HREF);
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
      go(href);
      return true;
    }
    go(href);
    return true;
  }

  function wireRobinhoodNav() {
    var node =
      typeof document !== "undefined"
        ? document.getElementById("nav-robinhood")
        : null;
    if (!node) return;
    node.addEventListener("click", function (ev) {
      openRobinhood(null, null, null, ev);
    });
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", wireRobinhoodNav);
    } else {
      wireRobinhoodNav();
    }
  }

  global.fccRobinhoodDesktopHref = desktopHref;
  global.fccRobinhoodMobileWebHref = mobileWebHref;
  global.fccRobinhoodIsMobileUa = isMobileUa;
  global.fccRobinhoodLaunchHref = launchHref;
  global.fccOpenRobinhood = openRobinhood;
})(typeof window !== "undefined" ? window : globalThis);
