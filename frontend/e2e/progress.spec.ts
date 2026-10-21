import { expect, type Page, test } from "@playwright/test";

// Test 46 (NFR 28): a question posed shows that it is in progress until its response, and not after it.
// The configured time is Table 15's "Indication that a question is in progress": a delay beyond one second is
// noticed and calls for an indication [36]. Each exchange is held past it: the request waits MODEL_DELAY_MS
// before it reaches the server, which also waits before each scripted model call (--scripted-delay). The store
// that cannot be reached (step 4) is the server's own response for it, given by the route: making the shared
// store unreachable would break the engines that run after (task 9, one server across engines).

const CONFIGURED_MS = 1000;
const MODEL_DELAY_MS = 1500;
const QUESTION = "Which suppliers were late in weeks 1 to 13?";
const UNREACHABLE = "What did S-014 deliver in March?";
const NOTICE = "The company records could not be reached. The question was not answered.";

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** The indication and the answers, read in one evaluation in the page, so a sample never falls between them. */
async function sample(page: Page): Promise<{ working: number; answers: number }> {
  return page.evaluate(() => ({
    working: document.querySelectorAll('[data-testid="timeline"]').length,
    answers: document.querySelectorAll('[data-testid="exchange"]').length,
  }));
}

/** Poses the question; the indication appears within the configured time, stays until the response is presented,
 * and is gone with it. Returns how long the indication was seen. */
async function posedUntilPresented(page: Page, question: string): Promise<number> {
  await page.getByLabel("Question").fill(question);
  const posed = Date.now();
  await page.getByLabel("Question").press("Enter");
  await expect(page.getByTestId("timeline")).toBeVisible({ timeout: CONFIGURED_MS });
  expect(Date.now() - posed).toBeLessThan(CONFIGURED_MS);
  for (;;) {
    const s = await sample(page);
    if (s.answers > 0) {
      expect(s.working).toBe(0); // not shown after the response
      return Date.now() - posed;
    }
    expect(s.working).toBe(1); // shown until it
    expect(Date.now() - posed).toBeLessThan(30_000);
    await page.waitForTimeout(100);
  }
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("mode")).toHaveText("Scripted model");
});

test("Test 46: the indication appears within the configured time and stays until the answer", async ({ page }) => {
  await page.route(/\/api\/conversations\/[^/]+\/exchanges$/, async (route) => {
    await wait(MODEL_DELAY_MS);
    await route.continue();
  });
  const seen = await posedUntilPresented(page, QUESTION);
  expect(seen).toBeGreaterThan(MODEL_DELAY_MS); // the response took longer than the configured time
  await expect(page.getByTestId("reading")).toHaveCount(2);
});

test("Test 46: with the store unreachable, the indication stays until the failure is presented", async ({ page }) => {
  const response = { outcome: "source_unreachable", readings: [], gaps: [], notice: NOTICE };
  let posed = false;
  await page.route(/\/api\/conversations\/[^/]+\/exchanges$/, async (route) => {
    await wait(MODEL_DELAY_MS);
    posed = true;
    const stage = (s: string) => `event: stage\ndata: ${JSON.stringify({ stage: s })}\n\n`;
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: stage("intent") + stage("retrieval")
        + `event: result\ndata: ${JSON.stringify({ exchange_id: "EX-T46", recorded: false, response, cost: null })}\n\n`,
    });
  });
  // the screen reads the conversation back from the server, which holds the exchange as not recorded; the
  // response here comes from the route, not the server, so this checks NFR 28 only, never FR 15 (chat 20:31)
  await page.route(/\/api\/conversations\/CV-[^/?]+$/, async (route) => {
    if (route.request().method() !== "GET" || !posed) return route.fallback();
    const id = route.request().url().split("/").pop();
    await route.fulfill({
      json: {
        conversation_id: id, created_at: new Date().toISOString(), title: UNREACHABLE,
        exchanges: [{ exchange_id: "EX-T46", posed: UNREACHABLE, recorded: false, response, cost: null, rating: null }],
      },
    });
  });
  await posedUntilPresented(page, UNREACHABLE);
  await expect(page.getByTestId("notice")).toHaveText(NOTICE);
});
