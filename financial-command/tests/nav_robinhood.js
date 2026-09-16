#!/usr/bin/env node
/**
 * Behavioral check: mobile Robinhood launch vs desktop no-op.
 * Loads financial-command/nav-robinhood.js in a vm (no browser).
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "nav-robinhood.js"),
  "utf8"
);

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

const sandbox = { window: {}, document: undefined, navigator: undefined };
sandbox.globalThis = sandbox.window;
vm.runInNewContext(SRC, vm.createContext(sandbox));
const g = sandbox.window;

const android =
  "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 Chrome/126.0.0.0 Mobile Safari/537.36 Brave/1.0";
const ios =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148";
const desktop =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36";

assert(typeof g.fccRobinhoodLaunchHref === "function", "exports launchHref");
assert(
  g.fccRobinhoodDesktopHref() === "https://robinhood.com/agentic?classic=1",
  "desktop agentic href"
);
assert(
  g.fccRobinhoodMobileWebHref() === "https://robinhood.com/",
  "mobile web fallback"
);
assert(g.fccRobinhoodIsMobileUa(android) === true, "android is mobile");
assert(g.fccRobinhoodIsMobileUa(ios) === true, "ios is mobile");
assert(g.fccRobinhoodIsMobileUa(desktop) === false, "desktop is not mobile");
assert(g.fccRobinhoodIsMobileUa("") === false, "empty ua is not mobile");

const androidHref = g.fccRobinhoodLaunchHref(android);
assert(androidHref.indexOf("intent://robinhood.com/#Intent;") === 0, "android intent host");
assert(androidHref.indexOf("scheme=https") >= 0, "android https scheme (App Link)");
assert(androidHref.indexOf("package=com.robinhood.android") >= 0, "android package");
assert(androidHref.indexOf("scheme=robinhood") < 0, "android does not use custom scheme");
assert(androidHref.indexOf("action=android.intent.action.MAIN") < 0, "android is not MAIN");
assert(
  androidHref.indexOf("category=android.intent.category.LAUNCHER") < 0,
  "android is not LAUNCHER"
);
assert(androidHref.indexOf("agentic") < 0, "android intent is not the agentic path");
const encodedWeb = encodeURIComponent("https://robinhood.com/");
assert(
  androidHref.indexOf("S.browser_fallback_url=" + encodedWeb) >= 0,
  "android encoded web fallback"
);

assert(g.fccRobinhoodLaunchHref(ios) === "robinhood://", "ios scheme");
assert(g.fccRobinhoodLaunchHref(desktop) === "", "desktop launch href empty");
assert(g.fccRobinhoodLaunchHref("") === "", "empty ua launch href empty");

const androidHits = [];
const androidEv = {
  preventDefault() {
    this.prevented = true;
  },
  stopPropagation() {
    this.stopped = true;
  },
};
assert(
  g.fccOpenRobinhood(android, (url) => androidHits.push(url), null, androidEv) === true,
  "android open true"
);
assert(androidEv.prevented === true, "android prevents default (no webview tab)");
assert(androidHits.length === 1, "android one nav");
assert(androidHits[0] === androidHref, "android nav href");

const deskHits = [];
const deskEv = {
  preventDefault() {
    this.prevented = true;
  },
};
assert(
  g.fccOpenRobinhood(desktop, (url) => deskHits.push(url), null, deskEv) === false,
  "desktop open false"
);
assert(deskEv.prevented !== true, "desktop does not preventDefault");
assert(deskHits.length === 0, "desktop no nav");

const iosHits = [];
const timers = [];
const clock = {
  now: () => 0,
  wait: (fn, ms) => {
    timers.push({ fn, ms });
    return timers.length;
  },
  cancel: () => {},
};
const iosEv = { preventDefault() {}, stopPropagation() {} };
assert(
  g.fccOpenRobinhood(ios, (url) => iosHits.push(url), clock, iosEv) === true,
  "ios open true"
);
assert(iosHits[0] === "robinhood://", "ios first nav is scheme");
assert(timers.length === 1 && timers[0].ms === 900, "ios fallback timer 900ms");
clock.now = () => 100;
timers[0].fn();
assert(iosHits[1] === "https://robinhood.com/", "ios web fallback not agentic");

console.log("ok nav-robinhood");
