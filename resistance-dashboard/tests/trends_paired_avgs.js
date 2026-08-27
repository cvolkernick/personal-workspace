#!/usr/bin/env node
/**
 * #254/#258/#268: Avg intake / Avg burned / Avg deficit share the Σ 75d
 * paired-days window. Same pair-day honesty as #243. Sign: surplus +,
 * deficit − (mean intake − burned). No invented rows.
 */
"use strict";

const path = require("path");
const avgs = require("../static/trends-paired-avgs.js");

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

function almostEqual(a, b, msg) {
  assert(Math.abs(a - b) < 1e-9, msg + " got " + a + " expected " + b);
}

assert(avgs.SPAN_DAYS === 75, "span must be the same 75d window as Σ chips");

const now = new Date(2026, 7, 22, 12, 0, 0);
const labels = avgs.windowLabels(avgs.SPAN_DAYS, now);
assert(labels.length === 75, "75 civil labels");
assert(labels[labels.length - 1] === "2026-08-22", "window ends today");
assert(labels[0] === "2026-06-09", "window starts 74 days earlier");

const recentPaired = [
  { date: "2026-08-20", calories: 2000 },
  { date: "2026-08-21", calories: 2200 },
  { date: "2026-08-22", calories: 1800 },
];
const recentBurned = [
  { date: "2026-08-20", calories: 2500 },
  { date: "2026-08-21", calories: 2300 },
  { date: "2026-08-22", calories: 2400 },
];

const nutrition = [
  ...recentPaired,
  { date: "2026-08-19", calories: 9999 },
  { date: "2026-07-01", calories: 1111 },
  { date: "2026-06-20", calories: 3333 },
  { date: "2026-06-01", calories: 5555 },
];
const burned = [
  ...recentBurned,
  { date: "2026-08-18", calories: 8888 },
  { date: "2026-07-01", calories: 2222 },
  { date: "2026-06-20", calories: 4444 },
  { date: "2026-06-01", calories: 6666 },
];
const nutritionCopy = JSON.parse(JSON.stringify(nutrition));
const burnedCopy = JSON.parse(JSON.stringify(burned));

const stats = avgs.pairedCalorieWindow(nutrition, burned, 75, now);
assert(stats.pairDays === 5, "only days with both series inside 75d");
assert(
  stats.sumIn === 2000 + 2200 + 1800 + 1111 + 3333,
  "Σ intake is the paired sum (includes 2026-06-20, now inside 75d)"
);
assert(
  stats.sumOut === 2500 + 2300 + 2400 + 2222 + 4444,
  "Σ burned is the paired sum (includes 2026-06-20, now inside 75d)"
);
almostEqual(stats.avgIn, stats.sumIn / 5, "avg intake = Σ intake / N");
almostEqual(stats.avgOut, stats.sumOut / 5, "avg burned = Σ burned / N");
almostEqual(stats.avgIn, 2088.8, "avg intake 2088.8");
almostEqual(stats.avgOut, 2773.2, "avg burned 2773.2");
const perDayDeltas = [
  2000 - 2500,
  2200 - 2300,
  1800 - 2400,
  1111 - 2222,
  3333 - 4444,
];
const meanDelta =
  perDayDeltas.reduce(function (a, b) {
    return a + b;
  }, 0) / perDayDeltas.length;
almostEqual(stats.sumDelta, perDayDeltas.reduce(function (a, b) {
  return a + b;
}, 0), "sumDelta is Σ (intake_i − burned_i)");
almostEqual(stats.avgDelta, meanDelta, "avgDelta is mean of per-day deltas");
almostEqual(
  stats.avgDelta,
  stats.avgIn - stats.avgOut,
  "mean(delta) matches intake_avg − burned_avg on the same pair set"
);
almostEqual(stats.avgDelta, -684.4, "avg deficit −684.4 (negative = deficit)");
assert(stats.avgDelta < 0, "this fixture is a net deficit");
assert(
  JSON.stringify(nutrition) === JSON.stringify(nutritionCopy),
  "must not invent or mutate nutrition rows"
);
assert(
  JSON.stringify(burned) === JSON.stringify(burnedCopy),
  "must not invent or mutate burned rows"
);

const still60 = avgs.pairedCalorieWindow(nutrition, burned, 60, now);
assert(
  still60.pairDays === 4,
  "explicit 60d still excludes 2026-06-20 (pairing is window-honest, not invented)"
);
const still45 = avgs.pairedCalorieWindow(nutrition, burned, 45, now);
assert(
  still45.pairDays === 3,
  "explicit 45d still excludes 2026-07-01 (pairing is window-honest, not invented)"
);
assert(
  stats.pairDays === 5,
  "75d includes 2026-06-20 and still excludes 2026-06-01 (outside the window)"
);

