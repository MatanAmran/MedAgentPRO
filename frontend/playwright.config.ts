import { existsSync } from "node:fs";
import { join } from "node:path";
import { defineConfig, type PlaywrightTestConfig } from "@playwright/test";

// End to end on the scripted model (classic test set, item 6; book 6.1: never a real model). Both
// servers are started here, on the ports the server's CORS allows, so stop any dev server first.
// The screen runs as the demo runs it: built, then started (task 9 review, addition 1).
// Engines (Test 38, NFR 22): Chromium, Firefox and WebKit in CI; locally the installed Edge only, since
// Application Control may block a downloaded browser (task 9, B1).

const CI = !!process.env.CI;
const venv = join(__dirname, "..", "backend", "venv", "Scripts", "python.exe");
export const python = process.env.PYTHON ?? (existsSync(venv) ? `"${venv}"` : "python");

// Each scripted model output waits this long, so a test can reload while an exchange runs
const SCRIPTED_DELAY = 0.4;

export const screen = {
  command: "node node_modules/next/dist/bin/next build && node node_modules/next/dist/bin/next start -p 3000",
  url: "http://localhost:3000",
  reuseExistingServer: false,
  timeout: 240_000,
};

export const shared: PlaywrightTestConfig = {
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  reporter: "list",
  use: { baseURL: "http://localhost:3000", trace: "retain-on-failure" },
  projects: CI
    ? [
        { name: "chromium", use: { browserName: "chromium" } },
        { name: "firefox", use: { browserName: "firefox" } },
        { name: "webkit", use: { browserName: "webkit" } },
      ]
    : [{ name: "edge", use: { browserName: "chromium", channel: "msedge" } }],
};

export default defineConfig({
  ...shared,
  testDir: "e2e",
  testIgnore: "real.spec.ts",
  webServer: [
    {
      command: `${python} -m demo_data.seed && ${python} -m main --scripted-delay ${SCRIPTED_DELAY}`,
      cwd: join(__dirname, "..", "backend"),
      url: "http://127.0.0.1:8000/api/health",
      reuseExistingServer: false,
      timeout: 60_000,
    },
    screen,
  ],
});
