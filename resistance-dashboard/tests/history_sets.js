#!/usr/bin/env node
/**
 * Workout history lines must include weight × sets × reps next to tonnage.
 */
"use strict";

const hist = require("../static/history-sets.js");

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

assert(hist.fmtWeightLbs(225) === "225", "integer weight has no decimal");
assert(hist.fmtWeightLbs(47.5) === "47.5", "plate increment keeps decimal");
assert(hist.formatSet({ weight_lbs: 50, sets: 3, reps: 10 }) === "50 lbs x 3 x 10", "triple");

const multi = hist.formatExerciseLine({
  name: "DB Flat Press",
  volume: 1620,
  sets: [
    { weight_lbs: 50, sets: 1, reps: 12 },
    { weight_lbs: 45, sets: 1, reps: 12 },
    { weight_lbs: 40, sets: 1, reps: 12 },
  ],
});
assert(
  multi ===
    "DB Flat Press (50 lbs x 1 x 12, 45 lbs x 1 x 12, 40 lbs x 1 x 12 · 1,620 lb tonnage)" ||
    multi ===
      "DB Flat Press (50 lbs x 1 x 12, 45 lbs x 1 x 12, 40 lbs x 1 x 12 · 1620 lb tonnage)",
  "multi-load next to tonnage, got: " + multi
);

const pr = hist.formatExerciseLine({
  name: "Tricep Pushdowns",
  is_pr: true,
  volume: 1710,
  sets_label: "47.5 lbs x 3 x 12",
});
assert(pr.indexOf("Tricep Pushdowns (PR)") === 0, "PR suffix on name");
assert(pr.indexOf("47.5 lbs x 3 x 12") !== -1, "uses sets_label from API");
assert(pr.indexOf("lb tonnage") !== -1, "labels the pound total as tonnage");
assert(pr.indexOf(" vol)") === -1, "drops the vol suffix");

const volOnly = hist.formatExerciseLine({ name: "Unknown", volume: 100 });
assert(
  volOnly === "Unknown (100 lb tonnage)",
  "tonnage fallback, got: " + volOnly
);

console.log("ok history-sets");
