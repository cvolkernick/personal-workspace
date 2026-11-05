import { expect, test } from "@playwright/test";
import { signIn, signOut } from "./helpers";

test("investor_isolation", async ({ page }) => {
  await signIn(page, "inv_avery");
  await expect(page.getByRole("link", { name: "Harbor" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Cedar" })).toHaveCount(0);
  const own = await page.goto("/cars/car_harbor");
  expect(own?.status()).toBe(200);
  await expect(page.getByRole("heading", { name: "Harbor" })).toBeVisible();

  const blocked = await page.goto("/cars/car_cedar");
  expect(blocked?.status()).toBe(404);
  await expect(page.getByText("Not found", { exact: true })).toBeVisible();

  await page.goto("/dashboard");
  await signOut(page);
  await signIn(page, "inv_blake");
  await expect(page.getByRole("link", { name: "Cedar" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Harbor" })).toHaveCount(0);
  const reverse = await page.goto("/cars/car_harbor");
  expect(reverse?.status()).toBe(404);
  await expect(page.getByText("Not found", { exact: true })).toBeVisible();
});
