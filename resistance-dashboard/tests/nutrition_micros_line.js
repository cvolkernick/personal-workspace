#!/usr/bin/env node
/**
 * #537: Fiber·Sodium·Sugar disclosure falls back to food_logs_today
 * when today_consumed lacks micros/nutrients. Extracts helpers from app.js.
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

function loadFn(name, prelude) {
  return new Function(`${prelude || ""}; ${extractFn(name)}; return ${name};`)();
}

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

const KEYS = `
  const FIBER_KEYS = ["DIETARY_FIBER", "FIBER", "TOTAL_DIETARY_FIBER", "TOTAL_FIBER"];
  const SODIUM_KEYS = ["SODIUM"];
  const SUGAR_KEYS = ["SUGAR", "SUGARS", "TOTAL_SUGAR", "TOTAL_SUGARS"];
`;

const fmtNum = loadFn("fmtNum");
const fmtSodiumMg = loadFn("fmtSodiumMg");
const pickNutrientGrams = loadFn("pickNutrientGrams", KEYS);
const microsFromNutrients = loadFn(
  "microsFromNutrients",
  `${KEYS}; ${extractFn("pickNutrientGrams")}`
);
const microsFromPayload = loadFn(
  "microsFromPayload",
  `${KEYS}; ${extractFn("pickNutrientGrams")}; ${extractFn("microsFromNutrients")}`
);
const microsLine = loadFn(
  "microsLine",
  `${KEYS}; ${extractFn("pickNutrientGrams")}; ${extractFn("microsFromNutrients")}; ${extractFn(
    "microsFromPayload"
  )}; ${extractFn("fmtNum")}; ${extractFn("fmtSodiumMg")}`
);
const foodLogsTodayFromStore = loadFn("foodLogsTodayFromStore");
const sumMicrosFromFoodLogs = loadFn(
  "sumMicrosFromFoodLogs",
  `${KEYS}; ${extractFn("pickNutrientGrams")}; ${extractFn("microsFromNutrients")}; ${extractFn(
    "microsFromPayload"
  )}`
);
const microsLineFromStore = loadFn(
  "microsLineFromStore",
  `${KEYS}; ${extractFn("pickNutrientGrams")}; ${extractFn("microsFromNutrients")}; ${extractFn(
    "microsFromPayload"
  )}; ${extractFn("fmtNum")}; ${extractFn("fmtSodiumMg")}; ${extractFn("microsLine")}; ${extractFn(
    "foodLogsTodayFromStore"
  )}; ${extractFn("sumMicrosFromFoodLogs")}`
);

assert(typeof fmtNum === "function", "fmtNum loaded");
assert(typeof microsLineFromStore === "function", "microsLineFromStore loaded");

const meals = [
  { name: "Oats", nutrients: { DIETARY_FIBER: 8, SODIUM: 0.04, SUGAR: 6 } },
  { name: "Chicken", nutrients: { DIETARY_FIBER: 0, SODIUM: 0.12 } },
  { name: "Whey", nutrients: {} },
];

const emptyConsumed = microsLineFromStore({
  today_consumed: { calories: 900, protein_g: 80, carbs_g: 60, fat_g: 20 },
  food_logs_today: meals,
});
assert(emptyConsumed.indexOf("fiber") >= 0, "fallback shows fiber from meals");
assert(emptyConsumed.indexOf("Na") >= 0, "fallback shows sodium from meals");
assert(emptyConsumed.indexOf("sugar") >= 0, "fallback shows sugar from meals");
assert(emptyConsumed.indexOf("fiber 8g") >= 0, "fiber 8+0 = 8, logged zero kept");
assert(emptyConsumed.indexOf("sugar 6g") >= 0, "sugar only from oats");

const nested = microsLineFromStore({
  today_consumed: { calories: 400 },
  nutrition_store: { food_logs_today: meals },
});
assert(nested.indexOf("fiber") >= 0, "nested nutrition_store.food_logs_today works");

const fromConsumed = microsLineFromStore({
  today_consumed: {
    calories: 900,
    micros: { fiber_g: 20, sodium_mg: 800, sugar_g: 18 },
  },
  food_logs_today: meals,
});
assert(fromConsumed.indexOf("fiber 20g") >= 0, "today_consumed micros win");
assert(fromConsumed.indexOf("sugar 18g") >= 0, "consumed sugar used, not meal sum");

const absent = microsLineFromStore({
  today_consumed: { calories: 400, protein_g: 30 },
  food_logs_today: [{ name: "Whey", nutrients: { PROTEIN: 30 } }],
});
assert(absent === "", "absent keys omitted, never invented");

const noLogs = microsLineFromStore({
  today_consumed: { calories: 400 },
  food_logs_today: [],
});
assert(noLogs === "", "empty logs stay hidden");

const loggedZero = microsLineFromStore({
  today_consumed: {},
  food_logs_today: [{ nutrients: { DIETARY_FIBER: 0 } }],
});
assert(loggedZero.indexOf("fiber 0g") >= 0, "logged zero is shown");
assert(loggedZero.indexOf("Na") < 0, "missing sodium omitted when only fiber=0");
assert(loggedZero.indexOf("sugar") < 0, "missing sugar omitted when only fiber=0");

const summed = sumMicrosFromFoodLogs(meals);
assert(summed.fiber_g === 8, "sum fiber skips missing, keeps logged 0");
assert(Math.round(summed.sodium_mg) === 160, "sum sodium 40+120 mg");
assert(summed.sugar_g === 6, "sugar not invented on chicken");

assert(foodLogsTodayFromStore(null).length === 0, "null store is empty");
assert(foodLogsTodayFromStore({ food_logs_today: meals }).length === 3, "top-level logs");

const microChipBand = loadFn("microChipBand");
assert(microChipBand("fiber", 15, 30) === "red", "fiber 50% of target is red");
assert(microChipBand("fiber", 27, 30) === "yellow", "fiber 90% is yellow");
assert(microChipBand("fiber", 30, 30) === "green", "fiber on target is green");
assert(microChipBand("fiber", 40, 30) === "green", "fiber over target is green");
assert(microChipBand("sugar", 75, 50) === "red", "sugar 150% is red");
assert(microChipBand("sugar", 40, 50) === "green", "sugar under target is green");
assert(microChipBand("sodium", 2500, 2300) === "red", "sodium over is red");
assert(microChipBand("sodium", 1000, 2300) === "green", "sodium under is green");
assert(microChipBand("fiber", null, 30) === null, "missing logged is omitted");
assert(microChipBand("sugar", 10, null) === null, "missing target is uncolored");

const microsFromStore = loadFn(
  "microsFromStore",
  `${KEYS}; ${extractFn("pickNutrientGrams")}; ${extractFn("microsFromNutrients")}; ${extractFn(
    "microsFromPayload"
  )}; ${extractFn("foodLogsTodayFromStore")}; ${extractFn("sumMicrosFromFoodLogs")}`
);
const fromStore = microsFromStore({
  today_consumed: { micros: { fiber_g: 20, sugar_g: 18 } },
});
assert(fromStore.fiber_g === 20, "store fiber");
assert(fromStore.sugar_g === 18, "store sugar");
assert(fromStore.sodium_mg == null, "missing sodium stays missing");

console.log("ok nutrition-micros-line");
