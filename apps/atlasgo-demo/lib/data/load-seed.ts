import seed from "./seed/fleet.json";
import type { SeedFile } from "./expand-seed";
export function loadSeedFile(): SeedFile { return seed as unknown as SeedFile; }
