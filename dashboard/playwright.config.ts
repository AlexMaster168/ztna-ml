import { defineConfig } from "@playwright/test";

// Runs against the compose stack (docker compose up); credentials come from the env.
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: process.env.DASHBOARD_URL ?? "http://localhost:3000",
    viewport: { width: 1360, height: 900 },
    trace: "retain-on-failure",
  },
});
