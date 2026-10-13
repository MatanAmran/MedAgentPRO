import { expect, type Page, test } from "@playwright/test";

// The demo questions the scripted model knows (backend/demo_data/scripted_exchanges.json)
const LATE = "Which suppliers were late in weeks 1 to 13?";
const WHY = "Why is S-014 late so often?";
const EMPTY = "What did S-040 supply?";

async function ask(page: Page, question: string) {
  await page.getByLabel("Question").fill(question);
  await page.getByLabel("Question").press("Enter");
}

async function answered(page: Page, n: number) {
  await expect(page.getByTestId("exchange")).toHaveCount(n, { timeout: 20_000 });
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("mode")).toHaveText("scripted model");
});

test("a question read two ways: two readings, each with what was examined", async ({ page }) => {
  await ask(page, LATE);
  await answered(page, 1);
  await expect(page.getByText("“late” can be read in 2 ways, so each is shown.")).toBeVisible();
  await expect(page.getByTestId("reading")).toHaveCount(2);
  await expect(page.getByTestId("examined")).toHaveCount(2);
  await expect(page.getByRole("heading", { name: "Delivered after the requested date" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "The supplier committed to a date after the requested date" })).toBeVisible();
  await expect(page.getByTestId("rating")).toHaveCount(1);
  await expect(page.getByTestId("conversations").getByRole("button", { name: LATE }).first()).toBeVisible();
});

test("a question about a cause is referred, the investigation button disabled in the demo", async ({ page }) => {
  await ask(page, WHY);
  await answered(page, 1);
  const referral = page.getByTestId("referral");
  await expect(referral).toContainText("This asks for a cause, which an investigation determines.");
  await expect(referral.getByRole("button", { name: "Open an investigation" })).toBeDisabled();
  await expect(page.getByTestId("rating")).toHaveCount(0);
});

test("an empty result is a finding, shown with what was examined", async ({ page }) => {
  await ask(page, EMPTY);
  await answered(page, 1);
  await expect(page.getByText("No records matched.")).toBeVisible();
  await expect(page.getByTestId("examined")).toHaveCount(1);
});

test("sending is locked while a question runs, and unlocked after", async ({ page }) => {
  await ask(page, LATE);
  await expect(page.getByTestId("stage")).toBeVisible();
  await page.getByLabel("Question").fill("Another question?");
  await expect(page.getByLabel("Send")).toBeDisabled();
  await expect(page.getByLabel("Demo user")).toBeDisabled();
  await expect(page.getByRole("button", { name: "+ New conversation" })).toBeDisabled();
  await answered(page, 1);
  await expect(page.getByLabel("Send")).toBeEnabled();
});

async function reloadMidStream(page: Page): Promise<string> {
  const created = page.waitForResponse((r) => r.url().endsWith("/api/conversations") && r.request().method() === "POST");
  await ask(page, LATE);
  const { conversation_id } = await (await created).json();
  await expect(page.getByTestId("stage")).toBeVisible();
  await page.reload();
  return conversation_id;
}

async function openNewest(page: Page) {
  await page.getByTestId("conversations").getByRole("button").first().click();
}

// T7-B1 (c): the new question waits on the server while the abandoned exchange finishes the call
// under way, shows that it is waiting, and is then answered, never refused
test("B1: reload mid-stream, then at once a new question in the same conversation is taken", async ({ page }) => {
  await reloadMidStream(page);
  await openNewest(page);
  await ask(page, WHY);
  await expect(page.getByTestId("referral")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("still being answered")).toHaveCount(0);
});

test("B1: reload mid-stream; once the abandoned exchange ends, the conversation takes a new question", async ({
  page,
}) => {
  const id = await reloadMidStream(page);
  // the server ends the abandoned exchange itself (S1) and frees the conversation (B1)
  await expect
    .poll(
      async () =>
        (await (await page.request.get(`http://127.0.0.1:8000/api/conversations/${id}`, {
          headers: { "X-Demo-User": "AP-01" },
        })).json()).exchanges.length,
      { timeout: 15_000 },
    )
    .toBe(1);
  await openNewest(page);
  await expect(page.getByTestId("exchange")).toHaveAttribute("data-outcome", "request_not_completed");
  await ask(page, WHY);
  await expect(page.getByTestId("referral")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("still being answered")).toHaveCount(0); // no 409
});

test("a rating is kept after a reload", async ({ page }) => {
  await ask(page, LATE);
  await answered(page, 1);
  await page.getByLabel("Rate up").click();
  await expect(page.getByLabel("Rate up")).toHaveAttribute("aria-pressed", "true");
  await page.reload();
  // the newest conversation is listed first; earlier tests left others with the same question
  await page.getByTestId("conversations").getByRole("button", { name: LATE }).first().click();
  await expect(page.getByLabel("Rate up")).toHaveAttribute("aria-pressed", "true");
});

test("a planner's conversations are their own", async ({ page }) => {
  await ask(page, WHY);
  await answered(page, 1);
  await page.getByLabel("Demo user").selectOption("PL-02");
  await expect(page.getByTestId("conversations").getByRole("button")).toHaveCount(0);
  await expect(page.getByText("demo, no authentication")).toBeVisible();
});

// Task 8: the whole demo set, as the presenter runs it: each question from its example button, a
// follow-up under the answer it follows, each ending as its script expects and showing what it is for
type Example = { question: string; follows: string | null; outcome: string; shows: string[] };

const SHOWN: Record<string, (last: import("@playwright/test").Locator) => Promise<void>> = {
  two_readings: (x) => expect(x.getByTestId("reading")).toHaveCount(2),
  one_reading: (x) => expect(x.getByTestId("reading")).toHaveCount(1),
  understood_as: (x) => expect(x.getByTestId("understood-as")).toBeVisible(),
  grouped: (x) => expect(x.getByTestId("reading-groups")).toBeVisible(),
  gap: (x) => expect(x.getByTestId("gap")).toHaveCount(1),
  recorded_reason: (x) => expect(x.getByTestId("reading")).toContainText("recorded as"),
  referral: (x) => expect(x.getByRole("button", { name: "Open an investigation" })).toBeDisabled(),
  no_match: (x) => expect(x.getByText("No records matched.")).toBeVisible(),
  not_held: (x) => expect(x.getByTestId("reading-notice")).toContainText("does not appear in"),
  withheld: (x) => expect(x.getByTestId("withheld")).toBeVisible(),
  request_for_input: (x) => expect(x.getByTestId("request-for-input")).toBeVisible(),
  action_declined: async (x) => {
    await expect(x.getByTestId("notice")).toHaveText("This assistant reads records. It does not change them.");
    await expect(x.getByTestId("rating")).toHaveCount(0);
  },
};

test("the demo set: every example question ends as its script expects", async ({ page }) => {
  test.setTimeout(180_000);
  const examples: Example[] = await (await page.request.get("http://127.0.0.1:8000/api/examples")).json();
  expect(examples.length).toBeGreaterThanOrEqual(12);
  let asked = 0;
  for (const e of examples) {
    if (!e.follows) {
      await page.getByRole("button", { name: "+ New conversation" }).click();
      asked = 0;
    }
    await page.getByTestId("examples").getByRole("button", { name: e.question, exact: true }).click();
    asked += 1;
    await expect(page.getByTestId("exchange")).toHaveCount(asked, { timeout: 20_000 });
    const last = page.getByTestId("exchange").last();
    await expect(last, e.question).toHaveAttribute("data-outcome", e.outcome);
    for (const s of e.shows) await SHOWN[s](last);
  }
});
