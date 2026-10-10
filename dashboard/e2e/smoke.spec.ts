import { expect, test } from "@playwright/test";

import { login } from "./helpers";

test("unauthenticated users are sent to login", async ({ page }) => {
  await page.goto("/nodes");
  await expect(page).toHaveURL(/\/login\?next=%2Fnodes/);
});

test("wrong password shows an error", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Username").fill("admin");
  await page.getByLabel("Password").fill("definitely-wrong");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText(/Invalid username or password|Too many attempts/)).toBeVisible();
});

test("quarantine from the UI shows up live on another tab", async ({ page, context }) => {
  await login(page);
  await expect(page.getByText("connected", { exact: true })).toBeVisible();

  const nodes = await context.newPage();
  await nodes.goto("/nodes");
  const row = nodes.getByRole("row", { name: /peer-normal-2/ });
  await row.getByRole("button", { name: "Quarantine" }).click();
  await expect(row.getByText("quarantined", { exact: true })).toBeVisible();

  // overview tab receives the event over SSE without a reload
  await expect(page.getByText(/quarantined by admin/)).toBeVisible({ timeout: 10_000 });

  await row.getByRole("button", { name: "Release" }).click();
  await expect(row.getByText("active", { exact: true })).toBeVisible();
  await expect(page.getByText(/active by admin/)).toBeVisible({ timeout: 10_000 });
});

test("node detail renders charts and requests", async ({ page }) => {
  await login(page);
  await page.goto("/nodes");
  await page.getByRole("link", { name: "peer-normal", exact: true }).click();
  await expect(page.getByRole("heading", { name: /peer-normal/ })).toBeVisible();
  await expect(page.getByRole("img", { name: "Requests per 30 s window" })).toBeVisible();
  await expect(page.getByText("Recent requests")).toBeVisible();
});

test("every section loads", async ({ page }) => {
  await login(page);
  for (const [link, heading] of [
    ["Incidents", "Incidents"],
    ["Models", "Models"],
    ["Enforcement", "Enforcement"],
    ["Audit", "Audit log"],
  ]) {
    await page.getByRole("link", { name: link, exact: true }).click();
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
  }
});
