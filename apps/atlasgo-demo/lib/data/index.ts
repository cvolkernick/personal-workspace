import "server-only";
import { AltMemorySource } from "./alt-source";
import type { DataSource } from "./types";
import { LiveDataSource } from "./live-source";
import { loadSeedFile } from "./load-seed";
import { MockDataSource } from "./mock-source";

const GLOBAL_KEY = "__atlasgoDataSource";

type Cache = { mode?: string; source?: DataSource };

function cache(): Cache {
  const g = globalThis as typeof globalThis & { [GLOBAL_KEY]?: Cache };
  if (!g[GLOBAL_KEY]) g[GLOBAL_KEY] = {};
  return g[GLOBAL_KEY];
}

export function createDataSource(mode = process.env.DATA_SOURCE ?? "mock"): DataSource {
  if (mode === "live") return new LiveDataSource();
  if (mode === "memory") return new AltMemorySource(loadSeedFile());
  if (mode === "mock") return new MockDataSource();
  throw new Error(`Unknown DATA_SOURCE "${mode}". Use mock, live, or memory.`);
}

export function getDataSource(): DataSource {
  const mode = process.env.DATA_SOURCE ?? "mock";
  const slot = cache();
  if (!slot.source || slot.mode !== mode) {
    slot.mode = mode;
    slot.source = createDataSource(mode);
  }
  return slot.source;
}

export function resetDataSourceForTests(): void {
  const slot = cache();
  delete slot.mode;
  delete slot.source;
}

export type { DataSource } from "./types";
