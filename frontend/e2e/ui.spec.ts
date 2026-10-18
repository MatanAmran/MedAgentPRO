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

test("phase B: rename, pin and archive from the menu; archived is hidden, its exchange still served", async ({ page }) => {
  const created = page.waitForResponse((r) => r.url().endsWith("/api/conversations") && r.request().method() === "POST");
  await page.getByRole("button", NEW_CONVERSATION).click();
  const { conversation_id } = await (await created).json();
  await askExample(page, LATE);
  await expect(page.getByTestId("exchange")).toHaveCount(1, { timeout: 20_000 });
  // the conversation just asked in: other tests on the shared server leave conversations with the same title
  const item = page.getByTestId("conversation-item").filter({ has: page.locator('[aria-current="page"]') });

  // rename, from the keyboard
  const options = item.getByTestId("conversation-options");
  await options.focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menu")).toBeVisible();
  await axeClean(page, "the open conversation menu");
  await page.keyboard.press("Enter"); // Rename, the first item
  await page.getByLabel("Conversation name").fill("Renamed by the phase B test");
  await page.keyboard.press("Enter");
  const renamed = page.getByTestId("conversation-item").filter({ hasText: "Renamed by the phase B test" });
  await expect(renamed).toHaveCount(1);

  // pin: the conversation moves to the Pinned group
  await renamed.getByTestId("conversation-options").click();
  await page.getByRole("menuitem", { name: "Pin to top" }).click();
  await expect(page.getByRole("region", { name: "Pinned" })).toContainText("Renamed by the phase B test");

  // archive: gone from the list, the thread cleared; the server still serves its exchange by id
  await page.getByRole("region", { name: "Pinned" }).getByTestId("conversation-options").click();
  await page.getByRole("menuitem", { name: "Archive" }).click();
  await expect(page.getByTestId("conversation-item").filter({ hasText: "Renamed by the phase B test" })).toHaveCount(0);
  await expect(page.getByTestId("exchange")).toHaveCount(0);
  const kept = await (
    await page.request.get(`http://127.0.0.1:8000/api/conversations/${conversation_id}`, { headers: { "X-Demo-User": "AP-01" } })
  ).json();
  expect(kept.archived).toBe(true);
  expect(kept.exchanges).toHaveLength(1);
});

test("phase C: pointing at or focusing a value in the answer marks its row", async ({ page }) => {
  await page.getByRole("button", NEW_CONVERSATION).click();
  await askExample(page, LATE);
  await expect(page.getByTestId("exchange")).toHaveCount(1, { timeout: 20_000 });
  const card = page.getByTestId("reading").first();
  const refs = card.getByTestId("answer").locator("button.ref");
  expect(await refs.count()).toBeGreaterThan(1);

  const first = refs.first();
  const row = first.getAttribute("data-ref-row");
  await first.hover();
  await expect(card.locator(`[data-row="${await row}"]`)).toHaveAttribute("data-hot", "true");
  await expect(first).toHaveClass(/ref-hot/);

  const second = refs.nth(2);
  await second.focus();
  await expect(card.locator(`[data-row="${await second.getAttribute("data-ref-row")}"]`)).toHaveAttribute("data-hot", "true");
  await axeClean(page, "a value in the answer focused");
  // the joined answer is what the screen shows
  await expect(card.getByTestId("answer")).toHaveText(/^S-\d{3} had /);
});

test("the conversation list and each answer stay one tab stop, however many conversations the server holds", async ({ page }) => {
  await page.getByRole("button", NEW_CONVERSATION).click();
  await askExample(page, LATE);
  await expect(page.getByTestId("exchange")).toHaveCount(1, { timeout: 20_000 });
  expect(await page.getByTestId("conversation-item").count()).toBeGreaterThan(1);
  const stops = (sel: string) =>
    page.locator(sel).evaluateAll((els) => els.filter((e) => (e as HTMLElement).tabIndex >= 0 && !e.hasAttribute("disabled")).length);
  expect(await stops('[data-testid="conversations"] button')).toBe(2); // the current conversation and its options
  for (const answer of await page.getByTestId("answer").all()) {
    expect(await answer.locator("button.ref").evaluateAll((els) => els.filter((e) => (e as HTMLElement).tabIndex === 0).length)).toBe(1);
  }
});
