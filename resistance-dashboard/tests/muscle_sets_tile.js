#!/usr/bin/env node
/**
 * #1193: the Weekly proper sets tile renders the feed shape.
 * Loads the real static/muscle-sets.js, not a copy.
 */
"use strict";

const path = require("path");
const lib = require(path.join(__dirname, "..", "static", "muscle-sets.js"));

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

const week = {
  muscle_sets: {
    chest: {
      label: "Chest",
      proper_sets: 8,
      tier: "blue",
      target: { role: "priority", low: 8, high: 10, stretch: true },
      trend_4w: { avg: 6.5, direction: "up", weeks: [] },
    },
    quads: {
      label: "Quads",
      proper_sets: 2.5,
      tier: "red",
      target: { role: "deprioritized", low: 4, high: 4 },
      trend_4w: { avg: null, direction: null, weeks: [] },
    },
    biceps: {
      label: "Biceps <b>",
      proper_sets: 5,
      tier: "green",
      target: { role: "middle", low: 4, high: 7 },
      trend_4w: { avg: 5, direction: "flat", weeks: [] },
    },
  },
  muscle_sets_meta: { week_start: "2026-10-12", week_end: "2026-10-18", proper_set_total: 15.5 },
  muscle_budget: {
    cap: 40,
    cap_source: "rolling_4w_avg",
    planned_total: 68,
    warning: { message: "Priority targets plus floors need 68 proper sets a week; the cap is 40." },
  },
  muscle_coach: { active: true, flags: [{ message: "Chest: on pace for 5.2." }] },
};

const html = lib.tileHtml(week);
assert(html.includes('data-group="chest" data-tier="blue"'), "chest row is blue");
assert(html.includes('data-group="quads" data-tier="red"'), "quads row is red");
assert(html.includes('data-group="biceps" data-tier="green"'), "biceps row is green");
assert(html.includes("Priority 8-10+"), "stretch shows 10+");
assert(html.includes("Floor 4"), "floor label");
assert(html.includes("Growth 4-7"), "middle label");
assert(html.includes("4w avg 6.5 ↑"), "trend arrow");
assert(html.includes("no history"), "no trend history is honest");
assert(html.includes(">2.5<"), "fractional sets keep one decimal");
assert(html.includes("Week 2026-10-12 to 2026-10-18"), "week window");
assert(html.includes("cap 40 proper sets"), "cap line");
assert(html.includes('role="alert"') && html.includes("the cap is 40"), "overrun warning shown");
assert(html.includes("Thursday check (suggestions only)") && html.includes("on pace for 5.2"), "thursday flags");
assert(!html.includes("<b>"), "labels are escaped");
assert(html.includes("&lt;b&gt;"), "escaped label present");

const quiet = lib.tileHtml({
  muscle_sets: week.muscle_sets,
  muscle_budget: { cap: null, planned_total: 68, warning: null },
  muscle_coach: { active: false, flags: [] },
});
assert(quiet.includes("no cap yet"), "no cap says so");
assert(!quiet.includes("Thursday check"), "no flags off Thursday");
assert(!quiet.includes('role="alert"'), "no warning when none");
assert(lib.tileHtml(null).includes("Not enough data."), "empty payload");
assert(lib.tileHtml({ muscle_sets: {} }).includes("muscle-set-grid"), "empty map still renders");
console.log("muscle_sets_tile.js OK");
