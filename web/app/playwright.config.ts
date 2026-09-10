import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL: process.env.BLOODNET_BROWSER_BASE_URL || "http://127.0.0.1:5173",
    trace: "on-first-retry",
    video: "retain-on-failure",
  },
  globalSetup: "./e2e/global-setup",
  webServer: {
    command: "npm run dev -- --host 127.0.0.1",
    url: process.env.BLOODNET_BROWSER_BASE_URL || "http://127.0.0.1:5173",
    reuseExistingServer: false,
    env: {
      ...process.env,
      VITE_BLOODNET_API_BASE_URL: process.env.VITE_BLOODNET_API_BASE_URL ?? "",
    },
  },
});
