import { expect, test } from "@playwright/test";

// The real-model path, as `python -m main --real` serves it, with a fake provider (task 9 review,
// addition 2). The screen shows the cost of each answer and the budget left (T3), lists no scripted
// examples, and shows the per-exchange cap's notice where a question would need more than it allows.

async function ask(page: import("@playwright/test").Page, question: string) {
  await page.getByLabel("Question").fill(question);
  await page.getByLabel("Question").press("Enter");
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("mode")).toHaveText("Live model");
});

test("no scripted examples on the real model", async ({ page }) => {
  await expect(page.getByLabel("Question")).toBeVisible();
  await expect(page.getByTestId("examples")).toHaveCount(0);
});

test("an answer shows its cost and the budget left", async ({ page }) => {
  await ask(page, "Which suppliers were late in weeks 1 to 13?");
  const answer = page.getByTestId("exchange").last();
  await expect(answer).toHaveAttribute("data-outcome", "answered", { timeout: 20_000 });
  await expect(answer.getByTestId("cost")).toHaveText(/^\$0\.\d{4} for this answer · \$4\.\d{2} of the demo budget left$/);
});

test("the per-exchange cap stops a question with its own notice, and its cost is shown", async ({ page }) => {
  await ask(page, "Which purchase orders were delivered late in September?");
  const answer = page.getByTestId("exchange").last();
  await expect(answer).toHaveAttribute("data-outcome", "request_not_completed", { timeout: 20_000 });
  await expect(answer.getByTestId("notice")).toHaveText(
    "This question needs more of the model than one answer may use. The question was not answered. Ask about fewer records.",
  );
  await expect(answer.getByTestId("cost")).toBeVisible();
});

test("says what can be asked on an empty screen, as text only (long list, item 6)", async ({ page }) => {
  const text = page.getByTestId("can-ask");
  await expect(text).toContainText("suppliers");
  await expect(text).toContainText("forecast");
  await expect(text.locator("button, a")).toHaveCount(0);
});

test("a name with several matches lists them; the full name answers and Examined says what matched (chat 20.10)", async ({
  page,
}) => {
  await ask(page, "What is on hand for Strain relief?");
  const several = page.getByTestId("exchange").last();
  await expect(several).toHaveAttribute("data-outcome", "answered", { timeout: 20_000 });
  await expect(several.getByTestId("reading-notice")).toHaveText(
    "'Strain relief' matches several part descriptions. Ask again with the full name.",
  );
  const candidates = several.getByTestId("reading-candidates");
  await expect(candidates.getByRole("listitem")).toHaveCount(6);
  await expect(candidates.getByRole("listitem").first()).toHaveText(/^Strain relief \w+ \(P-\d+\)$/);
  await expect(candidates.getByRole("listitem").last()).toHaveText("and 3 more");
  await expect(several.getByTestId("answer")).toHaveCount(0);

  await ask(page, "What is on hand for Strain relief K77?");
  const one = page.getByTestId("exchange").last();
  await expect(one.getByTestId("answer")).toContainText("P-10037", { timeout: 20_000 });
  await expect(one.getByTestId("examined")).toContainText(
    "part description 'Strain relief K77' matched Strain relief K77",
  );
  await expect(one.getByTestId("examined")).not.toContainText("(by");
  await expect(one.getByTestId("reading-candidates")).toHaveCount(0);
});
