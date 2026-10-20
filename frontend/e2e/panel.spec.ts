import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { NEW_CONVERSATION } from "./helpers";

// Task 10: "How this was answered", with the server run with --panel. In both modes: the scripted server
// (playwright.config.ts) and the real-model path with the fake provider (playwright.real.config.ts).
// Closed until asked for, opened and closed from the keyboard, and no axe violation when open (Test 40).

async function ask(page: Page, question: string) {
  await page.getByLabel("Question").fill(question);
  await page.getByLabel("Question").press("Enter");
}

async function answerTo(page: Page, question: string) {
  await page.getByRole("button", NEW_CONVERSATION).click();
  await ask(page, question);
  const answer = page.getByTestId("exchange").last();
  await expect(answer).toHaveAttribute("data-outcome", /answered|referred_to_investigation/, { timeout: 20_000 });
  return answer;
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await expect(page.getByLabel("Question")).toBeVisible();
});

test("closed until asked for; the keyboard opens and closes it; the calls are listed", async ({ page }) => {
  const real = (await page.getByTestId("mode").textContent()) === "Live model";
  const answer = await answerTo(page, "Which suppliers were late in weeks 1 to 13?");
  const button = answer.getByRole("button", { name: /How this was answered/ });
  await expect(button).toHaveAttribute("aria-expanded", "false");
  await expect(answer.getByTestId("how-answered-panel")).toHaveCount(0);

  await button.focus();
  await page.keyboard.press("Enter");
  await expect(button).toHaveAttribute("aria-expanded", "true");
  const rows = answer.getByTestId("how-answered-row");
  // task 4: the classification runs beside retrieval and synthesis, recorded when joined, after the gate
  await expect(rows).toHaveCount(4);
  await expect(rows.nth(0)).toContainText("Planning");
  await expect(rows.nth(2)).toContainText("Checking");
  await expect(rows.nth(3)).toContainText("Classifying");
  if (real) {
    await expect(rows.nth(0)).toContainText("claude-sonnet-5-5");
    await expect(answer.getByTestId("how-answered-total")).toContainText(/\$0\.0\d{3}/);
  } else {
    await expect(rows.nth(0)).toContainText("scripted");
    await expect(answer.getByTestId("how-answered-total")).toContainText("$0.0000");
  }

  const a11y = await new AxeBuilder({ page }).include('[data-testid="how-answered"]').analyze();
  expect(a11y.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);

  await page.keyboard.press("Space");
  await expect(button).toHaveAttribute("aria-expanded", "false");
});

test("a referral shows the one planning call and why nothing was drafted", async ({ page }) => {
  const answer = await answerTo(page, "Why is S-014 late so often?");
  await answer.getByRole("button", { name: /How this was answered/ }).click();
  await expect(answer.getByTestId("how-answered-row")).toHaveCount(1);
  await expect(answer.getByTestId("how-answered-note")).toHaveText(
    "No answer was drafted: the plan alone decided this.",
  );
});