const empty = avgs.pairedCalorieWindow([], [], 75, now);
assert(empty.pairDays === 0, "empty pairDays=0");
assert(empty.avgIn === null && empty.avgOut === null, "no avgs when unpaired");
assert(empty.avgDelta === null && empty.sumDelta === 0, "no avgDelta when unpaired");
assert(avgs.formatAvgKcal(0, 0) === "—", "pairDays=0 displays em dash");
assert(avgs.formatAvgKcal(empty.pairDays, empty.sumIn) === "—", "empty → —");
assert(avgs.formatAvgDelta(0, 0) === "—", "pairDays=0 deficit displays em dash");
assert(avgs.formatAvgDelta(empty.pairDays, empty.sumDelta) === "—", "empty Δ → —");

const unpairedOnly = avgs.pairedCalorieWindow(
  [{ date: "2026-08-22", calories: 1800 }],
  [{ date: "2026-08-21", calories: 2400 }],
  75,
  now
);
assert(unpairedOnly.pairDays === 0, "intake-only + burned-only is not a pair");
assert(avgs.formatAvgKcal(unpairedOnly.pairDays, unpairedOnly.sumIn) === "—");
assert(unpairedOnly.avgDelta === null, "unpaired days do not invent a deficit");
assert(avgs.formatAvgDelta(unpairedOnly.pairDays, unpairedOnly.sumDelta) === "—");

const nanSkip = avgs.pairedCalorieWindow(
  [
    { date: "2026-08-22", calories: Number.NaN },
    { date: "2026-08-21", calories: 2000 },
  ],
  [
    { date: "2026-08-22", calories: 2400 },
    { date: "2026-08-21", calories: 2100 },
  ],
  75,
  now
);
assert(nanSkip.pairDays === 1, "NaN intake is skipped like Σ chips");
almostEqual(nanSkip.avgIn, 2000, "remaining pair avg intake");
almostEqual(nanSkip.avgOut, 2100, "remaining pair avg burned");
almostEqual(nanSkip.avgDelta, -100, "remaining pair avg deficit");

const surplus = avgs.pairedCalorieWindow(
  [
    { date: "2026-08-22", calories: 2500 },
    { date: "2026-08-21", calories: 2300 },
  ],
  [
    { date: "2026-08-22", calories: 2000 },
    { date: "2026-08-21", calories: 2100 },
  ],
  60,
  now
);
almostEqual(surplus.avgDelta, (2500 - 2000 + 2300 - 2100) / 2, "surplus is mean of per-day deltas");
assert(surplus.avgDelta > 0, "intake > burned → positive surplus");
assert(avgs.formatAvgDelta(surplus.pairDays, surplus.sumDelta) === "+350", "surplus shown as +");
assert(avgs.deltaChipKind(surplus.pairDays, surplus.avgDelta) === "chip-surplus", "surplus tint");

assert(avgs.formatAvgDelta(3, -1200) === "−400", "deficit shown as −");
assert(avgs.deltaChipKind(3, -400) === "chip-deficit", "deficit tint");
assert(avgs.formatAvgDelta(3, 1200) === "+400", "surplus shown as +");
assert(avgs.deltaChipKind(3, 400) === "chip-surplus", "surplus tint on +");
assert(avgs.formatAvgDelta(2, 0) === "0", "balanced mean is 0");
assert(avgs.deltaChipKind(2, 0) === "chip-balance", "balanced tint");
assert(avgs.deltaChipKind(0, null) === "chip-balance", "empty Δ uses balance chrome");

function makeNote(sigma) {
  const chips = [];
  const row = {
    className: "chart-summary-row",
    parentNode: null,
    nextSibling: null,
    children: chips,
  };
  const note = {
    id: "nutrition-note",
    children: sigma ? [row] : [],
    textContent: sigma ? "Σ intake Σ burned" : "No nutrition/hydration yet",
    querySelectorAll: function (sel) {
      if (sel === ".chip-k") {
        return sigma
          ? [{ textContent: "Σ intake" }, { textContent: "Σ burned" }]
          : [];
      }
      return [];
    },
    querySelector: function (sel) {
      if (sel === ".chart-summary-row") return sigma ? row : null;
      if (sel === ".chart-summary-chip") return sigma ? chips[0] : null;
      return null;
    },
    contains: function (el) {
      return this.children.indexOf(el) !== -1;
    },
    insertBefore: function (node, _ref) {
      this.children.unshift(node);
    },
  };
  row.parentNode = note;
  row.parentNode.appendChild = function (node) {
    note.children.push(node);
  };
  row.parentNode.insertBefore = function (node, ref) {
    const i = note.children.indexOf(ref);
    if (i < 0) note.children.push(node);
    else note.children.splice(i, 0, node);
  };
  return note;
}

