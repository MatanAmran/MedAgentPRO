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
  // the joined answer is what the screen shows; the count opens it, filled by code and not a marked value (D5)
  await expect(card.getByTestId("answer")).toHaveText(/^\d+ suppliers had .* among them S-\d{3}/);
  expect(await refs.first().textContent()).toMatch(/^S-\d{3}$/);
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

// --------------------------------------------------------------------------
// Long list (chat 18.10, 20:30; LongList.dc.html)
// --------------------------------------------------------------------------

test("long list L5: Show archived lists an archived conversation, and Unarchive brings it back", async ({ page }) => {
  await page.getByRole("button", NEW_CONVERSATION).click();
  await askExample(page, LATE);
  await expect(page.getByTestId("exchange")).toHaveCount(1, { timeout: 20_000 });
  const title = `Archived by the L5 test ${Date.now()}`;
  const item = page.getByTestId("conversation-item").filter({ has: page.locator('[aria-current="page"]') });
  await item.getByTestId("conversation-options").click();
  await page.getByRole("menuitem", { name: "Rename" }).click();
  await page.getByLabel("Conversation name").fill(title);
  await page.keyboard.press("Enter");
  const mine = page.getByTestId("conversation-item").filter({ hasText: title });
  await mine.getByTestId("conversation-options").click();
  await page.getByRole("menuitem", { name: "Archive" }).click();
  await expect(mine).toHaveCount(0);

  const toggle = page.getByRole("button", { name: "Show archived" });
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  const archived = page.getByRole("region", { name: "Archived" });
  await expect(archived.getByTestId("conversation-item").filter({ hasText: title })).toHaveCount(1);
  await axeClean(page, "the archived conversations shown");
  await archived.getByTestId("conversation-item").filter({ hasText: title }).getByTestId("conversation-options").click();
  await expect(page.getByRole("menuitem", { name: "Archive", exact: true })).toHaveCount(0);
  await page.getByRole("menuitem", { name: "Unarchive" }).click();
  await expect(archived.getByTestId("conversation-item").filter({ hasText: title })).toHaveCount(0);
  await toggle.click();
  await expect(page.getByRole("region", { name: "Archived" })).toHaveCount(0);
  await expect(page.getByTestId("conversation-item").filter({ hasText: title })).toHaveCount(1);
});

// A long list as the real model can return it (31 parts, 30 shown, an answer naming each): the scripted set
// holds none, so the conversation is served by a route here, in the screen's own shapes
const PARTS = Array.from({ length: 30 }, (_, i) => `P-${10296 + i * 37}`);
const longSegments = PARTS.flatMap((id, i) => [
  ...(i % 5 === 0 ? [{ text: i === 0 ? "Parts below the reorder point include " : " Others in the records are " }] : []),
  { text: id, ref: { alias: "part_id", row: i + 1 } },
  { text: (i + 1) % 5 === 0 ? "." : i % 5 === 3 ? " and " : ", " },
]);
const LONG = {
  exchange_id: "EX-LONG", posed: "Which parts are below their reorder point?", recorded: true, cost: null, rating: null,
  response: {
    outcome: "answered", gaps: [],
    readings: [{
      label: "below_reorder", title: "Stock on hand is below the reorder point",
      checked: "Examined: parts where stock on hand is below the reorder point", result: "rows_returned",
      columns: ["part_id"], headers: { part_id: "part" }, identifiers: ["part_id"],
      rows: PARTS.map((part_id) => ({ part_id })), row_count: 30, total: 31, entity: "parts",
      answer: longSegments.map((s) => s.text).join(""), evidence: ["below_reorder.part_id"],
      answer_segments: longSegments,
    }],
  },
};

