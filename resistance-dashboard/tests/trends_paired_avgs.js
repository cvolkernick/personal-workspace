#!/usr/bin/env node
/**
 * #239: Avg intake / Avg burned share the Σ 45d paired-days window.
 * No invented nutrition rows.
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

assert(avgs.SPAN_DAYS === 45, "span must be the same 45d window as Σ chips");

const now = new Date(2026, 7, 22, 12, 0, 0);
const labels = avgs.windowLabels(45, now);
assert(labels.length === 45, "45 civil labels");
assert(labels[labels.length - 1] === "2026-08-22", "window ends today");
assert(labels[0] === "2026-07-09", "window starts 44 days earlier");

const nutrition = [
  { date: "2026-08-20", calories: 2000 },
  { date: "2026-08-21", calories: 2200 },
  { date: "2026-08-22", calories: 1800 },
  { date: "2026-08-19", calories: 9999 },
  { date: "2026-07-01", calories: 1111 },
];
const burned = [
  { date: "2026-08-20", calories: 2500 },
  { date: "2026-08-21", calories: 2300 },
  { date: "2026-08-22", calories: 2400 },
  { date: "2026-08-18", calories: 8888 },
  { date: "2026-07-01", calories: 2222 },
];
const nutritionCopy = JSON.parse(JSON.stringify(nutrition));
const burnedCopy = JSON.parse(JSON.stringify(burned));

const stats = avgs.pairedCalorieWindow(nutrition, burned, 45, now);
assert(stats.pairDays === 3, "only days with both series inside 45d");
assert(stats.sumIn === 2000 + 2200 + 1800, "Σ intake is the paired sum");
assert(stats.sumOut === 2500 + 2300 + 2400, "Σ burned is the paired sum");
almostEqual(stats.avgIn, stats.sumIn / 3, "avg intake = Σ intake / N");
almostEqual(stats.avgOut, stats.sumOut / 3, "avg burned = Σ burned / N");
almostEqual(stats.avgIn, 2000, "avg intake 2000");
almostEqual(stats.avgOut, 2400, "avg burned 2400");
assert(
  JSON.stringify(nutrition) === JSON.stringify(nutritionCopy),
  "must not invent or mutate nutrition rows"
);
assert(
  JSON.stringify(burned) === JSON.stringify(burnedCopy),
  "must not invent or mutate burned rows"
);

const empty = avgs.pairedCalorieWindow([], [], 45, now);
assert(empty.pairDays === 0, "empty pairDays=0");
assert(empty.avgIn === null && empty.avgOut === null, "no avgs when unpaired");
assert(avgs.formatAvgKcal(0, 0) === "—", "pairDays=0 displays em dash");
assert(avgs.formatAvgKcal(empty.pairDays, empty.sumIn) === "—", "empty → —");

const unpairedOnly = avgs.pairedCalorieWindow(
  [{ date: "2026-08-22", calories: 1800 }],
  [{ date: "2026-08-21", calories: 2400 }],
  45,
  now
);
assert(unpairedOnly.pairDays === 0, "intake-only + burned-only is not a pair");
assert(avgs.formatAvgKcal(unpairedOnly.pairDays, unpairedOnly.sumIn) === "—");

const nanSkip = avgs.pairedCalorieWindow(
  [
    { date: "2026-08-22", calories: Number.NaN },
    { date: "2026-08-21", calories: 2000 },
  ],
  [
    { date: "2026-08-22", calories: 2400 },
    { date: "2026-08-21", calories: 2100 },
  ],
  45,
  now
);
assert(nanSkip.pairDays === 1, "NaN intake is skipped like Σ chips");
almostEqual(nanSkip.avgIn, 2000, "remaining pair avg intake");
almostEqual(nanSkip.avgOut, 2100, "remaining pair avg burned");

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
  { health: { nutrition: nutrition, calories_burned: burned } },
  now
);
assert(painted && painted.pairDays === 3, "paint uses the same pair set");
const row = els["trends-avg-row"];
assert(row, "avg row is inserted under the Trends card");
assert(row.innerHTML.indexOf("Avg intake") !== -1, "Avg intake chip");
assert(row.innerHTML.indexOf("Avg burned") !== -1, "Avg burned chip");
assert(row.innerHTML.indexOf("2,000") !== -1 || row.innerHTML.indexOf("2000") !== -1, "avg intake value");
assert(row.innerHTML.indexOf("2,400") !== -1 || row.innerHTML.indexOf("2400") !== -1, "avg burned value");
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

els["nutrition-note"] = makeNote(false);
delete els["trends-avg-row"];
const emptyState = avgs.paintAvgChips(
  { health: { nutrition: [], calories_burned: [] } },
  now
);
assert(emptyState === null, "empty Trends card does not invent chips or food");
assert(!els["trends-avg-row"], "no avg row without Σ chips");

console.log("ok trends-paired-avgs");
