#!/usr/bin/env node
/**
 * Behavioral check: generatePlan shows a meal-card busy state before the
 * network returns, restores it in finally, and never hits workout generate.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "static", "app.js"),
  "utf8"
);

function extractFn(name) {
  let start = SRC.indexOf(`function ${name}`);
  if (start < 0) throw new Error("missing function " + name);
  if (start >= 6 && SRC.slice(start - 6, start) === "async ") start -= 6;
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

function makeEl(id, text) {
  const classes = new Set();
  const attributes = {};
  const dataset = {};
  const el = {
    id,
    textContent: text || "",
    innerHTML: text || "",
    disabled: false,
    hidden: true,
    dataset,
    classList: {
      contains: (c) => classes.has(c),
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      toggle: (c, on) => {
        if (on) classes.add(c);
        else classes.delete(c);
      },
    },
    getAttribute: (k) => (k in attributes ? attributes[k] : null),
    setAttribute: (k, v) => {
      attributes[k] = String(v);
    },
    removeAttribute: (k) => {
      delete attributes[k];
    },
  };
  return el;
}

const els = {
  "meal-plan-refreshing": makeEl("meal-plan-refreshing", "Refreshing meal plan…"),
  "meal-plan-result": makeEl("meal-plan-result", "<p>Next meal · oats 80g</p>"),
  "btn-generate-workout": makeEl("btn-generate-workout", "Refresh plan"),
  "today-workout": makeEl("today-workout", "Mode: train · prescription below"),
  "workout-plan-result": makeEl("workout-plan-result", "<p>PUSH · 5 lifts</p>"),
};

global.$ = (id) => els[id] || null;
global.document = { getElementById: (id) => els[id] || null };

global.setMealPlanBusy = loadFn("setMealPlanBusy");
global.generatePlan = loadFn("generatePlan");
const generatePlan = global.generatePlan;

function assertMealBusy(on) {
  const box = els["meal-plan-result"];
  assert(
    (box.getAttribute("aria-busy") === "true") === on,
    `meal-plan-result aria-busy should be ${on}`
  );
  assert(
    box.classList.contains("is-refreshing") === on,
    `meal-plan-result is-refreshing should be ${on}`
  );
  assert(els["meal-plan-refreshing"].hidden === !on, `banner hidden should be ${!on}`);
  assert(
    box.innerHTML.indexOf("oats 80g") >= 0,
    "in-flight busy must not wipe the existing meal plan"
  );
  assert(
    els["btn-generate-workout"].textContent === "Refresh plan",
    "inventory meal refresh must not change Refresh plan label"
  );
  assert(
    els["today-workout"].classList.contains("is-refreshing") === false,
    "inventory meal refresh must not dim the workout card"
  );
}

(async () => {
  let release;
  const fetches = [];
  let rendered = null;
  const alerts = [];

  global.renderMealPlan = (plan) => {
    rendered = plan;
  };
  global.renderWorkoutPlan = () => {
    throw new Error("inventory path must not render a workout plan");
  };
  global.showAlert = (msg, kind) => {
    alerts.push({ msg: String(msg || ""), kind });
  };

  global.fetch = (url, opts) => {
    fetches.push({ url, method: (opts && opts.method) || "GET", body: opts && opts.body });
    return new Promise((resolve) => {
      release = () =>
        resolve({
          ok: true,
          status: 200,
          json: async () => ({ ok: true, plan: { meals: [], message: "No in-stock" } }),
        });
    });
  };

  const pending = generatePlan();
  assertMealBusy(true);
  assert(fetches.length === 1, "POST starts while meal busy is visible");
  assert(fetches[0].url === "/api/meal-plan/generate", "inventory posts meal-plan generate");
  assert(fetches[0].method === "POST", "method is POST");
  assert(fetches[0].body === "{}", "meal generate body stays empty");
  assert(rendered === null, "plan is not rendered before the network returns");

  release();
  await pending;
  assertMealBusy(false);
  assert(rendered && rendered.message === "No in-stock", "success still renders the honest empty plan");
  assert(
    fetches.every((f) => f.url === "/api/meal-plan/generate"),
    "success path never posts workout generate"
  );

  fetches.length = 0;
  rendered = null;
  alerts.length = 0;
  global.fetch = (url, opts) => {
    fetches.push({ url, method: (opts && opts.method) || "GET", body: opts && opts.body });
    return new Promise((resolve) => {
      release = () =>
        resolve({
          ok: false,
          status: 500,
          json: async () => ({ ok: false, error: "planner_failed" }),
        });
    });
  };

  const failing = generatePlan();
  assertMealBusy(true);
  assert(fetches[0].url === "/api/meal-plan/generate", "failure still hits meal generate");
  release();
  await failing;
  assertMealBusy(false);
  assert(rendered === null, "failure does not render a plan");
  assert(
    alerts.some((a) => a.kind === "err" && a.msg.indexOf("planner_failed") >= 0),
    "existing error toast stays"
  );
  assert(
    els["meal-plan-result"].innerHTML.indexOf("oats 80g") >= 0,
    "failure must not wipe the existing meal plan"
  );
  assert(
    fetches.every((f) => f.url !== "/api/workout-plan/generate"),
    "failure path never posts workout generate"
  );

  console.log("ok");
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
