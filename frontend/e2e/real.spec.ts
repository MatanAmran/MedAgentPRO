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
