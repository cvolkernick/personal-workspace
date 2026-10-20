#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "static", "meal-snapshot.js"),
  "utf8"
);

function extractFn(name) {
  let start = SRC.indexOf("function " + name);
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

const recipeMealCardHtml = new Function(
  extractFn("escMeal") + "\n" + extractFn("recipeMealCardHtml") + "\nreturn recipeMealCardHtml;"
)();

const html = recipeMealCardHtml({
  recipe_id: "lemon-garlic-tilapia",
  recipe_name: "Lemon-Garlic Tilapia",
  slot_role: "post-workout",
  eat_at_label: "2:15 PM",
  servings: 1.25,
  batch_friendly: true,
  totals: { calories: 523, protein_g: 50, carbs_g: 44, fat_g: 16 },
  steps: ["Sear the tilapia.", "Fold in the pico."],
  items: [{ name: "Tilapia", portion_g: 170 }],
  slot_index: 1,
});

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

assert(html.indexOf("Lemon-Garlic Tilapia") >= 0, "recipe name");
assert(html.indexOf("Sear the tilapia.") >= 0, "steps");
assert(html.indexOf("170g") >= 0, "ingredient grams");
assert(html.indexOf("post-workout") >= 0, "slot role");
assert(html.indexOf("×1.25") >= 0, "servings");
assert(html.indexOf("Batch prep") >= 0, "batch badge");
assert(html.indexOf("recipe-swap") >= 0, "swap");
assert(html.indexOf("Log meal") >= 0, "log");
assert(html.indexOf("Cooked a batch") >= 0, "batch");
console.log("recipe meal card ok");
