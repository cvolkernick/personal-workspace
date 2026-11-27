#!/usr/bin/env node
/**
 * FF-4 Phase check card (Trends → Guardrails) and the More → Coach line.
 * Display only: every number comes from coach_card.phase_check.
 */
"use strict";

const pc = require("../static/phase-check.js");

let passed = 0;
function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
  passed += 1;
}

const live = {
  phase: "cut",
  state: "holding",
  state_label: "Holding",
  endpoint: 10.0,
  direction: "<=",
  switch_to: "bulk",
  median_7d: 9.8,
  median_14d: 9.8,
  bf_avg_14d: 9.93,
  today_past: true,
  hits: 8,
  hits_needed: 12,
  counted: 14,
  counted_needed: 14,
  thin: false,
  min_readings: 4,
  window_days: 7,
  earliest_switch_date: "2026-11-30",
  earliest_switch_label: "Mon 11/30",
  on_switch: { phase: "slow_bulk", kcal: 2344, protein_g: 170, fat_g: 65, carbs_g: 270 },
  ramp: { after_days: 14, kcal: 2550, plus_kcal: 200 },
  weight_rate: { two_week_pct: 0.11, band: [0.25, 0.5], position: "below" },
  lifts: { status: "green", reason: "Main-lift first sets are holding." },
  labs: { date: "2027-01-28" },
  auto_switch: true,
  applies_automatically: true,
  caveat: "Home-scale body fat is an estimate (±3–5 pts). The trend matters more than any one reading.",
};

const html = pc.cardHtml(live);
assert(html.includes('data-guardrail="phase-check"'), "tile carries data-guardrail=phase-check");
assert(html.includes("guardrail-tile phase-check-tile"), "uses the guardrail tile shell");
assert(!html.includes("Progression"), "no Progression");
assert((html.match(/class="pc-seg on"/g) || []).length === 8, "8 lit segments");
assert((html.match(/class="pc-seg/g) || []).length === 12, "12 segments");
assert(html.includes("Days held: <b>8 of 12</b>"), "days held label");
assert(html.includes("9.8% <small>14-day body fat median</small>"), "big number is the 14-day median");
assert(html.includes("7-day median 9.8% · 14-day avg (chart line) 9.9%"), "sub line with live 14-day avg");
assert(html.includes("Target <b>10%</b> — at or under ✓"), "target line");
assert(html.includes("<b>Auto-switch to bulk</b> when 12 of 14 days hold."), "auto box line 1");
assert(html.includes("Earliest <b>Mon 11/30</b> if every day stays ≤10%."), "earliest");
assert(html.includes("maintenance <b>2,344 kcal</b> (170 P · 65 F · ~270 C), then <b>+200 → 2,550</b> after 14 days."), "switch calories from the block");
assert(html.includes("losing 0.11%/wk (2-wk)</span> · below 0.25–0.5 band"), "weight chip");
assert(html.includes('<span class="k">Main lifts</span> <span class="pc-g">holding</span>'), "lifts chip");
assert(html.includes('<span class="k">Labs</span> 1/28/27'), "labs chip");
assert(html.includes("Home-scale body fat is an estimate"), "caveat");
assert(pc.coachLine(live) === "Cut · BF 9.8% · held 8/12 · auto-switch to bulk ~11/30 earliest", "coach line matches card: " + pc.coachLine(live));

// Missing values never invent a number.
const empty = Object.assign({}, live, {
  state: "not_enough_data", state_label: "Not enough data", median_7d: null, median_14d: null,
  bf_avg_14d: null, hits: 0, counted: 3, today_past: false, earliest_switch_date: null,
  earliest_switch_label: null, on_switch: null, ramp: { after_days: 14, kcal: null, plus_kcal: 200 },
});
const h2 = pc.cardHtml(empty);
assert(h2.includes("— <small>14-day body fat median</small>"), "empty median is a dash");
assert(h2.includes("14-day avg (chart line) —"), "empty avg is a dash");
assert(h2.includes("Counted days: <b>3 of 14</b>"), "not-enough-data counter");
assert(h2.includes("needs ≥4 readings in 7 days"), "not-enough-data hint");
assert(h2.includes("need a TDEE estimate first"), "no TDEE: holds");
assert(!/\d,\d{3} kcal/.test(h2), "no kcal without TDEE");
assert(pc.coachLine(empty).includes("3 of 14 counted days"), "coach line not enough data");
assert(pc.cardHtml(null) === "" && pc.coachLine(undefined) === "", "missing block renders nothing");

// Bulk mirror.
const bulk = Object.assign({}, live, {
  phase: "bulk", endpoint: 15.0, direction: ">=", switch_to: "cut", median_14d: 15.4, median_7d: 15.4,
  earliest_switch_label: "Mon 11/30",
  on_switch: { phase: "cut", kcal: 1875, protein_g: 160, fat_g: 55, carbs_g: 180 }, ramp: null,
});
const h3 = pc.cardHtml(bulk);
assert(h3.includes("<b>Auto-switch to cut</b>"), "bulk switches to cut");
assert(h3.includes("stays ≥15%"), "bulk bound");
assert(h3.includes("Target <b>15%</b> — at or over"), "bulk target");
assert(h3.includes("stored cut <b>1,875 kcal</b>"), "bulk restores stored cut");
assert(pc.coachLine(bulk) === "Bulk · BF 15.4% · held 8/12 · auto-switch to cut ~11/30 earliest", "bulk coach line");

// 7.5% cut floor: fractional endpoint formats as 7.5%, not 8% or 7%.
const cut75 = Object.assign({}, live, { endpoint: 7.5, median_7d: 9.8, median_14d: 9.8, today_past: false, hits: 0, state: "far", state_label: "Far" });
const h75 = pc.cardHtml(cut75);
assert(h75.includes("Target <b>7.5%</b> — at or under"), "7.5 target line");
assert(h75.includes("stays ≤7.5%"), "7.5 earliest bound");
assert(!h75.includes("Target <b>10%</b>"), "7.5 fixture does not show 10%");
assert(!h75.includes("stays ≤10%"), "7.5 fixture does not keep 10% bound");

// Escaping.
assert(pc.cardHtml(Object.assign({}, live, { state_label: "<img>" })).includes("&lt;img&gt;"), "escapes");

console.log(`phase_check_card: ${passed} assertions passed`);
