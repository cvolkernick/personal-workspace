import { defineConfig } from "@playwright/test";

const source = process.env.ATLAS_E2E_SOURCE === "memory" ? "memory" : "mock";
const base = source === "memory" ? 3310 : 3210;

function server(port: number) {
  return {
    command: `ATLASGO_TODAY=2026-11-05 DATA_SOURCE=${source} npm run start -- --port ${port}`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 120_000,
  };
}

export default defineConfig({
  testDir: "e2e",
  testMatch: "**/*.e2e.ts",
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  projects: [
    {
      name: "desktop",
      testIgnore: [/invoice/, /smoke/],
      use: { baseURL: `http://127.0.0.1:${base}`, viewport: { width: 1280, height: 800 } },
    },
    {
      name: "smoke-desktop",
      testMatch: /smoke/,
      use: { baseURL: `http://127.0.0.1:${base}`, viewport: { width: 1280, height: 800 } },
    },
    {
      name: "smoke-mobile",
      testMatch: /smoke/,
      use: { baseURL: `http://127.0.0.1:${base}`, viewport: { width: 390, height: 844 } },
    },
    {
      name: "invoice",
      testMatch: /invoice/,
      use: { baseURL: `http://127.0.0.1:${base + 1}`, viewport: { width: 1280, height: 800 } },
    },
  ],
  webServer: [server(base), server(base + 1)],
});
