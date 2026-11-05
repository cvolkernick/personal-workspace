import { expect, test } from "@playwright/test";
import { ROADMAP_ITEMS } from "../lib/roadmap";
import { signIn } from "./helpers";

test("items_present", async ({ page }) => {
  await signIn(page, "inv_casey");
  await page.goto("/roadmap");
  await expect(page.getByRole("heading", { name: "Coming next" })).toBeVisible();
  for (const item of ROADMAP_ITEMS) {
    await expect(page.getByRole("listitem").filter({ hasText: item })).toBeVisible();
  }
});
