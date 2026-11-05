import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

test("badge_visible", async ({ page }) => {
  await signIn(page, "inv_avery");
  await page.goto("/dashboard?month=2026-10");
  await expect(page.getByTestId("roi-badge-car_harbor")).toBeVisible();
  await expect(page.getByTestId("roi-badge-car_lumen")).toHaveCount(0);
  await expect(page.getByTestId("roi-badge-car_sable")).toHaveCount(0);
  await expect(page.getByTestId("maint-alert-car_sable")).toHaveText("Maintenance alert");
  await expect(page.getByTestId("maint-alert-car_harbor")).toHaveCount(0);
  await expect(page.getByTestId("cumulative-car_harbor")).toHaveText("$7,200");
  await expect(page.getByTestId("fleet-cumulative")).toHaveText("$13,320");
});
