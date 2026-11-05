import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { expect, it } from "vitest";

const FORBIDDEN = /mock-source|live-source|alt-source|fleet-repository|expand-seed|load-seed|fleet\.json|MockDataSource|LiveDataSource|AltMemorySource|FleetRepository|loadSeedFile/;

it("ui_imports_interface_only", () => {
  const root = process.cwd();
  const uiFiles = [
    ...walk(path.join(root, "app")),
    ...walk(path.join(root, "components")),
    path.join(root, "middleware.ts"),
  ];
  expect(uiFiles.length).toBeGreaterThan(5);
  for (const file of uiFiles) {
    const text = readFileSync(file, "utf8");
    expect(text, path.relative(root, file)).not.toMatch(FORBIDDEN);
  }

  for (const file of walk(path.join(root, "lib/roi"))) {
    const text = readFileSync(file, "utf8");
    expect(text, file).not.toMatch(/mock-source|fleet\.json|getDataSource|loadSeedFile|MockDataSource/);
  }
});

function walk(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) found.push(...walk(full));
    else if (/\.(ts|tsx|js|mjs)$/.test(entry)) found.push(full);
  }
  return found;
}
