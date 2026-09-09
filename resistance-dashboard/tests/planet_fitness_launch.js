#!/usr/bin/env node
/**
 * Behavioral check: Planet Fitness launch hrefs + desktop no-op.
 * Extracts helpers from static/app.js (no browser harness).
 */
"use strict";

const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "static", "app.js"),
  "utf8"
);

function extractFn(name) {
  let start = SRC.indexOf(`function ${name}`);
  if (start < 0) throw new Error("missing function " + name);
  if (start >= 6 && SRC.slice(start - 6, start) === "async ") start -= 6;
  const brace = SRC.indexOf("{", start);
  let depth = 0;
  for (let i = brace; i < SRC.length; i++) {
    const ch = SRC[i];
    if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) return SRC.slice(start, i + 1);
    }
  }
  throw new Error("unclosed function " + name);
}

function loadLaunchFns() {
  const constsStart = SRC.indexOf("const PF_ANDROID_PACKAGE");
  const hrefStart = SRC.indexOf("function planetFitnessLaunchHref");
  if (constsStart < 0 || hrefStart < 0) {
    throw new Error("missing PF launch block");
  }
  const consts = SRC.slice(constsStart, hrefStart);
  const body = [
    consts,
    extractFn("planetFitnessLaunchHref"),
    extractFn("planetFitnessIosStoreHref"),
    extractFn("openPlanetFitnessApp"),
    "return { planetFitnessLaunchHref, planetFitnessIosStoreHref, openPlanetFitnessApp };",
  ].join("\n");
  return new Function(body)();
}

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

const {
  planetFitnessLaunchHref,
  planetFitnessIosStoreHref,
  openPlanetFitnessApp,
} = loadLaunchFns();

const android =
  "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 Chrome/126.0.0.0 Mobile Safari/537.36";
const ios =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148";
const desktop =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36";

const androidHref = planetFitnessLaunchHref(android);
assert(androidHref.indexOf("intent://") === 0, "android starts with intent://");
assert(androidHref.indexOf("package=com.planetfitness") >= 0, "android package");
assert(
  androidHref.indexOf("play.google.com/store/apps/details?id=com.planetfitness") >= 0,
  "android play fallback"
);

assert(planetFitnessLaunchHref(ios) === "planetfitness://", "ios scheme");
assert(
  planetFitnessIosStoreHref() === "https://apps.apple.com/app/id399857015",
  "ios store"
);
assert(planetFitnessLaunchHref(desktop) === "", "desktop empty href");
assert(planetFitnessLaunchHref("") === "", "empty ua");

const androidHits = [];
assert(
  openPlanetFitnessApp(android, (url) => androidHits.push(url)) === true,
  "android open true"
);
assert(androidHits.length === 1, "android one nav");
assert(androidHits[0] === androidHref, "android nav href");

const deskHits = [];
assert(
  openPlanetFitnessApp(desktop, (url) => deskHits.push(url)) === false,
  "desktop open false"
);
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
assert(
  openPlanetFitnessApp(ios, (url) => iosHits.push(url), clock) === true,
  "ios open true"
);
assert(iosHits[0] === "planetfitness://", "ios first nav is scheme");
assert(timers.length === 1 && timers[0].ms === 900, "ios fallback timer 900ms");
clock.now = () => 100;
timers[0].fn();
assert(iosHits[1] === "https://apps.apple.com/app/id399857015", "ios store fallback");

console.log("ok planet-fitness-launch");
