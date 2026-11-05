import "server-only";
import { AltMemorySource } from "./alt-source";
import type { DataSource } from "./types";
import { LiveDataSource } from "./live-source";
import { loadSeedFile } from "./load-seed";
import { MockDataSource } from "./mock-source";

const GLOBAL_KEY = "__atlasgoDataSource";

type Cache = { mode?: string; source?: DataSource };
type Holder = { [GLOBAL_KEY]?: Cache };

// `next start` gives each edge route its own VM. Next injects one host
// request-context object into every VM; the fleet cache hangs off that object
// so an invoice written on one route is still there on the next. A single
// Workers isolate already shares `globalThis`, which is checked first.
function holders(): Holder[] {
  const slots: Holder[] = [globalThis as Holder];
  const bridge = (globalThis as Holder & { [key: symbol]: unknown })[Symbol.for("@next/request-context")];
  if (bridge && typeof bridge === "object") slots.push(bridge as Holder);
  return slots;
}

function cache(): Cache {
  const slots = holders();
  for (const slot of slots) {
    if (slot[GLOBAL_KEY]) return slot[GLOBAL_KEY];
  }
  const created: Cache = {};
  for (const slot of slots) {
    try {
      slot[GLOBAL_KEY] = created;
    } catch {
      // A frozen host object cannot carry the cache.
    }
  }
  return created;
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
