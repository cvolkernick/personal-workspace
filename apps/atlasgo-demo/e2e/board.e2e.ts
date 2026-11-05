import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

const STATUSES = ["Available", "Rented", "Reserved", "Maintenance", "Out of service"];

test("statuses_filter_overdue", async ({ page }) => {
  await signIn(page, "admin_morgan");
  await expect(page).toHaveURL(/\/board/);
  for (const status of STATUSES) {
    await expect(page.locator(`[data-status="${status}"]`).first()).toBeVisible();
  }
  await expect(page.getByTestId("flag-car_harbor")).toContainText("Overdue");
  await expect(page.getByTestId("flag-car_quill")).toContainText("Due back today");
  await expect(page.locator("[data-car='car_cedar']")).toContainText("18");

  await page.getByLabel("Filter by status").selectOption("Maintenance");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(page.locator("[data-testid='board-row']")).toHaveCount(1);
  await expect(page.locator("[data-car='car_sable']")).toBeVisible();
  await expect(page.locator("[data-car='car_harbor']")).toHaveCount(0);

  await page.goto("/board?investor=inv_casey");
  await expect(page.locator("[data-testid='board-row']")).toHaveCount(2);
});
