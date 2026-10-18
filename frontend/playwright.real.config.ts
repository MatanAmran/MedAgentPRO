import { join } from "node:path";
import { defineConfig } from "@playwright/test";
import { python, screen, shared } from "./playwright.config";

// The real-model path, end to end, with a fake provider (task 9 review, addition 2): the server runs
// main.create_app("real") with LiteLLM's completion() answering from the scripts. No key, no network,
// a spend ledger of its own (backend/tools/fake_real_server.py).
//   npx playwright test -c playwright.real.config.ts
export default defineConfig({
  ...shared,
  testDir: "e2e",
  testMatch: ["real.spec.ts", "panel.spec.ts"],
  webServer: [
    {
      command: `${python} -m demo_data.seed && ${python} -m tools.fake_real_server --panel`,
      cwd: join(__dirname, "..", "backend"),
      url: "http://127.0.0.1:8000/api/health",
      reuseExistingServer: false,
      timeout: 60_000,
    },
    screen,
  ],
});
