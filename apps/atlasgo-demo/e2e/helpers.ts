import { expect, type Page } from "@playwright/test";

export async function signIn(page: Page, actorId: string) {
  await page.context().clearCookies();
  await page.goto("/");
  await page.locator(`[data-actor="${actorId}"]`).click();
  await page.waitForURL(/\/(dashboard|board)/);
  await expect(page.getByText("Demo — sample data")).toBeVisible();
}

export async function signOut(page: Page) {
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Fleet return, on one page" })).toBeVisible();
}
