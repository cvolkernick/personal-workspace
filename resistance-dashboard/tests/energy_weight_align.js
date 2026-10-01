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

/**
 * #961 — predicted-vs-actual line, implied burn, weekly review.
 * Existing alignment numbers stay on energyWeightAlignment.
 */
const roll = require("../static/calorie-rolling-avg.js");

function daysFrom(start, n) {
  const out = [];
  let d = start;
  for (let i = 0; i < n; i++) {
    out.push(d);
    d = align.addDays(d, 1);
  }
  return out;
}

const labels = daysFrom("2026-08-01", 35);
assert(labels[34] === "2026-09-04", "35-day window ends 2026-09-04");
assert(labels[7] === "2026-08-08", "index 7 is Aug 8");
assert(labels[27] === "2026-08-28", "index 27 is Aug 28");

const intakeRows = [];
const burnedRows = [];
for (let i = 0; i < 28; i++) {
  intakeRows.push({ date: labels[i], calories: 2200 });
  burnedRows.push({ date: labels[i], calories: 2500 });
}
for (let i = 28; i < 35; i++) {
  burnedRows.push({ date: labels[i], calories: 3000 });
}
const weights = [
  { date: labels[0], weight_lbs: 180 },
  { date: labels[7], weight_lbs: 179 },
  { date: labels[34], weight_lbs: 176 },
];

const ext = align.energyScaleExtension({
  labels,
  intakeRows,
  burnedRows,
  weights,
});
assert(ext.open === true, "28/35 logged and a real weigh-in span opens the line");
assert(Math.abs(ext.cumDeltaKcal - -8400) < 1e-6, "unlogged burn days add nothing; cum is 28×-300");
assert(ext.pairDays === 28, "pair days stay the 28 logged days");
assert(Math.abs(ext.finalPredicted - -2.4) <= 0.1, "predicted final is -2.4 lb");
assert(Math.abs(ext.finalActual - -4) <= 0.1, "scale final is -4.0 lb");

const card = align.energyWeightAlignment({
  cumDeltaKcal: ext.cumDeltaKcal,
  pairDays: ext.pairDays,
  weights,
  windowStart: labels[0],
  windowEnd: labels[34],
  goalHint: "cut",
});
assert(card, "existing card still returns for this window");
assert(Math.abs(ext.finalPredicted - card.expectedLb) <= 0.1, "chart end matches From calories");
assert(Math.abs(ext.finalActual - card.actualLb) <= 0.1, "chart end matches On scale");
assert(
  Math.abs(card.actualLb - card.expectedLb - card.residualLb) < 1e-6,
  "gap chip is still scale − expected"
);

const handImplied = 2200 - (-4 * 3500) / 35;
assert(Math.abs(ext.implied.burnPerDay - handImplied) <= 1, "implied burn matches the fixture formula");
assert(Math.abs(ext.implied.burnPerDay - 2600) <= 1, "implied burn is 2600 kcal/day");
assert(Math.abs(ext.implied.loggedBurnPerDay - 2500) <= 1, "logged burn stays 2500");
assert(Math.abs(ext.implied.diffPerDay - 100) <= 1, "difference is +100 kcal/day");
assert(ext.implied.delta7Lb === -4, "7-day average weight change is -4 lb");

assert(ext.weekly && ext.weekly.days === 28, "weekly review is the trailing 28 days");
assert(Math.abs(ext.weekly.gapLb - -1.2) <= 0.1, "4-week gap is -1.2 lb");
assert(Math.abs(ext.weekly.impliedBurn - 2575) <= 1, "4-week implied burn is 2575");
assert(Math.abs(ext.weekly.loggedBurn - 2500) <= 1, "4-week logged burn is 2500");

const chartBurned = roll.valuesOnLabels(roll.kcalByDate(burnedRows), labels);
assert(
  JSON.stringify(ext.burnedSeries) === JSON.stringify(chartBurned),
  "burned series matches the intake-vs-burned daily points"
);

const bareLabels = labels.slice(0, 28);
const bare = align.energyScaleExtension({
  labels: bareLabels,
  intakeRows: intakeRows.slice(),
  burnedRows: burnedRows.filter((row) => row.date <= labels[27]),
  weights,
});
assert(bare.cumDeltaKcal === ext.cumDeltaKcal, "10-style: extra unlogged days do not change the sum");
assert(
  Math.abs(bare.finalPredicted - ext.finalPredicted) < 1e-9,
  "predicted change matches the logged days alone"
);

