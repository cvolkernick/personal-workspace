#!/usr/bin/env node
/**
 * Body-fat series aligned onto the Trends weight dates.
 * Later recorded_at wins. Missing days stay null. Out of range is dropped.
 */
"use strict";

const bf = require("../static/body-fat-series.js");

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

const samples = [
  { date: "2026-11-06", percentage: 18.1, recorded_at: "2026-11-06T11:00:00Z" },
  { date: "2026-11-06", percentage: 18.4, recorded_at: "2026-11-06T15:02:00Z" },
  { date: "2026-11-05", percentage: 18.6, recorded_at: "2026-11-05T12:00:00Z" },
  { date: "2026-11-04", percentage: 140, recorded_at: "2026-11-04T12:00:00Z" },
  { date: "2026-11-03", percentage: -1 },
];

const aligned = bf.alignToLabels(
  ["2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07"],
  samples
);
assert(JSON.stringify(aligned) === JSON.stringify([null, 18.6, 18.4, null]), "align " + JSON.stringify(aligned));

const latest = bf.latest(samples);
assert(latest && latest.date === "2026-11-06" && latest.percentage === 18.4, "latest " + JSON.stringify(latest));

const sameStamp = bf.lastByDate([
  { date: "2026-11-06", percentage: 19, recorded_at: "2026-11-06T11:00:00Z" },
  { date: "2026-11-06", percentage: 19.5, recorded_at: "2026-11-06T11:00:00Z" },
]);
assert(sameStamp["2026-11-06"].percentage === 19.5, "same timestamp last write wins");

assert(bf.alignToLabels(["2026-11-06"], [])[0] === null, "empty stays null");
assert(bf.latest([]) === null, "no latest");

console.log("ok body-fat-series");