const els = {};
global.document = {
  getElementById: function (id) {
    return els[id] || null;
  },
  createElement: function (tag) {
    const el = {
      tagName: tag,
      className: "",
      _id: "",
      innerHTML: "",
    };
    Object.defineProperty(el, "id", {
      get: function () {
        return this._id;
      },
      set: function (v) {
        if (this._id && els[this._id] === this) delete els[this._id];
        this._id = v;
        if (v) els[v] = this;
      },
    });
    return el;
  },
};

els["nutrition-note"] = makeNote(true);
const painted = avgs.paintAvgChips(
  { health: { nutrition: recentPaired, calories_burned: recentBurned } },
  now
);
assert(painted && painted.pairDays === 3, "paint uses the same pair set");
const row = els["trends-avg-row"];
assert(row, "avg row is inserted under the Trends card");
assert(row.innerHTML.indexOf("Avg intake") !== -1, "Avg intake chip");
assert(row.innerHTML.indexOf("Avg burned") !== -1, "Avg burned chip");
assert(row.innerHTML.indexOf("Avg deficit") !== -1, "Avg deficit chip on the same row");
assert(row.innerHTML.indexOf("2,000") !== -1 || row.innerHTML.indexOf("2000") !== -1, "avg intake value");
assert(row.innerHTML.indexOf("2,400") !== -1 || row.innerHTML.indexOf("2400") !== -1, "avg burned value");
assert(row.innerHTML.indexOf("−400") !== -1, "avg deficit −400 from the same 3 paired days");
assert(row.innerHTML.indexOf("−deficit") !== -1, "sign copy: −deficit");
assert(row.innerHTML.indexOf("+surplus") !== -1, "sign copy: +surplus");
assert(row.innerHTML.indexOf("chip-deficit") !== -1, "deficit tint on the Δ chip");
assert(row.innerHTML.indexOf("chicken") === -1, "does not invent food");
assert(row.innerHTML.indexOf("oats") === -1, "does not invent food");

els["nutrition-note"] = makeNote(true);
delete els["trends-avg-row"];
const zeroPaint = avgs.paintAvgChips(
  { health: { nutrition: [], calories_burned: [] } },
  now
);
assert(zeroPaint.pairDays === 0, "paint pairDays=0");
const zeroRow = els["trends-avg-row"];
assert(zeroRow.innerHTML.indexOf("—") !== -1, "pairDays=0 shows —");
assert(zeroRow.innerHTML.indexOf("Avg intake") !== -1, "honest empty still labeled");
assert(zeroRow.innerHTML.indexOf("Avg deficit") !== -1, "empty still labels Avg deficit");
assert(
  (zeroRow.innerHTML.match(/—/g) || []).length >= 3,
  "all three avgs are em dashes when unpaired"
);

els["nutrition-note"] = makeNote(false);
delete els["trends-avg-row"];
const emptyState = avgs.paintAvgChips(
  { health: { nutrition: [], calories_burned: [] } },
  now
);
assert(emptyState === null, "empty Trends card does not invent chips or food");
assert(!els["trends-avg-row"], "no avg row without Σ chips");

els["nutrition-note"] = makeNote(true);
delete els["trends-avg-row"];
const surplusPaint = avgs.paintAvgChips(
  {
    health: {
      nutrition: [{ date: "2026-08-22", calories: 2500 }],
      calories_burned: [{ date: "2026-08-22", calories: 2000 }],
    },
  },
  now
);
assert(surplusPaint && surplusPaint.avgDelta === 500, "paint surplus from one pair");
const surplusRow = els["trends-avg-row"];
assert(surplusRow.innerHTML.indexOf("Avg deficit") !== -1, "surplus still labeled Avg deficit");
assert(surplusRow.innerHTML.indexOf("+500") !== -1, "surplus value is positive");
assert(surplusRow.innerHTML.indexOf("chip-surplus") !== -1, "surplus tint");
assert(surplusRow.innerHTML.indexOf("chicken") === -1, "surplus paint does not invent food");

console.log("ok trends-paired-avgs");
