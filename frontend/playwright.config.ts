import { existsSync } from "node:fs";
import { join } from "node:path";
import { defineConfig } from "@playwright/test";

// End to end on the scripted model (classic test set, item 6; book 6.1: never a real model). Both
// servers are started here, on the ports the server's CORS allows, so stop any dev server first.
// Locally the installed Edge runs the tests (Application Control may block a downloaded Chromium);
// in CI, Playwright's Chromium.

const CI = !!process.env.CI;
const venv = join(__dirname, "..", "backend", "venv", "Scripts", "python.exe");
const python = process.env.PYTHON ?? (existsSync(venv) ? `"${venv}"` : "python");

// Each scripted model output waits this long, so a test can reload while an exchange runs
const SCRIPTED_DELAY = 0.4;

export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  reporter: CI ? "list" : [["list"]],
  use: {
    baseURL: "http://localhost:3000",
    channel: CI ? undefined : "msedge",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: `${python} -m demo_data.seed && ${python} -m main --scripted-delay ${SCRIPTED_DELAY}`,
      cwd: join(__dirname, "..", "backend"),
      url: "http://127.0.0.1:8000/api/health",
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: "node node_modules/next/dist/bin/next dev -p 3000",
      url: "http://localhost:3000",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
