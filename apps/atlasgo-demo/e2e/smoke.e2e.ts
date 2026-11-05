import { expect, test, type Page } from "@playwright/test";
import { signIn, signOut } from "./helpers";

test("loads sample pages without console errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const text = message.text();
    if (text.includes("favicon")) return;
    errors.push(text);
  });

  await signIn(page, "inv_avery");
  for (const path of ["/dashboard", "/cars/car_harbor", "/reports", "/reports/2026-10", "/invoices", "/roadmap"]) {
    const response = await page.goto(path);
    expect(response?.status(), path).toBeLessThan(400);
    await expect(page.getByText("Demo — sample data")).toBeVisible();
  }
  await assertMobileStack(page);

  await signOut(page);
  await signIn(page, "admin_morgan");
  const board = await page.goto("/board");
  expect(board?.status()).toBeLessThan(400);
  await expect(page.getByRole("heading", { name: "Car status" })).toBeVisible();
  expect(errors).toEqual([]);
});

async function assertMobileStack(page: Page) {
  const viewport = page.viewportSize();
  if (!viewport || viewport.width > 600) return;
  const box = await page.locator("header.top").boundingBox();
  expect(box?.width ?? 0).toBeLessThanOrEqual(viewport.width);
}
