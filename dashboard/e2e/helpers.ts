import { expect, type Page } from "@playwright/test";

export const USER = process.env.E2E_USER ?? "admin";
export const PASSWORD = process.env.E2E_PASSWORD ?? "";
export const SSO_PASSWORD = process.env.E2E_SSO_PASSWORD ?? "";

export async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Ім'я користувача").fill(USER);
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Увійти", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Огляд" })).toBeVisible();
}

/** Single sign-on through Keycloak (realm "ztna"). Keycloak's form ids are stable across
 * its themes and languages. */
export async function ssoLogin(page: Page, username: string) {
  await page.goto("/login");
  await page.getByRole("link", { name: /SSO/ }).click();
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(SSO_PASSWORD);
  await page.locator("#kc-login").click();
  await expect(page.getByRole("heading", { name: "Огляд" })).toBeVisible();
}
