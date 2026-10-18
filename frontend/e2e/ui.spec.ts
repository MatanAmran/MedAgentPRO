import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { NEW_CONVERSATION } from "./helpers";

// UI refresh, phase A (18.10, docs/design/ui-refresh): every state of the new screen passes axe in full
// (WCAG 2.2 A and AA, contrast included: Test 40, NFR 24), the picker and Stop work from the keyboard, and
// Export downloads what the card shows.

const LATE = "Which suppliers were late in weeks 1 to 13?";

async function axeClean(page: Page, where: string) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  const found = r.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(" ")}: ${n.failureSummary}`));
  expect(found, `axe at ${where}`).toEqual([]);
}

async function askExample(page: Page, question: string) {
  await page.getByTestId("examples").getByRole("button", { name: question, exact: true }).click();
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("examples")).toBeVisible();
});

test("axe holds on the empty screen, an answer, the open picker and the open panel", async ({ page }) => {
  await axeClean(page, "the empty screen");
  await page.getByRole("button", NEW_CONVERSATION).click();
  await askExample(page, LATE);
  await expect(page.getByTestId("exchange")).toHaveCount(1, { timeout: 20_000 });
  await axeClean(page, "an answer with two readings");
  await page.getByTestId("user-picker").click();
  await expect(page.getByRole("listbox", { name: "Demo user" })).toBeVisible();
  await axeClean(page, "the open user picker");
  await page.keyboard.press("Escape");
  await page.getByTestId("exchange").getByRole("button", { name: /How this was answered/ }).click();
  await axeClean(page, "the open How this was answered panel");
  // one scrollbar: the thread scrolls, the page does not (the panel's sr-only caption once stretched it)
  expect(await page.evaluate(() => document.documentElement.scrollHeight - document.documentElement.clientHeight)).toBe(0);
});

test("the timeline shows the stages while a question runs, and passes axe", async ({ page }) => {
  await askExample(page, LATE);
  const timeline = page.getByTestId("timeline");
  await expect(timeline).toBeVisible();
  await expect(timeline.locator('[data-state="active"]')).toHaveCount(1);
  await axeClean(page, "the timeline");
  await expect(page.getByTestId("exchange")).toHaveCount(1, { timeout: 20_000 });
  await expect(timeline).toHaveCount(0);
  await expect(page.getByTestId("agent-row")).toContainText("Planned 2 readings");
});

test("Stop from the keyboard ends the wait, and the conversation takes the next question", async ({ page }) => {
  await askExample(page, LATE);
  const stop = page.getByRole("button", { name: "Stop" });
  await expect(stop).toBeVisible();
  await stop.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("pending").getByRole("alert")).toHaveText("Stopped. The question was not answered.");
  await expect(page.getByLabel("Send")).toBeDisabled(); // nothing typed yet
  await page.getByLabel("Question").fill("Why is S-014 late so often?");
  await page.getByLabel("Question").press("Enter");
  await expect(page.getByTestId("exchange").last()).toHaveAttribute("data-outcome", "referred_to_investigation", {
    timeout: 30_000,
  });
});

test("Export downloads the rows shown, named by how many of how many; Copy is a button", async ({ page }) => {
  await askExample(page, LATE);
  await expect(page.getByTestId("exchange")).toHaveCount(1, { timeout: 20_000 });
  const card = page.getByTestId("reading").first();
  const button = card.getByTestId("export-csv");
  await expect(button).toHaveText(/^Export \d+ rows?$/);
  const shown = Number((await button.textContent())?.match(/\d+/)?.[0]);
  const [download] = await Promise.all([page.waitForEvent("download"), button.click()]);
  expect(download.suggestedFilename()).toMatch(new RegExp(`^late_by_delivery_${shown}_of_\\d+\\.csv$`));
  const text = await (await download.createReadStream()).toArray().then((c) => Buffer.concat(c).toString("utf-8"));
  expect(text.trim().split("\r\n")).toHaveLength(shown + 1);
  await expect(card.getByRole("button", { name: /Copy the answer/ })).toBeVisible();
});

test("the conversation list groups and searches", async ({ page }) => {
  await page.getByRole("button", NEW_CONVERSATION).click();
  await askExample(page, LATE);
  await expect(page.getByTestId("exchange")).toHaveCount(1, { timeout: 20_000 });
  await expect(page.getByRole("region", { name: "Today" })).toContainText(LATE);
  await page.getByLabel("Search conversations").fill("no conversation is called this");
  await expect(page.getByText("No conversation matches.")).toBeVisible();
});
