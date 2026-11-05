import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

test("review_gate", async ({ page }) => {
  await signIn(page, "inv_avery");
  await page.goto("/cars/car_sable?month=2026-10");
  await expect(page.getByTestId("month-maintenance")).toHaveText("$180");
  await expect(page.getByTestId("cumulative-car_sable")).toHaveText("$1,680");

  await page.goto("/invoices");
  await page.locator("input[name='image']").setInputFiles("e2e/fixtures/invoice-DEMO-C3.png");
  await page.getByRole("button", { name: "Upload for review" }).click();
  await expect(page.getByRole("heading", { name: "Review before posting" })).toBeVisible();
  await expect(page.getByLabel("Car")).toHaveValue("car_sable");
  const reviewUrl = page.url();

  await page.goto("/cars/car_sable?month=2026-10");
  await expect(page.getByTestId("month-maintenance")).toHaveText("$180");
  await expect(page.getByTestId("cumulative-car_sable")).toHaveText("$1,680");
  await expect(page.locator("[data-source='invoice']")).toHaveCount(0);

  await page.goto(reviewUrl);
  await page.getByRole("button", { name: "Approve and post expense" }).click();
  await expect(page.getByRole("heading", { name: "Sable" })).toBeVisible();
  await expect(page.getByTestId("month-maintenance")).toHaveText("$305");
  await expect(page.getByTestId("cumulative-car_sable")).toHaveText("$1,555");
  await expect(page.locator("[data-source='invoice']")).toHaveCount(1);
});
