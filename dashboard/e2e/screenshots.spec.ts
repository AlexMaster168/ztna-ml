import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { login } from "./helpers";

// Documentation screenshots: SCREENSHOTS=1 npx playwright test e2e/screenshots.spec.ts
test.skip(!process.env.SCREENSHOTS, "set SCREENSHOTS=1 to capture docs screenshots");
test.setTimeout(180_000);

const OUT = path.resolve(__dirname, "../../docs/screenshots");

async function shot(page: Page, name: string, fullPage = true) {
  // the SSE stream keeps the network busy, so never wait for "networkidle"
  await page.waitForLoadState("load");
  await page.waitForTimeout(700);
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage });
}

test("light theme", async ({ page }) => {
  await page.goto("/login");
  await shot(page, "01-login", false);

  await login(page);
  await expect(page.getByText("connected", { exact: true })).toBeVisible();
  // produce a couple of live events for the feed from a second tab
  const other = await page.context().newPage();
  await other.goto("/nodes");
  const row = other.getByRole("row", { name: /peer-normal-2/ });
  await row.getByRole("button", { name: "Quarantine" }).click();
  await expect(row.getByText("quarantined", { exact: true })).toBeVisible();
  await row.getByRole("button", { name: "Release" }).click();
  await expect(row.getByText("active", { exact: true })).toBeVisible();
  await other.close();
  await expect(page.getByText(/active by admin/)).toBeVisible({ timeout: 10_000 });
  await shot(page, "02-overview");

  await page.goto("/nodes");
  await shot(page, "03-nodes");

  await page.getByRole("link", { name: "peer-attacker", exact: true }).click();
  await expect(page.getByRole("img", { name: "Trust score, last hour" })).toBeVisible();
  await shot(page, "04-node-attacker");

  await page.goto("/nodes");
  await page.getByRole("link", { name: "peer-dev", exact: true }).click();
  await shot(page, "05-node-developer");

  await page.goto("/incidents?status=");
  await shot(page, "06-incidents");
  await page.locator("table a[href^='/incidents/']").first().click();
  await expect(page.getByText("Automatic actions")).toBeVisible();
  await shot(page, "07-incident");

  await page.goto("/models");
  await shot(page, "08-models");
  await page.locator("a[href^='/models/']").first().click();
  await shot(page, "09-model-report");

  await page.goto("/settings");
  await shot(page, "10-enforcement");

  await page.goto("/audit");
  await shot(page, "11-audit", false);
});

test.describe("dark theme", () => {
  test.use({ colorScheme: "dark" });

  test("overview and node", async ({ page }) => {
    await login(page);
    await page.waitForTimeout(2000);
    await shot(page, "12-overview-dark");
    await page.goto("/nodes");
    await page.getByRole("link", { name: "peer-attacker", exact: true }).click();
    await shot(page, "13-node-attacker-dark");
  });
});
