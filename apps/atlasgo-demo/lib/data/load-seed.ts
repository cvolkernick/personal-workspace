import { readFileSync } from "node:fs";
import path from "node:path";
import type { SeedFile } from "./expand-seed";

const SEED_PATH = path.join(process.cwd(), "lib/data/seed/fleet.json");

export function loadSeedFile(): SeedFile {
  const raw = readFileSync(SEED_PATH, "utf8");
  return JSON.parse(raw) as SeedFile;
}
