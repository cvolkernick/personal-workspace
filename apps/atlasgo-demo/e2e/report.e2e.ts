import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

test("csv_matches", async ({ page }) => {
  await signIn(page, "inv_avery");
  await page.goto("/reports/2026-10");
  await expect(page.getByTestId("report-net")).toHaveText("$2,020");
  await expect(page.getByRole("cell", { name: "Harbor" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Turo" })).toBeVisible();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByTestId("csv-download").click(),
  ]);
  const text = await readFile(await download.path(), "utf8");
  expect(text).toContain("Harbor,1260,40,20,0,1200,1200");
  expect(text).toContain("TOTAL,2800,270,80,430,2450,2020");
  expect(text).toContain("Turo,1400,140,1260");
});
