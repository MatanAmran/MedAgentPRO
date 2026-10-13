import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Component and library tests (classic test set, item 6). The end-to-end tests are Playwright's (e2e/).
export default defineConfig({
  resolve: { alias: { "@": fileURLToPath(new URL("./", import.meta.url)) } },
  test: {
    environment: "jsdom",
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**", "e2e/**", ".next/**"],
  },
});
