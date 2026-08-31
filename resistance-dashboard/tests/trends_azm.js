#!/usr/bin/env node
/**
 * #392: Trends AZM is 90 civil days of daily total_minutes, a 7d rolling
 * average of present points, and an OLS trendline on those daily points.
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

assert(azm.SPAN_DAYS === 90, "window is 90 civil days");
assert(azm.ROLL_DAYS === 7, "rolling average is 7 days");
assert(azm.TARGET_LOOKBACK_DAYS === 14, "target lookback is 14 days");
assert(azm.AZM_TARGET_FLOOR === 10, "target floor is 10");
assert(azm.AZM_TARGET_CAP === 45, "target cap is 45");
assert(azm.DEFAULT_AZM_TARGET === 20, "empty history defaults to 20");
assert(azm.SERIES_COLORS.daily === "#8b9bb4", "daily is muted gray");
assert(azm.SERIES_COLORS.roll === "#3d9cf0", "rolling avg is house blue");
assert(azm.SERIES_COLORS.trend === "#f07178", "trendline is house coral");
assert(azm.SERIES_COLORS.target === "#5ce1a8", "target is house mint");
assert(azm.azmTargetMinutes([]) === 20, "empty recent uses default 20");
assert(azm.azmTargetMinutes([10, 10, 10]) === 10, "median at floor stays 10");
assert(azm.azmTargetMinutes([80, 80, 80]) === 45, "median above cap is 45");
assert(azm.azmTargetMinutes([20, 21]) === 20, "even median 20.5 ties-to-even like Python");
assert(azm.pyRound(20.5) === 20, "Python 3 round half-even on 20.5");
assert(azm.pyRound(21.5) === 22, "Python 3 round half-even on 21.5");

const now = new Date(2026, 7, 27, 12, 0, 0);
const labels = azm.windowLabels(90, now);
assert(labels.length === 90, "90 civil labels");
assert(labels[labels.length - 1] === "2026-08-27", "window ends today");
assert(labels[0] === "2026-05-30", "window starts 89 days earlier");

const assayLike = [
  { date: "2026-05-20", total_minutes: 413 },
  { date: "2026-08-11", total_minutes: 10 },
  { date: "2026-08-16", total_minutes: 22 },
  { date: "2026-08-17", total_minutes: 18 },
  { date: "2026-08-18", total_minutes: 24 },
  { date: "2026-08-19", total_minutes: 12 },
  { date: "2026-08-20", total_minutes: 30 },
  { date: "2026-08-21", total_minutes: 16 },
];

const present = azm.azmSeries(assayLike, now);
assert(present.spanDays === 90, "series reports 90d span");
assert(present.pointDays === 7, "only days inside the 90d window count");
assert(present.daily.length === 90, "daily series is one slot per civil day");
assert(present.daily[0] === null, "2026-05-30 missing stays null, not 0");
assert(present.labels.indexOf("2026-08-11") !== -1, "Aug 11 is in the 90d window");
assert(present.daily[present.labels.indexOf("2026-08-11")] === 10, "in-window Aug 11 is 10");
assert(present.daily[present.labels.indexOf("2026-08-22")] === null, "2026-08-22 missing stays null, not 0");
assert(present.daily[present.labels.indexOf("2026-08-27")] === null, "2026-08-27 missing stays null, not 0");
assert(present.daily.indexOf(413) === -1, "out-of-window May 20 is not invented into the series");

const idx21 = present.labels.indexOf("2026-08-21");
almostEqual(
  present.rolling7[idx21],
  (22 + 18 + 24 + 12 + 30 + 16) / 6,
  "7d rolling avg on Aug 21 averages present points in the last 7 civil days (gap days omitted, not 0)"
);
assert(present.lastRolling7 != null, "latest rolling avg is present");
assert(azm.formatRolling(present.lastRolling7) !== "—", "present formats the rolling avg");
assert(present.target === 20, "14d median of in-window days (Aug 13–26) is 20; Aug 11 is outside lookback");
assert(
  azm.recentAzmMinutes(present.daily, present.labels).indexOf(10) === -1,
  "Aug 11 is in 90d series but excluded from 14d target lookback"
);
assert(
  azm.recentAzmMinutes(present.daily, present.labels).indexOf(413) === -1,
  "out-of-window 413 does not enter the target median"
);

const rollWindow = azm.rollingAverage([10, null, 20, 30], 7);
almostEqual(rollWindow[0], 10, "first present point is its own rolling avg");
almostEqual(rollWindow[1], 10, "gap day still carries trailing avg of present neighbors");
almostEqual(rollWindow[3], (10 + 20 + 30) / 3, "rolling avg never treats null as 0");

const trend = azm.linearTrend([10, 20, 30]);
almostEqual(trend[0], 10, "2-or-more present points get an OLS line");
almostEqual(trend[2], 30, "OLS through 10,20,30 is the line itself");
const gappyTrend = azm.linearTrend([10, null, 30]);
assert(gappyTrend[0] != null && gappyTrend[2] != null, "trend fits present points only");
almostEqual(gappyTrend[1], 20, "trend is evaluated on gap slots, not set to 0");
assert(
  azm.linearTrend([10, null, null]).every(function (v) {
    return v == null;
  }),
  "one present point is not a trendline"
);

assert(azm.formatRolling(null) === "—", "empty formats as em dash");
assert(azm.formatRolling(0) === "0", "zero minutes formats as 0, not em dash");

const empty = azm.azmSeries([], now);
assert(empty.lastRolling7 === null, "empty list is null, not 0");
assert(empty.pointDays === 0, "empty has no point days");
assert(empty.target === 20, "empty history still has the default 20 target");
assert(azm.formatRolling(empty.lastRolling7) === "—", "empty formats as em dash");
assert(
  empty.trend.every(function (v) {
    return v == null;
  }),
  "empty series has no invented trend"
);

const missing = azm.azmSeries(null, now);
assert(missing.lastRolling7 === null, "null points stay honest empty");
assert(azm.formatRolling(missing.lastRolling7) === "—", "null formats as em dash");

const outsideOnly = azm.azmSeries(
  [
    { date: "2026-05-20", total_minutes: 413 },
    { date: "2026-05-28", total_minutes: 88 },
  ],
  now
);
assert(outsideOnly.lastRolling7 === null, "points outside 90d do not invent a rolling avg");
assert(azm.formatRolling(outsideOnly.lastRolling7) === "—", "out-of-window is em dash");

const zeroDay = azm.azmSeries([{ date: "2026-08-27", total_minutes: 0 }], now);
assert(zeroDay.lastRolling7 === 0, "a real 0-minute day is a present point");
assert(zeroDay.pointDays === 1, "zero-minute day counts as a point day");

const noTotal = azm.azmSeries(
  [
    { date: "2026-08-27", fat_burn_minutes: 12, cardio_minutes: 8 },
    { date: "2026-08-26", steps: 8000, calories: 2400 },
  ],
  now
);
assert(noTotal.lastRolling7 === null, "does not invent total from zones / steps / kcal");

assert(azm.azmPoints({}).length === 0, "missing health key is []");
assert(azm.azmPoints({ health: {} }).length === 0, "missing AZM key is []");
assert(
  azm.azmPoints({
    health: { calories_burned: [{ date: "2026-08-27", calories: 2400 }] },
  }).length === 0,
  "does not read burned kcal as AZM"
);
assert(
  azm.azmPoints({ health: { active_zone_minutes: assayLike } }).length === 8,
  "reads health.active_zone_minutes"
);
assert(
  azm.azmPoints({ today: { active_zone_minutes: assayLike } }).length === 8,
  "can reuse agent Today slice of the same field"
);

assert(
  azm.sparklineSvg(present.daily, present.labels, present.rolling7, present.trend, present.target).indexOf("<svg") === 0,
  "present days get a sparkline"
);
assert(azm.sparklineSvg(empty.daily, empty.labels, empty.rolling7, empty.trend, empty.target) === "", "no sparkline when no points");
assert(
  azm.sparklineSvg(present.daily, present.labels, present.rolling7, present.trend, present.target).indexOf('data-y-max="413"') === -1,
  "Y max is not the out-of-window May 20 value"
);
assert(
  azm.sparklineSvg(present.daily, present.labels, present.rolling7, present.trend, present.target).indexOf(">413</text>") === -1,
  "no 413 tick from out-of-window May 20"
);

const spark = azm.sparklineSvg(present.daily, present.labels, present.rolling7, present.trend, present.target);
assert(spark.indexOf('data-y-min="0"') !== -1, "Y domain starts at 0");
assert(spark.indexOf('data-y-max="30"') !== -1, "Y top is this window's max present minutes (30)");
assert((spark.match(/class="azm-y"/g) || []).length === 2, "Y ticks: 0 and max only");
assert(spark.indexOf('class="azm-y"') !== -1 && spark.indexOf(">0</text>") !== -1, "Y floor label is 0");
assert(spark.indexOf(">30</text>") !== -1, "Y top label is 30 minutes, not a min-max stretch");
assert(spark.indexOf("azm-baseline") !== -1, "faint 0-baseline");
assert(spark.indexOf("azm-daily") !== -1, "daily series is drawn");
assert(spark.indexOf("azm-roll") !== -1, "7d rolling avg is drawn");
assert(spark.indexOf("azm-trend") !== -1, "trendline is drawn");
assert(spark.indexOf("azm-target") !== -1, "target line is drawn");
assert(spark.indexOf("stroke-dasharray") !== -1, "trendline is dashed");
assert(spark.indexOf("#3d9cf0") !== -1, "rolling avg stays #3d9cf0");
assert(spark.indexOf("#f07178") !== -1, "trendline stays house #f07178");
assert(spark.indexOf("#5ce1a8") !== -1, "target stays house #5ce1a8");
assert((spark.match(/<circle /g) || []).length === 0, "90d spark has no per-day dots");
const yTexts = [...spark.matchAll(/class="azm-y"[^>]*>([^<]+)/g)].map(function (m) {
  return m[1];
});
assert(yTexts.indexOf("0") !== -1 && yTexts.indexOf("30") !== -1, "Y ticks are 0 and 30");
assert(
  yTexts.every(function (t) {
    return t.toUpperCase().indexOf("AZM") === -1;
  }),
  "Y ticks are minutes only, no AZM word"
);

const ticks = azm.monthTickIndexes(present.labels);
assert(ticks.length >= 3, "90d window has several month ticks");
assert(azm.monthLabel(present.labels[0]) === "May", "window starts in May");
assert(spark.indexOf(">May</text>") === -1, "skip May 30 when it would sit on Jun 1");
assert(spark.indexOf(">Jun</text>") !== -1, "Jun tick");
assert(spark.indexOf(">Jul</text>") !== -1, "Jul tick");
assert(spark.indexOf(">Aug</text>") !== -1, "Aug tick");
assert((spark.match(/class="azm-x"/g) || []).length === ticks.length, "X labels are month ticks, not SMTWTFS");
assert(ticks[0] > 0, "first kept tick is Jun 1, not the May 30 start");
assert(spark.indexOf(">S</text>") === -1, "no weekday-letter X ticks on the 90d spark");
assert(spark.indexOf("chart.js") === -1, "still an SVG spark, not Chart.js");
assert(spark.indexOf("last 90 days") !== -1, "aria names the 90d window");
assert(spark.indexOf('height="100%"') !== -1, "svg height fills the chart box");
assert(spark.indexOf('preserveAspectRatio="none"') !== -1, "svg stretches to the box");
assert(spark.indexOf('height="80"') === -1, "no pinned 80px spark height");
assert(spark.indexOf('viewBox="0 0 720 220"') !== -1, "plot viewBox is card-sized, not 400x80");

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
makeEl("azm-target-value");
makeEl("azm-target-sub");
makeEl("azm-trend-note");
makeEl("azm-sparkline");

const painted = azm.paintAzmCard({ health: { active_zone_minutes: assayLike } }, now);
assert(painted && painted.lastRolling7 != null, "paint uses 7d rolling avg");
assert(els["azm-week-value"].textContent !== "—", "card shows rolling avg");
assert(els["azm-week-value"].textContent !== "132", "card is not a 7d trailing sum");
assert(els["azm-week-sub"].textContent.indexOf("90d") !== -1, "sub names the 90d window");
assert(els["azm-week-sub"].textContent.indexOf("7 days") !== -1, "sub labels present days");
assert(els["azm-trend-note"].textContent.indexOf("7d rolling avg") !== -1, "note documents 7d rolling avg");
assert(els["azm-trend-note"].textContent.indexOf("trendline") !== -1, "note names the trendline");
assert(els["azm-trend-note"].textContent.indexOf("7d trailing sum") === -1, "note dropped the 7d trailing sum");
assert(els["azm-sparkline"].innerHTML.indexOf("<svg") !== -1, "sparkline paints from the same points");
assert(els["azm-sparkline"].innerHTML.indexOf("azm-roll") !== -1, "painted spark includes rolling avg");
assert(els["azm-sparkline"].innerHTML.indexOf("azm-trend") !== -1, "painted spark includes trendline");
assert(els["azm-sparkline"].innerHTML.indexOf("azm-target") !== -1, "painted spark includes target line");
assert(els["azm-target-value"].textContent === "20", "chip shows 14d median target");
assert(els["azm-target-sub"].textContent.indexOf("14d") !== -1, "chip names 14d median");
assert(els["azm-trend-note"].textContent.indexOf("target 20") !== -1, "note names the target");
assert(els["azm-sparkline"].innerHTML.indexOf("chicken") === -1, "does not invent food");
assert(els["azm-week-value"].textContent.indexOf("2400") === -1, "does not show burned kcal");

const emptyPaint = azm.paintAzmCard({ health: { active_zone_minutes: [] } }, now);
assert(emptyPaint.lastRolling7 === null, "empty paint rolling is null");
assert(els["azm-week-value"].textContent === "—", "empty card is em dash");
assert(els["azm-sparkline"].innerHTML === "", "empty card has no invented sparkline");
assert(
  els["azm-trend-note"].textContent.indexOf("No Active Zone Minutes") !== -1,
  "empty note is honest"
);
assert(els["azm-trend-note"].textContent.indexOf("90 days") !== -1, "empty note names 90 days");
assert(els["azm-target-value"].textContent === "20", "empty card still shows default target 20");
assert(els["azm-sparkline"].innerHTML.indexOf("azm-target") === -1, "no invented spark/target line when no points");

const burnedOnly = azm.paintAzmCard(
  {
    health: {
      calories_burned: [{ date: "2026-08-27", calories: 2400 }],
      nutrition: [{ date: "2026-08-27", calories: 2000 }],
    },
  },
  now
);
assert(burnedOnly.lastRolling7 === null, "burned-only payload does not invent AZM");
assert(els["azm-week-value"].textContent === "—", "burned-only stays em dash");

const lowDays = [];
for (var d = 13; d <= 26; d++) {
  lowDays.push({ date: "2026-08-" + String(d).padStart(2, "0"), total_minutes: 5 });
}
const low = azm.azmSeries(lowDays, now);
assert(low.target === 10, "median 5 floors to 10");
const lowSpark = azm.sparklineSvg(low.daily, low.labels, low.rolling7, low.trend, low.target);
assert(lowSpark.indexOf('data-y-max="10"') !== -1, "Y max expands to the floor target when daily max is 5");
assert(lowSpark.indexOf("azm-target") !== -1, "target line still draws when it is the Y max");

const cardioNow = new Date(2026, 7, 31, 12, 0, 0);
const cardioDays = [
  { date: "2026-08-16", total_minutes: 10 },
  { date: "2026-08-17", total_minutes: 22 },
  { date: "2026-08-18", total_minutes: 18 },
  { date: "2026-08-19", total_minutes: 24 },
  { date: "2026-08-20", total_minutes: 12 },
  { date: "2026-08-21", total_minutes: 30 },
  { date: "2026-08-22", total_minutes: 16 },
  { date: "2026-08-23", total_minutes: 20 },
  { date: "2026-08-24", total_minutes: 28 },
  { date: "2026-08-25", total_minutes: 14 },
  { date: "2026-08-26", total_minutes: 26 },
  { date: "2026-08-27", total_minutes: 19 },
  { date: "2026-08-28", total_minutes: 21 },
  { date: "2026-08-29", total_minutes: 17 },
  { date: "2026-08-30", total_minutes: 23 },
  { date: "2026-08-31", total_minutes: 400 },
];
const cardio = azm.azmSeries(cardioDays, cardioNow);
assert(cardio.target === 20, "same 14d median as cardio_quest (20.5 ties-to-even → 20); today 400 excluded");
assert(
  azm.recentAzmMinutes(cardio.daily, cardio.labels).indexOf(400) === -1,
  "today is excluded from the Trends target median"
);
assert(
  azm.recentAzmMinutes(cardio.daily, cardio.labels).indexOf(10) === -1,
  "Aug 16 is before the 14d start (Aug 17) and stays out"
);

console.log("ok trends-azm");
