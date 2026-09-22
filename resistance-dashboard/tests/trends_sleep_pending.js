#!/usr/bin/env node
/**
 * Trends sleep note: the open GH-lag night is a gap, not "counted as 0h".
 * A closed miss stays 0. A logged short night keeps its hours (#870).
 */
"use strict";

const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "static", "app.js"),
  "utf8"
);

function extractFn(name) {
  const start = SRC.indexOf(`function ${name}`);
  if (start < 0) throw new Error("missing function " + name);
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

function loadFn(name) {
  return new Function(`${extractFn(name)}; return ${name};`)();
}

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

const sleepTrendValue = loadFn("sleepTrendValue");
const countZeroSleepNights = loadFn("countZeroSleepNights");
const pendingOvernightDate = loadFn("pendingOvernightDate");

assert(
  sleepTrendValue(0, "2026-09-22", "2026-09-22") === null,
  "pending night with no sample is a gap"
);
assert(
  sleepTrendValue(0, "2026-09-21", "2026-09-22") === 0,
  "older miss still counts as 0h"
);
assert(
  sleepTrendValue(5.5, "2026-09-22", "2026-09-22") === 5.5,
  "logged short night keeps real hours"
);
assert(
  sleepTrendValue(0, "2026-09-22", "") === 0,
  "no pending date keeps the zero"
);

const vals = [
  sleepTrendValue(8, "2026-09-21", "2026-09-22"),
  sleepTrendValue(0, "2026-09-22", "2026-09-22"),
  sleepTrendValue(0, "2026-09-20", "2026-09-22"),
];
assert(countZeroSleepNights(vals) === 1, "note counts the real miss only");
assert(
  pendingOvernightDate({
    recovery: { inputs: { pending_overnight_date: "2026-09-22" } },
  }) === "2026-09-22",
  "Trends reads recovery.inputs.pending_overnight_date"
);
assert(
  SRC.includes("no log counted as 0h"),
  "Trends note still names zero-filled nights"
);
assert(
  SRC.includes("sleepTrendValue(") && SRC.includes("countZeroSleepNights("),
  "chart uses the pending-night gap"
);

console.log("trends_sleep_pending: ok");
