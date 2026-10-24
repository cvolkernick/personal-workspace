"use strict";
// Runs the Cash Streams Period averages renderer under a stub DOM and prints
// the rendered rows as JSON: { income: [[label, perDay]], expenses: [...] }.
// Usage: node cash_streams_sort_sim.js <cash-streams.html> <fixture.json>
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const fixture = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

function extractFn(src, name) {
  const re = new RegExp("(?:async\\s+)?function\\s+" + name + "\\s*\\(");
  const m = re.exec(src);
  if (!m) throw new Error("missing " + name);
  const start = src.indexOf("{", m.index);
  let depth = 0;
  for (let i = start; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") {
      depth--;
      if (depth === 0) return src.slice(m.index, i + 1);
    }
  }
  throw new Error("unclosed " + name);
}

function extractConst(src, name) {
  const at = src.indexOf("const " + name + " = [");
  if (at < 0) throw new Error("missing " + name);
  const end = src.indexOf("];", at);
  return src.slice(at, end + 2);
}

const names = [
  "esc", "moneyCents", "parseDay", "bandOther", "chartPoints",
  "visibleChartPoints", "bandAverages", "expenseAverages",
  "sortByPerDayDesc", "renderBreakdown",
];
const bundle = [
  "let currentDays = " + Number(fixture.days || 90) + ";",
  extractConst(html, "ROLLING_BANDS"),
  extractConst(html, "NAMED_YNAB_BANDS"),
  ...names.map((n) => extractFn(html, n)),
  "return renderBreakdown;",
].join("\n");

function makeList() {
  const list = { children: [] };
  list.replaceChildren = () => { list.children = []; };
  list.appendChild = (el) => { list.children.push(el); };
  return list;
}
const els = {
  "rolling-income": makeList(),
  "rolling-expenses": makeList(),
  "rolling-breakdown-hint": { textContent: "" },
};
const document = {
  getElementById: (id) => els[id] || null,
  createElement: () => ({ innerHTML: "", textContent: "", className: "" }),
};

const renderBreakdown = new Function("document", bundle)(document);
renderBreakdown(fixture.streams, fixture.rolling);

function rows(list) {
  return list.children.map((li) => {
    if (!li.innerHTML) return [li.textContent, null];
    const m = /^<span>(.*?)<\/span><span>\$([\d,.]+)\/day<\/span>$/.exec(li.innerHTML);
    if (!m) throw new Error("unexpected row " + li.innerHTML);
    return [m[1].replace(/&amp;/g, "&"), Number(m[2].replace(/,/g, ""))];
  });
}
process.stdout.write(JSON.stringify({
  income: rows(els["rolling-income"]),
  expenses: rows(els["rolling-expenses"]),
  hint: els["rolling-breakdown-hint"].textContent,
}));
