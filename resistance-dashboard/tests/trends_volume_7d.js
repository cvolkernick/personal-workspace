#!/usr/bin/env node
/**
 * #752: Daily Volume 7d rolling mean is trailing weekly volume.
 * Rest days of 0 stay in the window. Partial window at the series start.
 * Must match rollingAverage(values, window) in static/app.js.
 */
"use strict";

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

function almostEqual(a, b, msg) {
  assert(Math.abs(a - b) < 1e-9, msg + " got " + a + " expected " + b);
}

/** Copy of app.js rollingAverage — trailing mean of finite points. */
function rollingAverage(values, window) {
  const out = [];
  for (let i = 0; i < values.length; i++) {
    const slice = [];
    for (let j = Math.max(0, i - window + 1); j <= i; j++) {
      const v = values[j];
      if (v != null && !Number.isNaN(Number(v))) slice.push(Number(v));
    }
    out.push(slice.length ? slice.reduce((s, x) => s + x, 0) / slice.length : null);
  }
  return out;
}

const week = [10000, 0, 0, 12000, 0, 8000, 0];
const roll = rollingAverage(week, 7);
almostEqual(roll[0], 10000, "day 1 partial window is that day");
almostEqual(roll[1], 5000, "day 2 mean includes rest-day 0");
almostEqual(roll[6], 30000 / 7, "day 7 = mean of that day + previous 6, zeros included");
assert(roll[6] < 10000, "rest zeros pull trailing weekly volume below training-day-only mean");

const onlyTrain = (10000 + 12000 + 8000) / 3;
assert(Math.abs(onlyTrain - 10000) < 1e-9, "training-days-only mean is 10000");
assert(roll[6] !== onlyTrain, "7d volume mean is not training-days-only");

const empty = rollingAverage([], 7);
assert(empty.length === 0, "empty series stays empty");

const nineties = [];
for (let i = 0; i < 90; i++) nineties.push(i % 3 === 0 ? 9000 : 0);
const roll90 = rollingAverage(nineties, 7);
assert(roll90.length === 90, "90d series stays 90 points");
almostEqual(
  roll90[89],
  rollingAverage(nineties.slice(83), 7)[6],
  "last point uses only the final 7 days"
);

console.log("ok trends-volume-7d");
