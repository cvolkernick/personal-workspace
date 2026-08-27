#!/usr/bin/env node
/**
 * Energy vs scale: 1.25 lb tight match; 55% relative only inside 5 lb cap.
 * |gap| > 5 lb is never "Lines up". Do not deepen the cut on a 10 lb gap.
 */
"use strict";

const align = require("../static/energy-weight-align.js");

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

assert(align.TIGHT_LB === 1.25, "tight match stays 1.25 lb");
assert(align.CAP_LB === 5, "don't-panic cap is 5 lb");
assert(align.KCAL_PER_LB === 3500, "Wishnofsky 3500 kcal/lb");

assert(align.isAligned(0, 10) === true, "zero gap lines up");
assert(align.isAligned(1.25, 20) === true, "1.25 lb tight match still lines up");
assert(align.isAligned(1.0, 0.2) === true, "tight match does not need 55% relative");
assert(align.isAligned(2.0, 4.0) === true, "2 lb of 4 lb expected (50%) inside cap");
assert(align.isAligned(5.0, 10.0) === true, "5.0 lb at the cap can still use 55%");
assert(align.isAligned(5.01, 10.0) === false, "|gap| > 5 lb never lines up (50% of expected)");
assert(
  align.isAligned(11.7, 21.9) === false,
  "75d live gap 11.7/21.9 ≈ 53% must not pass as aligned"
);
assert(align.isAligned(3.0, 4.0) === false, "75% relative is not 55% even under the cap");
assert(align.isAligned(2.0, 0.5) === false, "relative clause needs |expected| ≥ 0.75");

const corrupt75 = align.energyWeightAlignment({
  cumDeltaKcal: -76758,
  pairDays: 74,
  weights: [
    { date: "2026-06-14", weight_lbs: 183.0 },
    { date: "2026-08-26", weight_lbs: 172.8 },
  ],
  windowStart: "2026-06-14",
  windowEnd: "2026-08-27",
  goalHint: "cut",
});
assert(corrupt75, "75d-like payload returns insight");
assert(corrupt75.status !== "aligned", "11.7 lb gap is never Lines up");
assert(Math.abs(corrupt75.residualLb) > 5, "fixture residual is outside the 5 lb cap");
assert(
  corrupt75.advice.join(" ").indexOf("Do not deepen the cut") !== -1,
  "do not deepen the cut on a 10 lb gap"
);
assert(
  corrupt75.advice.join(" ").indexOf("increase the true deficit") === -1,
  "corrupt-gap advice does not recommend a deeper cut"
);

const tight = align.energyWeightAlignment({
  cumDeltaKcal: -3500,
  pairDays: 10,
  weights: [
    { date: "2026-08-01", weight_lbs: 180.0 },
    { date: "2026-08-20", weight_lbs: 179.0 },
  ],
  windowStart: "2026-08-01",
  windowEnd: "2026-08-27",
  goalHint: "cut",
});
assert(tight && tight.status === "aligned", "1 lb actual vs 1 lb expected still Lines up");
assert(Math.abs(tight.residualLb) < 0.01, "tight fixture residual ~0");

const underCap = align.energyWeightAlignment({
  cumDeltaKcal: -14000,
  pairDays: 10,
  weights: [
    { date: "2026-08-01", weight_lbs: 180.0 },
    { date: "2026-08-20", weight_lbs: 178.0 },
  ],
  windowStart: "2026-08-01",
  windowEnd: "2026-08-27",
  goalHint: "cut",
});
assert(underCap, "under-cap payload returns insight");
assert(
  Math.abs(underCap.residualLb - 2.0) < 1e-9,
  "expected −4 lb, scale −2 lb → +2 lb gap"
);
assert(underCap.status === "aligned", "2 lb of 4 lb expected still Lines up inside the cap");

const empty = align.energyWeightAlignment({
  cumDeltaKcal: -10000,
  pairDays: 2,
  weights: [
    { date: "2026-08-01", weight_lbs: 180 },
    { date: "2026-08-20", weight_lbs: 178 },
  ],
  windowStart: "2026-08-01",
  windowEnd: "2026-08-27",
});
assert(empty === null, "pairDays < 5 is not enough");

console.log("ok energy-weight-align");