const holeLabels = daysFrom("2026-07-01", 14);
const holeIn = [];
const holeBurn = [];
for (let i = 0; i < 10; i++) {
  holeIn.push({ date: holeLabels[i], calories: 2000 });
  holeBurn.push({ date: holeLabels[i], calories: 1800 });
}
for (let i = 10; i < 14; i++) holeBurn.push({ date: holeLabels[i], calories: 4000 });
const holes = align.energyScaleExtension({
  labels: holeLabels,
  intakeRows: holeIn,
  burnedRows: holeBurn,
  weights: [
    { date: holeLabels[0], weight_lbs: 180 },
    { date: holeLabels[13], weight_lbs: 179 },
  ],
});
const only = align.energyScaleExtension({
  labels: holeLabels.slice(0, 10),
  intakeRows: holeIn,
  burnedRows: holeBurn.slice(0, 10),
  weights: [
    { date: holeLabels[0], weight_lbs: 180 },
    { date: holeLabels[9], weight_lbs: 179 },
  ],
});
assert(holes.cumDeltaKcal === only.cumDeltaKcal, "10 logged + 4 unlogged equals the 10 logged days");
assert(Math.abs(holes.finalPredicted - only.finalPredicted) < 1e-9, "unlogged days do not move the line");
assert(holes.open === false, "10/14 fails the 14-day and 80% gates");
assert(holes.implied == null, "implied burn is hidden when the gates fail");
assert(holes.weekly == null, "weekly review is hidden when the gates fail");
const holeHtml = align.extensionHtml(holes);
assert(holeHtml.indexOf("<svg") === -1, "closed window does not draw the line");
assert(/Calibrating/.test(holeHtml), "closed window says calibrating");
assert(/coverage/i.test(holeHtml), "closed window names coverage");
assert(holeHtml.indexOf("Implied burn") === -1, "implied burn copy is hidden");
assert(holeHtml.indexOf("4-week gap") === -1, "weekly line is hidden");

const openHtml = align.extensionHtml(ext);
assert(openHtml.indexOf("<svg") !== -1, "open window draws the line");
assert(openHtml.indexOf("ewi-chart") !== -1, "line chart has ewi-chart");
assert(openHtml.indexOf('data-final-predicted="-2.4"') !== -1, "svg end is the predicted chip");
assert(openHtml.indexOf('data-final-actual="-4.0"') !== -1, "svg end is the scale chip");
assert(
  openHtml.indexOf("Estimated from your weight trend.") !== -1,
  "implied burn is labeled from the weight trend"
);
assert(openHtml.indexOf("2,600 kcal/day") !== -1, "implied burn renders 2,600");
assert(openHtml.indexOf("4-week gap") !== -1, "weekly review names the 4-week gap");
assert(openHtml.indexOf("-1.2 lb") !== -1, "weekly gap renders -1.2 lb");
assert(openHtml.indexOf("2,575 kcal/day") !== -1, "weekly implied burn renders 2,575");

const cardHtml =
  '<div class="energy-weight-insight align-warn">' +
  '<span class="chip-k">From calories</span><span class="chip-v">-2.4 lb</span>' +
  '<span class="chip-k">On scale</span><span class="chip-v">-4.0 lb</span>' +
  '<span class="chip-k">Gap</span>' +
  '<div class="ewi-extra">' +
  openHtml +
  "</div>" +
  '<ul class="ewi-advice"><li>existing guidance</li></ul></div>';
const svgAt = cardHtml.indexOf("<svg");
assert(svgAt > cardHtml.indexOf("energy-weight-insight"), "chart is inside the card");
assert(svgAt < cardHtml.indexOf("ewi-advice"), "chart sits above the existing guidance");
assert(cardHtml.indexOf("From calories") < svgAt, "existing chips stay above the line");

const snapLabels = daysFrom("2026-05-01", 20);
const snapIn = [];
const snapBurn = [];
for (let i = 0; i < 16; i++) {
  snapIn.push({ date: snapLabels[i], calories: 2000 });
  snapBurn.push({ date: snapLabels[i], calories: 2000 });
}
const snapWeights = [{ date: snapLabels[0], weight_lbs: 200 }];
const endLoads = [190, 191, 192, 193, 194, 195, 196];
for (let i = 0; i < endLoads.length; i++) {
  snapWeights.push({ date: snapLabels[13 + i], weight_lbs: endLoads[i] });
}
const snap = align.energyScaleExtension({
  labels: snapLabels,
  intakeRows: snapIn,
  burnedRows: snapBurn,
  weights: snapWeights,
});
assert(snap.open === true, "16/20 opens");
assert(snap.finalActual === -4, "final point is the card scale change, not the 7-day mean");
assert(Math.abs(snap.actual[18] - -7.5) < 1e-9, "the point before the end is still the 7-day mean");

const dupRows = [
  { date: "2026-08-01", calories: 100 },
  { date: "2026-08-01", calories: 1800 },
  { date: "2026-08-02", calories: 0 },
];
const dup = align.energyScaleExtension({
  labels: ["2026-08-01", "2026-08-02", "2026-08-03"],
  intakeRows: [],
  burnedRows: dupRows,
  weights: [],
});
const dupExpect = roll.valuesOnLabels(roll.kcalByDate(dupRows), [
  "2026-08-01",
  "2026-08-02",
  "2026-08-03",
]);
assert(JSON.stringify(dup.burnedSeries) === JSON.stringify(dupExpect), "last finite burn wins; a logged 0 stays 0; a missing day stays null");
assert(dupExpect[0] === 1800 && dupExpect[1] === 0 && dupExpect[2] == null, "chart fixture shape");

console.log("ok energy-weight-align");
