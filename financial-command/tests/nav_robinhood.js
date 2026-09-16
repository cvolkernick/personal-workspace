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

function load(extra) {
  const sandbox = Object.assign(
    { window: {}, document: undefined, navigator: undefined },
    extra || {}
  );
  sandbox.globalThis = sandbox.window;
  if (!sandbox.window.document && sandbox.document) {
    sandbox.window.document = sandbox.document;
  }
  if (!sandbox.window.navigator && sandbox.navigator) {
    sandbox.window.navigator = sandbox.navigator;
  }
  vm.runInNewContext(SRC, vm.createContext(sandbox));
  return { sandbox, g: sandbox.window };
}

function fakeAnchor() {
  return {
    id: "nav-robinhood",
    href: "https://robinhood.com/agentic?classic=1",
    target: "_blank",
    attrs: {
      href: "https://robinhood.com/agentic?classic=1",
      target: "_blank",
    },
    setAttribute(k, v) {
      this.attrs[k] = v;
      this[k] = v;
    },
    removeAttribute(k) {
      delete this.attrs[k];
      if (k === "target") this.target = "";
      else delete this[k];
    },
    getAttribute(k) {
      return this.attrs[k];
    },
    closest(sel) {
      return sel === "#nav-robinhood" ? this : null;
    },
  };
}

const { g } = load();

const android =
  "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 Chrome/126.0.0.0 Mobile Safari/537.36 Brave/1.0";
const ios =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148";
const desktop =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36";
const braveDesktopUa =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36";

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
assert(g.fccRobinhoodIsMobileUa(braveDesktopUa) === false, "reduced UA is not mobile");
assert(g.fccRobinhoodIsMobile(android) === true, "isMobile accepts UA string");
assert(g.fccRobinhoodIsMobile(desktop) === false, "isMobile desktop UA string");

assert(
  g.fccRobinhoodIsMobile({ userAgent: desktop, userAgentData: { mobile: true } }) ===
    true,
  "userAgentData.mobile true is mobile"
);
assert(
  g.fccRobinhoodIsMobile({
    userAgent: braveDesktopUa,
    userAgentData: { mobile: true, platform: "Android" },
  }) === true,
  "Brave reduced UA + Client Hints mobile"
);
assert(
  g.fccRobinhoodIsMobile({ userAgent: android, userAgentData: { mobile: false } }) ===
    true,
  "Android UA still mobile when hints say false"
);
assert(
  g.fccRobinhoodIsMobile({ userAgent: desktop, userAgentData: { mobile: false } }) ===
    false,
  "desktop hints not mobile"
);

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
assert(
  g
    .fccRobinhoodLaunchHref({
      userAgent: braveDesktopUa,
      userAgentData: { mobile: true, platform: "Android" },
    })
    .indexOf("intent://") === 0,
  "hints Android → intent"
);
assert(
  g.fccRobinhoodLaunchHref({
    userAgent: braveDesktopUa,
    userAgentData: { mobile: true, platform: "iOS" },
  }) === "robinhood://",
  "hints iOS → scheme"
);
assert(
  g
    .fccRobinhoodLaunchHref({
      userAgent: braveDesktopUa,
      userAgentData: { mobile: true },
    })
    .indexOf("intent://") === 0,
  "mobile hint without platform defaults Android"
);

const androidNode = fakeAnchor();
assert(
  g.fccApplyRobinhoodAnchor(androidNode, android) === true,
  "android apply true"
);
assert(androidNode.getAttribute("href") === androidHref, "android href rewritten");
assert(androidNode.getAttribute("target") === undefined, "android target dropped");

const deskNode = fakeAnchor();
assert(g.fccApplyRobinhoodAnchor(deskNode, desktop) === false, "desktop apply false");
assert(
  deskNode.getAttribute("href") === "https://robinhood.com/agentic?classic=1",
  "desktop href unchanged"
);
assert(deskNode.getAttribute("target") === "_blank", "desktop target kept");

const androidHits = [];
const androidEv = {
  preventDefault() {
    this.prevented = true;
  },
  stopPropagation() {
    this.stopped = true;
  },
};
const tapNode = fakeAnchor();
assert(
  g.fccOpenRobinhood(android, (url) => androidHits.push(url), null, androidEv, tapNode) ===
    true,
  "android open true"
);
assert(androidEv.prevented !== true, "android does not preventDefault");
assert(androidEv.stopped !== true, "android does not stopPropagation");
assert(androidHits.length === 0, "android does not JS-navigate (href is the tap)");
assert(tapNode.getAttribute("href") === androidHref, "tap rewrites href");
assert(tapNode.getAttribute("target") === undefined, "tap drops target");

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
const iosEv = {
  preventDefault() {
    this.prevented = true;
  },
};
const iosNode = fakeAnchor();
assert(
  g.fccOpenRobinhood(ios, (url) => iosHits.push(url), clock, iosEv, iosNode) === true,
  "ios open true"
);
assert(iosNode.getAttribute("href") === "robinhood://", "ios href is scheme");
assert(iosNode.getAttribute("target") === undefined, "ios target dropped");
assert(iosEv.prevented !== true, "ios does not preventDefault");
assert(iosHits.length === 0, "ios does not JS-navigate the scheme");
assert(timers.length === 1 && timers[0].ms === 900, "ios fallback timer 900ms");
clock.now = () => 100;
timers[0].fn();
assert(iosHits[0] === "https://robinhood.com/", "ios web fallback not agentic");

const wiredNode = fakeAnchor();
const clicks = [];
const fakeDoc = {
  readyState: "complete",
  getElementById: (id) => (id === "nav-robinhood" ? wiredNode : null),
  addEventListener(type, fn) {
    clicks.push({ type, fn });
  },
};
const wiredNav = {
  userAgent: braveDesktopUa,
  userAgentData: { mobile: true, platform: "Android" },
};
const wired = load({ document: fakeDoc, navigator: wiredNav });
assert(
  wiredNode.getAttribute("href").indexOf("intent://") === 0,
  "wire rewrites href on load"
);
assert(wiredNode.getAttribute("target") === undefined, "wire drops target on load");
assert(
  clicks.some((c) => c.type === "click"),
  "delegated click on document"
);
const child = {
  closest(sel) {
    return sel === "#nav-robinhood" ? wiredNode : null;
  },
};
const lateNode = fakeAnchor();
const lateEv = {
  target: {
    closest(sel) {
      return sel === "#nav-robinhood" ? lateNode : null;
    },
  },
  preventDefault() {
    this.prevented = true;
  },
};
const clickFn = clicks.find((c) => c.type === "click").fn;
clickFn(lateEv);
assert(lateEv.prevented !== true, "delegated click does not preventDefault");
assert(
  lateNode.getAttribute("href").indexOf("intent://") === 0,
  "delegated click rewrites a later-painted node"
);
assert(lateNode.getAttribute("target") === undefined, "delegated click drops target");
clickFn({ target: child });
assert(typeof wired.g.fccWireRobinhoodNav === "function", "exports wire");

console.log("ok nav-robinhood");
