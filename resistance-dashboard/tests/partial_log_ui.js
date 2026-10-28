#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const APP = fs.readFileSync(path.join(ROOT, "static", "app.js"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "static", "index.html"), "utf8");

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL", msg);
    process.exit(1);
  }
}

assert(HTML.includes(">Add to log<"), "button reads Add to log");
assert(HTML.includes('id="btn-finish"'), "finish button exists");
assert(HTML.includes(">Finish workout<"), "finish label");
assert(!HTML.includes(">Save workout<"), "old save label is gone");
assert(APP.includes("function logDayISO"), "log day helper");
assert(APP.includes("function finishWorkout"), "finish handler");
assert(APP.includes("submission_id"), "duplicate submit id");
assert(APP.includes("workout_progress"), "dashboard progress");
assert(APP.includes("Training in progress"), "adherence progress line");

function extractFn(name) {
  let start = APP.indexOf(`function ${name}`);
  if (start < 0) throw new Error("missing " + name);
  const brace = APP.indexOf("{", start);
  let depth = 0;
  for (let j = brace; j < APP.length; j++) {
    if (APP[j] === "{") depth += 1;
    else if (APP[j] === "}") {
      depth -= 1;
      if (depth === 0) return APP.slice(start, j + 1);
    }
  }
  throw new Error("unclosed " + name);
}

const logDayISO = new Function(
  "now",
  extractFn("logDayISO").replace("function logDayISO(now)", "return (function (now)") + ")(now);"
);

// 23:36 ET on 2026-10-27 is 03:36 UTC on 2026-10-28 (EDT).
assert(logDayISO(new Date("2026-10-28T03:36:00Z")) === "2026-10-27", "23:36 ET stays on that evening");
// 23:36 EST is 04:36 UTC the next morning — still the ET evening, not the UTC date.
assert(logDayISO(new Date("2026-01-16T04:36:00Z")) === "2026-01-15", "23:36 EST is not the next UTC date");
// 03:30 ET is before the 4 AM cutoff, so the training day is still yesterday.
assert(logDayISO(new Date("2026-10-28T07:30:00Z")) === "2026-10-27", "03:30 ET uses the cutoff");

console.log("partial_log_ui: ok");
