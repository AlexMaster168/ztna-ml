import { expect, test } from "@playwright/test";

import { SSO_PASSWORD, login, ssoLogin } from "./helpers";

test("unauthenticated users are sent to login", async ({ page }) => {
  await page.goto("/nodes");
  await expect(page).toHaveURL(/\/login\?next=%2Fnodes/);
  await expect(page.getByRole("heading", { name: "Панель керування ZTNA" })).toBeVisible();
});

test("wrong password shows an error", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Ім'я користувача").fill("admin");
  await page.getByLabel("Пароль").fill("definitely-wrong");
  await page.getByRole("button", { name: "Увійти", exact: true }).click();
  await expect(
    page.getByText(/Неправильне ім'я користувача або пароль|Забагато спроб/),
  ).toBeVisible();
});

test("quarantine from the UI shows up live on another tab", async ({ page, context }) => {
  await login(page);
  await expect(page.getByText("підключено", { exact: true })).toBeVisible();

  const nodes = await context.newPage();
  await nodes.goto("/nodes");
  const row = nodes.getByRole("row", { name: /peer-normal-2/ });
  await row.getByRole("button", { name: "У карантин" }).click();
  await expect(row.getByText("карантин", { exact: true })).toBeVisible();

  // overview tab receives the event over SSE without a reload
  await expect(page.getByText(/карантин · admin/)).toBeVisible({ timeout: 10_000 });

  await row.getByRole("button", { name: "Зняти карантин" }).click();
  await expect(row.getByText("активний", { exact: true })).toBeVisible();
  await expect(page.getByText(/активний · admin/)).toBeVisible({ timeout: 10_000 });
});

test("node detail renders charts, requests and resource access", async ({ page }) => {
  await login(page);
  await page.goto("/nodes");
  await page.getByRole("link", { name: "peer-dev", exact: true }).click();
  await expect(page.getByRole("heading", { name: /peer-dev/ })).toBeVisible();
  await expect(page.getByRole("img", { name: "Запити за 30-секундне вікно" })).toBeVisible();
  await expect(page.getByText("Останні запити")).toBeVisible();
  const access = page.locator("section").filter({ hasText: "Доступ до ресурсів зараз" });
  await expect(access.getByRole("row", { name: /admin/ }).getByText("дозволено")).toBeVisible();
});

test("every section loads", async ({ page }) => {
  await login(page);
  for (const [link, heading] of [
    ["Інциденти", "Інциденти"],
    ["Доступ", "Доступ до ресурсів"],
    ["Шлюзи", "Шлюзи"],
    ["Моделі", "Моделі"],
    ["Реагування", "Реагування"],
    ["Сповіщення", "Сповіщення"],
    ["Аудит", "Журнал аудиту"],
  ]) {
    await page.getByRole("link", { name: link, exact: true }).click();
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
  }
  await page.goto("/gateways");
  await expect(page.getByRole("heading", { name: "gw-2" })).toBeVisible();
});

test("language switch to English and back", async ({ page }) => {
  await login(page);
  await page.getByRole("button", { name: "ENG" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Nodes", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "УКР" }).click();
  await expect(page.getByRole("heading", { name: "Огляд" })).toBeVisible();
});

test("single sign-on maps the Keycloak role", async ({ page }) => {
  test.skip(!SSO_PASSWORD, "set E2E_SSO_PASSWORD to test SSO");
  await ssoLogin(page, "taras");
  await expect(page.getByText(/taras · аналітик · SSO/)).toBeVisible();
  // analysts see the audit log but cannot publish resources
  await page.goto("/access");
  await expect(page.getByRole("button", { name: "Опублікувати" })).toHaveCount(0);
  await page.getByRole("link", { name: "Вийти" }).click();
  await expect(page).toHaveURL(/\/login/);
  // the provider session ended too: SSO asks for credentials again
  await page.getByRole("link", { name: /SSO/ }).click();
  await expect(page.locator("#username")).toBeVisible();
});
