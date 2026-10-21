#!/usr/bin/env node
/** Quiet poll every 30 min; refresh-on-focus at most every 10 min (Vercel CPU fix). */
"use strict";
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const SRC = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8");

assert.ok(/const QUIET_POLL_MS = 30 \* 60 \* 1000;/.test(SRC), "QUIET_POLL_MS must be 30 min");
assert.ok(/const FOCUS_REFRESH_MIN_MS = 10 \* 60 \* 1000;/.test(SRC), "focus cap must be 10 min");
assert.ok(/tick\(FOCUS_REFRESH_MIN_MS\)/.test(SRC), "visibilitychange must use the focus cap");

const start = SRC.indexOf("function shouldQuietRefresh");
const end = SRC.indexOf("}", SRC.indexOf("{", start)) + 1;
const shouldQuietRefresh = new Function(SRC.slice(start, end) + "; return shouldQuietRefresh;")();
const MIN = 60 * 1000;
assert.strictEqual(shouldQuietRefresh(1000, 0, 10 * MIN), true, "first refresh allowed");
assert.strictEqual(shouldQuietRefresh(9 * MIN, 1, 10 * MIN), false, "focus within 10 min skipped");
assert.strictEqual(shouldQuietRefresh(10 * MIN + 1, 1, 10 * MIN), true, "focus after 10 min allowed");
assert.strictEqual(shouldQuietRefresh(5, 4, 0), true, "interval tick always allowed");
console.log("quiet_poll_throttle: ok");
