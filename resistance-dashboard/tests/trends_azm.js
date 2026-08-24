#!/usr/bin/env node
/**
 * #299: weekly AZM is a 7d trailing sum of existing daily points.
 * Missing / empty → honest "—". Never invent from burned kcal / steps.
 */
"use strict";

const path = require("path");
const azm = require("../static/trends-azm.js");

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

function almostEqual(a, b, msg) {
  assert(Math.abs(a - b) < 1e-9, msg + " got " + a + " expected " + b);
}

assert(azm.SPAN_DAYS === 7, "weekly window is 7 civil days");

const now = new Date(2026, 7, 23, 12, 0, 0);
const labels = azm.windowLabels(7, now);
assert(labels.length === 7, "7 civil labels");
assert(labels[labels.length - 1] === "2026-08-23", "window ends today");
assert(labels[0] === "2026-08-17", "window starts 6 days earlier");

const assayLike = [
  { date: "2026-08-11", total_minutes: 10 },
  { date: "2026-08-16", total_minutes: 22 },
  { date: "2026-08-17", total_minutes: 18 },
  { date: "2026-08-18", total_minutes: 24 },
  { date: "2026-08-19", total_minutes: 12 },
  { date: "2026-08-20", total_minutes: 30 },
  { date: "2026-08-21", total_minutes: 16 },
];

const present = azm.weeklyAzm(assayLike, now);
assert(present.pointDays === 5, "only days inside the 7d window count");
almostEqual(
  present.weeklySum,
  18 + 24 + 12 + 30 + 16,
  "weekly is 7d trailing sum of present total_minutes"
);
assert(present.daily[0] === 18, "2026-08-17 is first in-window day");
assert(present.daily[5] === null, "2026-08-22 missing stays null, not 0");
assert(present.daily[6] === null, "2026-08-23 missing stays null, not 0");
assert(azm.formatWeekly(present.weeklySum) === "100", "present formats the sum");

const empty = azm.weeklyAzm([], now);
assert(empty.weeklySum === null, "empty list is null, not 0");
assert(empty.pointDays === 0, "empty has no point days");
assert(azm.formatWeekly(empty.weeklySum) === "—", "empty formats as em dash");

const missing = azm.weeklyAzm(null, now);
assert(missing.weeklySum === null, "null points stay honest empty");
assert(azm.formatWeekly(missing.weeklySum) === "—", "null formats as em dash");

const outsideOnly = azm.weeklyAzm(
  [
    { date: "2026-08-11", total_minutes: 99 },
    { date: "2026-08-16", total_minutes: 88 },
  ],
  now
);
assert(outsideOnly.weeklySum === null, "points outside 7d do not invent a weekly");
assert(azm.formatWeekly(outsideOnly.weeklySum) === "—", "out-of-window is em dash");

const zeroDay = azm.weeklyAzm([{ date: "2026-08-23", total_minutes: 0 }], now);
assert(zeroDay.weeklySum === 0, "a real 0-minute day is a present point");
assert(azm.formatWeekly(0) === "0", "zero minutes formats as 0, not em dash");

const noTotal = azm.weeklyAzm(
  [
    { date: "2026-08-23", fat_burn_minutes: 12, cardio_minutes: 8 },
    { date: "2026-08-22", steps: 8000, calories: 2400 },
  ],
  now
);
assert(noTotal.weeklySum === null, "does not invent total from zones / steps / kcal");

assert(azm.azmPoints({}).length === 0, "missing health key is []");
assert(azm.azmPoints({ health: {} }).length === 0, "missing AZM key is []");
assert(
  azm.azmPoints({
    health: { calories_burned: [{ date: "2026-08-23", calories: 2400 }] },
  }).length === 0,
  "does not read burned kcal as AZM"
);
assert(
  azm.azmPoints({ health: { active_zone_minutes: assayLike } }).length === 7,
  "reads health.active_zone_minutes"
);
assert(
  azm.azmPoints({ today: { active_zone_minutes: assayLike } }).length === 7,
  "can reuse agent Today slice of the same field"
);

assert(azm.sparklineSvg(present.daily).indexOf("<svg") === 0, "present days get a sparkline");
assert(azm.sparklineSvg(empty.daily) === "", "no sparkline when no points");
assert(azm.sparklineSvg(present.daily).indexOf("99") === -1, "sparkline does not invent out-of-window values");

const els = {};
function makeEl(id) {
  const el = { id: id, textContent: "", innerHTML: "" };
  els[id] = el;
  return el;
}
global.document = {
  getElementById: function (id) {
    return els[id] || null;
  },
};
makeEl("azm-trend-card");
makeEl("azm-week-value");
makeEl("azm-week-sub");
makeEl("azm-trend-note");
makeEl("azm-sparkline");

const painted = azm.paintAzmCard({ health: { active_zone_minutes: assayLike } }, now);
assert(painted && painted.weeklySum === 100, "paint uses 7d trailing sum");
assert(els["azm-week-value"].textContent === "100", "card shows weekly sum");
assert(els["azm-week-sub"].textContent.indexOf("5 days") !== -1, "sub labels present days");
assert(els["azm-trend-note"].textContent.indexOf("7d trailing sum") !== -1, "note documents 7d trailing");
assert(els["azm-sparkline"].innerHTML.indexOf("<svg") !== -1, "sparkline paints from the same points");
assert(els["azm-sparkline"].innerHTML.indexOf("chicken") === -1, "does not invent food");
assert(els["azm-week-value"].textContent.indexOf("2400") === -1, "does not show burned kcal");

const emptyPaint = azm.paintAzmCard({ health: { active_zone_minutes: [] } }, now);
assert(emptyPaint.weeklySum === null, "empty paint weekly is null");
assert(els["azm-week-value"].textContent === "—", "empty card is em dash");
assert(els["azm-sparkline"].innerHTML === "", "empty card has no invented sparkline");
assert(
  els["azm-trend-note"].textContent.indexOf("No Active Zone Minutes") !== -1,
  "empty note is honest"
);

const burnedOnly = azm.paintAzmCard(
  {
    health: {
      calories_burned: [{ date: "2026-08-23", calories: 2400 }],
      nutrition: [{ date: "2026-08-23", calories: 2000 }],
    },
  },
  now
);
assert(burnedOnly.weeklySum === null, "burned-only payload does not invent AZM");
assert(els["azm-week-value"].textContent === "—", "burned-only stays em dash");

console.log("ok trends-azm");
