#!/usr/bin/env node
/**
 * #906 RHR trend line: OLS ignores null days (never treats them as 0 bpm)
 * and is hidden when trendSlopePerDay returns null.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "static", "app.js"),
  "utf8"
);

function extractFn(name) {
  const start = SRC.indexOf(`function ${name}`);
  if (start < 0) throw new Error("missing function " + name);
  const brace = SRC.indexOf("{", start);
  let depth = 0;
  for (let i = brace; i < SRC.length; i++) {
    const ch = SRC[i];
    if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) return SRC.slice(start, i + 1);
    }
  }
  throw new Error("unclosed function " + name);
}

function loadFn(name) {
  return new Function(`${extractFn(name)}; return ${name};`)();
}

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

const trendSlopePerDay = loadFn("trendSlopePerDay");
const linearTrend = loadFn("linearTrend");

assert(trendSlopePerDay([60]) === null, "<2 finite days hides the slope");
assert(trendSlopePerDay([null, null]) === null, "all-null hides the slope");
assert(
  linearTrend([60, null]).every((v) => v === null),
  "linearTrend is an all-null series when the slope helper would return null"
);

// Indices 0, 2, 4. Null gaps must not become 0 bpm.
const gappy = [60, null, 62, null, 64];
const slope = trendSlopePerDay(gappy);
assert(Math.abs(slope - 1) < 1e-9, "gappy series slope is 1 bpm/day, got " + slope);
const line = linearTrend(gappy);
assert(line.length === gappy.length, "trend line spans the window");
line.forEach((y, i) => {
  assert(Math.abs(y - (60 + i)) < 1e-9, "line y at " + i + " is " + y);
});

const asZero = trendSlopePerDay([60, 0, 62, 0, 64]);
assert(Math.abs(asZero - 0.8) < 1e-9, "zero-filled gaps slope is 0.8, got " + asZero);
assert(Math.abs(asZero - slope) > 0.1, "treating gaps as 0 bpm changes the slope");

function slopeTxt(perDay) {
  if (perDay == null) return "";
  return ` · trend ${perDay >= 0 ? "+" : ""}${(perDay * 7).toFixed(2)} bpm/week`;
}
assert(slopeTxt(null) === "", "no slope clause when null");
assert(slopeTxt(1) === " · trend +7.00 bpm/week", "positive week slope");
assert(slopeTxt(-0.1) === " · trend -0.70 bpm/week", "negative week slope");

const chart = SRC.slice(
  SRC.indexOf("const rhrDatasets = ["),
  SRC.indexOf('if ($("rhr-trend-note"))')
);
assert(chart.includes('label: "Trend"'), "trend dataset");
assert(chart.includes('label: "Baseline"'), "baseline dataset");
assert(chart.includes("if (rhrTrend)"), "trend dataset omitted when null");
assert(chart.includes("if (baselineLine)"), "baseline dataset omitted when unavailable");
assert(
  SRC.includes("const rSlope = trendSlopePerDay(rhrVals);"),
  "slope comes from trendSlopePerDay"
);
assert(
  SRC.includes("const rhrTrend = rSlope == null ? null : linearTrend(rhrVals);"),
  "line is hidden when the slope helper returns null"
);
assert(SRC.includes("bpm/week"), "note names bpm/week");
assert(SRC.includes("bpm: by[key] != null ? by[key] : null"), "missing days stay null");

console.log("rhr_trend_baseline: ok");
