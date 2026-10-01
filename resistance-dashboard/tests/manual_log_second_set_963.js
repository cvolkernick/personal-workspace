#!/usr/bin/env node
/**
 * Logged-today shows every set group (#963). A second save must not
 * re-post the rows that were just stored.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "static", "app.js"), "utf8");

function extractFn(name) {
  let start = SRC.indexOf(`function ${name}`);
  if (start < 0) throw new Error("missing function " + name);
  if (start >= 6 && SRC.slice(start - 6, start) === "async ") start -= 6;
  let i = SRC.indexOf("(", start);
  let depth = 0;
  for (; i < SRC.length; i++) {
    const ch = SRC[i];
    if (ch === "(") depth += 1;
    else if (ch === ")") {
      depth -= 1;
      if (depth === 0) {
        i += 1;
        break;
      }
    }
  }
  const brace = SRC.indexOf("{", i);
  depth = 0;
  for (let j = brace; j < SRC.length; j++) {
    const ch = SRC[j];
    if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) return SRC.slice(start, j + 1);
    }
  }
  throw new Error("unclosed " + name);
}

const loggedLiftDetail = eval(`(${extractFn("loggedLiftDetail")})`);

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL", msg);
    process.exit(1);
  }
}

const both = loggedLiftDetail({
  name: "Barbell Flat Bench Press",
  sets: [
    { weight_lbs: 85, sets: 1, reps: 8 },
    { weight_lbs: 90, sets: 1, reps: 6 },
  ],
});
assert(both === "85 lb · 1×8, 90 lb · 1×6", both);

const one = loggedLiftDetail({
  sets: [{ weight_lbs: 85, sets: 1, reps: 8 }],
});
assert(one === "85 lb · 1×8", one);

const empty = loggedLiftDetail({ sets: [] });
assert(empty === "movement logged", empty);

const submit = extractFn("submitWorkout");
const resetAt = submit.indexOf("resetManualLogExercises()");
const reloadAt = submit.indexOf("await loadDashboard(false)");
assert(resetAt >= 0 && reloadAt > resetAt, "form resets before reload");

console.log("ok manual-log-second-set-963");
