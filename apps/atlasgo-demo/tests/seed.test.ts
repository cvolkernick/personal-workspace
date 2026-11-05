import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { expect, it } from "vitest";
import { expandSeed, type SeedFile } from "@/lib/data/expand-seed";
import { loadSeedFile } from "@/lib/data/load-seed";
import { CAR_STATUSES } from "@/lib/data/types";

const FAKE_NAMES = new Set([
  "Avery Quinn",
  "Blake Moreno",
  "Casey Nguyen",
  "Jordan Hale",
  "Riley Cho",
  "Samira Adel",
  "Noah Pell",
  "Morgan Ellis",
]);

const PHONE = /\b\d{3}[-.\s)]\d{3}[-.\s]\d{4}\b|\(\d{3}\)\s*\d{3}[-.\s]\d{4}/;
const DENY = /\b(justin|christopher|volkernick|chris)\b/i;

it("no_real_pii", () => {
  const raw = readFileSync(path.join(process.cwd(), "lib/data/seed/fleet.json"), "utf8");
  expect(raw).not.toMatch(PHONE);
  expect(raw).not.toMatch(DENY);
  expect(raw).not.toMatch(/@(gmail|yahoo|outlook|hotmail|icloud)\./i);

  const seed = loadSeedFile();
  expect(seed.investors).toHaveLength(3);
  expect(seed.cars).toHaveLength(8);
  expect(seed.months).toEqual(["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]);
  const statuses = new Set(seed.statuses.map((status) => status.status));
  for (const status of CAR_STATUSES) expect(statuses.has(status)).toBe(true);

  for (const investor of seed.investors) {
    expect(FAKE_NAMES.has(investor.name)).toBe(true);
    expect(investor.email).toMatch(/^[a-z.]+@example\.com$/);
  }
  for (const status of seed.statuses) {
    if (status.renter) expect(FAKE_NAMES.has(status.renter)).toBe(true);
  }
  for (const car of seed.cars) {
    expect(car.plate).toMatch(/^DEMO-C[1-8]$/);
    expect(car.vin).toMatch(/^DEMO0ATLASVIN\d{4}$/);
    expect(car.vin).toMatch(/[IOQ]/);
    expect(car.purchaseCost).toBeGreaterThanOrEqual(4000);
    expect(car.purchaseCost).toBeLessThanOrEqual(8000);
    expect(car.monthlyPayment).toBeGreaterThanOrEqual(0);
    expect(car.monthlyPayment).toBeLessThanOrEqual(250);
  }
  for (const schedule of seed.schedules) {
    const amounts = [schedule, ...Object.values(schedule.overrides ?? {})];
    for (const amount of amounts) {
      if (amount.gross === undefined) continue;
      expect(amount.gross).toBeGreaterThanOrEqual(600);
      expect(amount.gross).toBeLessThanOrEqual(1400);
    }
  }

  const expanded = expandSeed(seed as SeedFile);
  expect(expanded.trips).toHaveLength(48);
  expect(expanded.expenses).toHaveLength(96);

  const root = process.cwd();
  const files = [
    path.join(root, "README.md"),
    path.join(root, "middleware.ts"),
    ...sourceFiles(path.join(root, "app")),
    ...sourceFiles(path.join(root, "components")),
    ...sourceFiles(path.join(root, "lib")),
  ];
  for (const file of files) {
    const text = readFileSync(file, "utf8");
    expect(text, file).not.toMatch(PHONE);
    expect(text, file).not.toMatch(DENY);
  }
});

function sourceFiles(root: string): string[] {
  const skip = new Set(["node_modules", ".next", "test-results", "playwright-report"]);
  const found: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      if (skip.has(entry)) continue;
      const full = path.join(dir, entry);
      const stat = statSync(full);
      if (stat.isDirectory()) walk(full);
      else if (/\.(ts|tsx|js|mjs|json|css|md)$/.test(entry)) found.push(full);
    }
  };
  walk(root);
  return found;
}
