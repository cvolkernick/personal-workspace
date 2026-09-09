#!/usr/bin/env node
/**
 * #546: wake-window pace numbers stay separate from calendar-day totals.
 * Extracts helpers from app.js.
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

function loadFn(name, prelude) {
  return new Function(`${prelude || ""}; ${extractFn(name)}; return ${name};`)();
}

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

const fmtNum = loadFn("fmtNum");
const paceRowIntake = loadFn("paceRowIntake");
const loggedTodayCalendarLabel = loadFn("loggedTodayCalendarLabel");
const formatLoggedTodayCalendarLine = loadFn(
  "formatLoggedTodayCalendarLine",
  extractFn("fmtNum")
);

assert(loggedTodayCalendarLabel() === "logged today (calendar day)", "calendar label");

assert(paceRowIntake({ consumed: 1400 }, 200) === 1400, "prefers window consumed");
assert(paceRowIntake({ consumed: 0 }, 500) === 0, "zero window intake is real");
assert(paceRowIntake(null, 200) === 200, "no pace falls back to civil");
assert(paceRowIntake({}, 200) === 200, "missing consumed falls back");
assert(paceRowIntake({ consumed: "x" }, 200) === 200, "NaN consumed falls back");

const line = formatLoggedTodayCalendarLine({
  calories: 200,
  protein_g: 10,
  carbs_g: 20,
  fat_g: 5,
});
assert(
  line === "Logged today (calendar day): 200 kcal · 10g P · 20g C · 5g F",
  "full civil line: " + line
);

assert(formatLoggedTodayCalendarLine(null) === "", "null civil is empty");
assert(formatLoggedTodayCalendarLine({}) === "", "empty civil invents nothing");
assert(
  formatLoggedTodayCalendarLine({ calories: 800 }) ===
    "Logged today (calendar day): 800 kcal",
  "calories-only does not invent macros"
);

assert(SRC.includes("pctSuffix: \" target hit\""), "calorie tile labels target hit");
assert(SRC.includes("wake-window intake"), "legend says wake-window");
assert(SRC.includes("After bedtime, pace uses the calendar day"), "after-empty copy");

console.log("ok pace-civil-day-labels");
