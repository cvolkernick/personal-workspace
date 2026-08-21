#!/usr/bin/env node
/**
 * Behavioral check: generateWorkoutPlan shows a busy state across workout +
 * meal generate, and restores it in finally on success and workout failure.
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
  "btn-generate-workout": makeEl("btn-generate-workout", "Refresh plan"),
  "btn-force-session-push": makeEl("btn-force-session-push", "Push"),
  "btn-force-session-pull": makeEl("btn-force-session-pull", "Pull"),
  "btn-force-session-legs": makeEl("btn-force-session-legs", "Legs"),
  "workout-plan-refreshing": makeEl("workout-plan-refreshing", "Refreshing workout plan…"),
  "today-workout": makeEl("today-workout", "Mode: train · prescription below"),
  "workout-plan-result": makeEl("workout-plan-result", "<p>PUSH · 5 lifts</p>"),
  "meal-plan-refreshing": makeEl("meal-plan-refreshing", "Refreshing meal plan…"),
  "meal-plan-result": makeEl("meal-plan-result", "<p>Next meal · oats 80g</p>"),
};

global.$ = (id) => els[id] || null;
global.document = { getElementById: (id) => els[id] || null };

const WORKOUT_PLAN_TRIGGER_IDS = [
  "btn-generate-workout",
  "btn-force-session-push",
  "btn-force-session-pull",
  "btn-force-session-legs",
];
const WORKOUT_PLAN_REFRESH_LABEL = "Refreshing plan…";
const WORKOUT_PLAN_IDLE_LABEL = "Refresh plan";
global.WORKOUT_PLAN_TRIGGER_IDS = WORKOUT_PLAN_TRIGGER_IDS;
global.WORKOUT_PLAN_REFRESH_LABEL = WORKOUT_PLAN_REFRESH_LABEL;
global.WORKOUT_PLAN_IDLE_LABEL = WORKOUT_PLAN_IDLE_LABEL;

global.setWorkoutPlanBusy = loadFn("setWorkoutPlanBusy");
global.setMealPlanBusy = loadFn("setMealPlanBusy");
global.setRefreshPlanBusy = loadFn("setRefreshPlanBusy");
global.generatePlan = loadFn("generatePlan");
global.generateWorkoutPlan = loadFn("generateWorkoutPlan");
const generateWorkoutPlan = global.generateWorkoutPlan;

function assertBusy(on) {
  const main = els["btn-generate-workout"];
  assert(main.disabled === on, `main disabled should be ${on}`);
  assert(
    (main.getAttribute("aria-busy") === "true") === on,
    `main aria-busy should be ${on}`
  );
  assert(main.classList.contains("is-refreshing") === on, `main is-refreshing should be ${on}`);
  assert(
    main.textContent === (on ? "Refreshing plan…" : "Refresh plan"),
    `main label should be ${on ? "Refreshing plan…" : "Refresh plan"}`
  );
  ["btn-force-session-push", "btn-force-session-pull", "btn-force-session-legs"].forEach((id) => {
    const el = els[id];
    assert(el.disabled === on, `${id} disabled should be ${on}`);
    assert(
      (el.getAttribute("aria-busy") === "true") === on,
      `${id} aria-busy should be ${on}`
    );
    assert(el.classList.contains("is-refreshing") === on, `${id} is-refreshing should be ${on}`);
  });
  assert(els["workout-plan-refreshing"].hidden === !on, `banner hidden should be ${!on}`);
  assert(
    els["today-workout"].classList.contains("is-refreshing") === on,
    `today-workout is-refreshing should be ${on}`
  );
  assert(
    els["workout-plan-result"].classList.contains("is-refreshing") === on,
    `workout-plan-result is-refreshing should be ${on}`
  );
  assert(
    els["meal-plan-result"].classList.contains("is-refreshing") === on,
    `meal-plan-result is-refreshing should be ${on}`
  );
  assert(
    els["today-workout"].innerHTML.indexOf("Mode: train") >= 0,
    "in-flight busy must not wipe the existing Today workout lead"
  );
  assert(
    els["meal-plan-result"].innerHTML.indexOf("oats 80g") >= 0,
    "in-flight busy must not wipe the existing meal plan"
  );
}

function workoutOk() {
  return {
    ok: true,
    status: 200,
    json: async () => ({ ok: true, plan: { session_type: "push", is_rest_day: false } }),
  };
}

function mealOk() {
  return {
    ok: true,
    status: 200,
    json: async () => ({ ok: true, plan: { meals: [], message: "No in-stock" } }),
  };
}

function workoutFail() {
  return {
    ok: false,
    status: 500,
    json: async () => ({ ok: false, error: "planner_failed" }),
  };
}

function installHeldFetch() {
  const pending = [];
  global.fetch = (url, opts) => {
    fetches.push({ url, method: (opts && opts.method) || "GET", body: opts && opts.body });
    return new Promise((resolve) => {
      pending.push(() =>
        resolve(url.indexOf("meal-plan") >= 0 ? mealOk() : workoutOk())
      );
    });
  };
  return pending;
}

const fetches = [];
let renderedWorkout = null;
let renderedMeal = null;
const alerts = [];

global.renderWorkoutPlan = (plan) => {
  renderedWorkout = plan;
};
global.renderMealPlan = (plan) => {
  renderedMeal = plan;
};
global.showAlert = (msg, kind) => {
  alerts.push({ msg: String(msg || ""), kind });
};

(async () => {
  let pendingReleases = installHeldFetch();

  const pending = generateWorkoutPlan();
  assertBusy(true);
  assert(fetches.length === 1, "workout POST starts while busy is visible");
  assert(fetches[0].url === "/api/workout-plan/generate", "first post is workout-plan generate");
  assert(fetches[0].method === "POST", "method is POST");
  assert(fetches[0].body === "{}", "Refresh plan workout body stays empty");
  assert(renderedWorkout === null, "workout is not rendered before the network returns");

  pendingReleases.shift()();
  await Promise.resolve();
  await Promise.resolve();
  assert(fetches.length === 2, "meal generate starts after workout returns");
  assert(fetches[1].url === "/api/meal-plan/generate", "second post is meal-plan generate");
  assertBusy(true);
  assert(renderedMeal === null, "meal is not rendered before its network returns");

  pendingReleases.shift()();
  await pending;
  assertBusy(false);
  assert(renderedWorkout && renderedWorkout.session_type === "push", "success still renders the workout plan");
  assert(renderedMeal && renderedMeal.message === "No in-stock", "success still renders the honest meal plan");
  assert(alerts.some((a) => a.kind === "ok"), "success toast stays");

  fetches.length = 0;
  renderedWorkout = null;
  renderedMeal = null;
  alerts.length = 0;
  pendingReleases = [];
  global.fetch = (url, opts) => {
    fetches.push({ url, method: (opts && opts.method) || "GET", body: opts && opts.body });
    return new Promise((resolve) => {
      pendingReleases.push(() =>
        resolve(url.indexOf("workout-plan") >= 0 ? workoutFail() : mealOk())
      );
    });
  };

  const failing = generateWorkoutPlan("pull");
  assertBusy(true);
  assert(fetches[0].url === "/api/workout-plan/generate", "force-session still hits generate");
  assert(JSON.parse(fetches[0].body).session_type === "pull", "force-session still sends session_type");
  pendingReleases.shift()();
  await Promise.resolve();
  await Promise.resolve();
  assert(fetches[1] && fetches[1].url === "/api/meal-plan/generate", "meal generate still runs after workout failure");
  pendingReleases.shift()();
  await failing;
  assertBusy(false);
  assert(renderedWorkout === null, "workout failure does not render a workout plan");
  assert(renderedMeal && renderedMeal.message === "No in-stock", "meal still renders after workout failure");
  assert(
    alerts.some((a) => a.kind === "err" && a.msg.indexOf("planner_failed") >= 0),
    "existing error toast stays"
  );
  assert(
    els["today-workout"].innerHTML.indexOf("Mode: train") >= 0,
    "failure must not wipe the existing Today workout lead"
  );

  console.log("ok");
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
