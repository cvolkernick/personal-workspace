#!/usr/bin/env node
/** List dots use the same body-map state and fill as the diagram. */
"use strict";
const fs = require("fs");
const path = require("path");
const SRC = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8");

function extractFn(name) {
  const start = SRC.indexOf(`function ${name}`);
  if (start < 0) throw new Error("missing function " + name);
  const brace = SRC.indexOf("{", start);
  let depth = 0;
  for (let i = brace; i < SRC.length; i++) {
    if (SRC[i] === "{") depth += 1;
    else if (SRC[i] === "}") {
      depth -= 1;
      if (depth === 0) return SRC.slice(start, i + 1);
    }
  }
  throw new Error("unclosed " + name);
}

function assert(cond, message) {
  if (!cond) {
    console.error("FAIL:", message);
    process.exit(1);
  }
}

function region(id) {
  const attrs = { class: "body-region", "data-muscle": id };
  return {
    dataset: {},
    style: {},
    getAttribute(name) {
      return attrs[name] || "";
    },
    setAttribute(name, value) {
      attrs[name] = String(value);
    },
    addEventListener() {},
  };
}

function harness(regions) {
  const list = { innerHTML: "" };
  const ids = {
    "muscle-sets-list": list,
    "muscle-sets-sub": { textContent: "" },
    "muscle-sets-warning": { textContent: "" },
    "muscle-map-detail": { textContent: "" },
    "muscle-body-map": {
      querySelectorAll(sel) {
        return sel === ".body-region" ? regions : [];
      },
    },
  };
  const document = { getElementById(id) { return ids[id] || null; } };
  const api = new Function(
    "document",
    `${extractFn("phaseBaroEsc")}
     ${extractFn("muscleMapColor")}
     ${extractFn("paintMuscleBodyMap")}
     ${extractFn("renderMuscleSets")}
     const $ = (id) => document.getElementById(id);
     return { muscleMapColor, renderMuscleSets };`
  )(document);
  return { list, api };
}

const FILLS = { under: "#f0b429", on: "#5ce1a8", over: "#f07178", gap: "#5c6b82" };

const { api } = harness([]);
for (const [state, fill] of Object.entries(FILLS)) {
  const color = api.muscleMapColor(state);
  assert(color.state === state && color.fill === fill, state + " fill " + color.fill);
  const fromRow = api.muscleMapColor({ id: "x", state });
  assert(fromRow.fill === fill && fromRow.state === state, "row " + state);
}
const unknown = api.muscleMapColor({ state: "blue", tier: "blue" });
assert(unknown.state === "gap" && unknown.fill === FILLS.gap, "tier blue is not a map color");
assert(api.muscleMapColor(null).fill === FILLS.gap, "missing row is gap");

function paint(bodyMap) {
  const regions = ["chest", "front_delts", "side_delts", "abs"].map(region);
  const { list, api: live } = harness(regions);
  live.renderMuscleSets({
    muscle_sets: {
      muscle_sets: {
        chest: { proper_sets: 9, target: "8–10", tier: "blue" },
        front_delts: { proper_sets: 9, target: "4", tier: "blue" },
        side_delts: { proper_sets: 0, target: "8–10", tier: "red" },
        abs: { proper_sets: 0, target: "", tier: "red" },
      },
      body_map: bodyMap,
      budget: { cap: 14 },
      window: { start: "2026-11-05", end: "2026-11-11" },
    },
  });
  return { html: list.innerHTML, regions };
}

function dot(html, label) {
  const match = html.match(
    new RegExp(
      `<li><span class="muscle-map-dot map-(\\w+)" style="background:(#[0-9a-f]+)" aria-label="[^"]*"></span> ${label} ·`
    )
  );
  assert(match, "missing list row for " + label + ": " + html);
  return { state: match[1], fill: match[2] };
}

// 2026-11-11 window: chest 9 / 8–10 on target, front delts 9 / 4 over,
// side delts 0 under, abs 0 with no programmed lift is gap. The old list
// painted chest and front delts blue from tier.
const painted = paint([
  { id: "chest", state: "on" },
  { id: "front_delts", state: "over" },
  { id: "side_delts", state: "under" },
  { id: "abs", state: "gap", detail: "abs · no programmed lift" },
]);
assert(!painted.html.includes("#3b82f6"), "list still uses the tier blue");
assert(!painted.html.includes("guardrail-dot"), "list still uses guardrail dots");
const expected = {
  chest: "on",
  "front delts": "over",
  "side delts": "under",
  abs: "gap",
};
for (const [label, state] of Object.entries(expected)) {
  const row = dot(painted.html, label);
  assert(row.state === state, label + " state " + row.state);
  assert(row.fill === FILLS[state], label + " fill " + row.fill);
}
for (const node of painted.regions) {
  const state = expected[
    { chest: "chest", front_delts: "front delts", side_delts: "side delts", abs: "abs" }[
      node.getAttribute("data-muscle")
    ]
  ];
  assert(node.getAttribute("class") === "body-region map-" + state, node.getAttribute("class"));
  assert(node.style.fill === FILLS[state], node.getAttribute("data-muscle") + " map fill");
}

// Side delts at 0 follow the map when the map says gap, not the red tier.
const gapped = paint([
  { id: "chest", state: "on" },
  { id: "front_delts", state: "over" },
  { id: "side_delts", state: "gap" },
  { id: "abs", state: "gap" },
]);
const side = dot(gapped.html, "side delts");
assert(side.state === "gap" && side.fill === FILLS.gap, "side delts follow the map gap");
const sideRegion = gapped.regions.find((node) => node.getAttribute("data-muscle") === "side_delts");
assert(sideRegion.style.fill === side.fill, "side delt list dot matches the region");

console.log("ok muscle_map_colors");