test("long list 1-4: a long answer folds, its values read as text, its parts as tags with a filter", async ({ page }, info) => {
  await page.route(/\/api\/conversations$/, (r) =>
    r.request().method() === "GET"
      ? r.fulfill({ json: [{ conversation_id: "CV-LONG", created_at: new Date().toISOString(), title: "Long list", exchanges: 1 }] })
      : r.fallback());
  await page.route(/\/api\/conversations\/CV-LONG$/, (r) =>
    r.fulfill({ json: { conversation_id: "CV-LONG", created_at: new Date().toISOString(), title: "Long list", exchanges: [LONG] } }));
  await page.reload();
  await page.getByTestId("conversation-item").filter({ hasText: "Long list" }).getByRole("button").first().click();
  const card = page.getByTestId("reading");
  await expect(card).toHaveCount(1);
  await expect(card.getByTestId("reading-count")).toHaveText("31 parts · 30 shown");

  // item 1: no chip, red only while pointed at; the comma stays on the value's line
  const refs = card.getByTestId("answer").locator("button.ref");
  const first = refs.first();
  expect(await first.evaluate((e) => getComputedStyle(e).backgroundColor)).toBe("rgba(0, 0, 0, 0)");
  expect(await first.evaluate((e) => getComputedStyle(e).textDecorationStyle)).toBe("dotted");
  expect(await first.evaluate((e) => e.parentElement?.textContent)).toBe(`${PARTS[0]},`);

  // item 2: folded after the first sentences; an arrow onto a folded value unfolds it
  const fold = card.getByTestId("answer-fold");
  await expect(fold).toHaveText("Read the full answer (30 references) ↓");
  await expect(refs).toHaveCount(5);
  await first.focus();
  await page.keyboard.press("End");
  await expect(fold).toHaveAttribute("aria-expanded", "true");
  await expect(refs).toHaveCount(30);
  await expect(page.locator(":focus")).toHaveText(PARTS[29]);
  await expect(card.getByTestId("answer")).toHaveText(LONG.response.readings[0].answer);
  expect(await card.getByTestId("answer").locator("button.ref").evaluateAll(
    (els) => els.filter((e) => (e as HTMLElement).tabIndex === 0).length)).toBe(1);

  // item 3: a tag grid, linked both ways; the filter keeps what holds the text
  const tags = card.getByTestId("reading-tags").getByRole("listitem");
  await expect(tags).toHaveCount(30);
  await expect(card.getByTestId("reading-table")).toHaveCount(0);
  await expect(card.locator(`[data-row="30"]`)).toHaveAttribute("data-hot", "true");
  await tags.nth(2).hover();
  await expect(refs.nth(2)).toHaveClass(/ref-hot/);
  await axeClean(page, "a long list, unfolded, a tag pointed at");
  await card.getByRole("searchbox", { name: "Filter these rows" }).fill(PARTS[3]);
  await expect(tags).toHaveCount(1);
  await expect(card.getByRole("status")).toHaveText("1 of 30 shown");
  await axeClean(page, "the tags filtered");

  if (["edge", "chromium"].includes(info.project.name)) {
    await card.getByRole("searchbox", { name: "Filter these rows" }).fill("");
    await fold.click();
    await page.addStyleTag({ content: "html { filter: grayscale(1); }" });
    await card.screenshot({ path: info.outputPath("grayscale", "10-long-list.png") });
  }
});

test("Phase B task 5: Show all records fills a cut reading from the server, and passes axe", async ({ page }) => {
  await page.getByRole("button", NEW_CONVERSATION).click();
  await askExample(page, LATE);
  await expect(page.getByTestId("exchange")).toHaveCount(1, { timeout: 20_000 });
  const first = page.getByTestId("reading").first();
  await expect(first.getByTestId("reading-count")).toContainText("10 shown");
  const all = first.getByTestId("show-all-records");
  await expect(all).toHaveText(/Show all \d+ records/);
  await all.click();
  await expect(all).toHaveCount(0);
  await expect(first.getByTestId("reading-count")).not.toContainText("shown");
  await expect(first.locator("tbody tr")).toHaveCount(28);
  await axeClean(page, "a reading shown whole");
});
