import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, type Locator, type Page, test } from "@playwright/test";

// The whole demo set, as the presenter runs it (task 8), for every role of the demo (Test 38, NFR 22;
// task 9, B5): each question from its example button, a follow-up under the answer it follows. What
// each role must see comes from that role's golden response (backend/tests/golden), one source for the
// server's tests and the screen's. Contrast is checked on every state (Test 40, WCAG 1.4.3), the set is
// walked from the keyboard alone (Test 40), and each kind of state is captured in grayscale for review.

type Example = { question: string; follows: string | null; outcome: string; shows: string[] };
type Golden = {
  outcome: string;
  readings: { title: string }[];
  gaps: unknown[];
  withheld?: string;
  understood_as?: string;
  notice?: string;
  offer_investigation?: boolean;
  request_for_input?: unknown;
};

const ROLES = [
  { user: "AP-01", file: "AP-01" },
  { user: "PL-02", file: "PL-02" },
] as const;
const SNAPSHOTS = join(__dirname, "..", "..", "backend", "tests", "golden", "snapshots");

/** The golden file's name for a question, as backend/tests/golden/test_golden.py builds it. */
function slug(q: string): string {
  return q
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 50);
}

function golden(question: string, role: string): Golden {
  return JSON.parse(readFileSync(join(SNAPSHOTS, `response__${slug(question)}__${role}.json`), "utf-8"));
}

async function examples(page: Page): Promise<Example[]> {
  return (await page.request.get("http://127.0.0.1:8000/api/examples")).json();
}

/** What the screen shows for an exchange, against the golden response of the same role. */
async function matchesGolden(last: Locator, g: Golden) {
  await expect(last).toHaveAttribute("data-outcome", g.outcome);
  await expect(last.getByTestId("reading")).toHaveCount(g.readings.length);
  for (const r of g.readings) await expect(last.getByRole("heading", { name: r.title, exact: true })).toBeVisible();
  await expect(last.getByTestId("gap")).toHaveCount(g.gaps.length);
  await expect(last.getByTestId("withheld")).toHaveCount(g.withheld ? 1 : 0);
  await expect(last.getByTestId("understood-as")).toHaveCount(g.understood_as ? 1 : 0);
  await expect(last.getByTestId("request-for-input")).toHaveCount(g.request_for_input ? 1 : 0);
  await expect(last.getByTestId("referral")).toHaveCount(g.offer_investigation ? 1 : 0);
  if (g.notice) await expect(last).toContainText(g.notice);
  const ratable = g.outcome === "answered" && g.readings.length > 0;
  await expect(last.getByTestId("rating")).toHaveCount(ratable ? 1 : 0);
}

/** WCAG 1.4.3: every text against its background, on the page as it stands (Test 40). */
async function contrastHolds(page: Page, where: string) {
  const r = await new AxeBuilder({ page }).withRules(["color-contrast"]).analyze();
  const found = r.violations.flatMap((v) => v.nodes.map((n) => `${n.target.join(" ")}: ${n.failureSummary}`));
  expect(found, `contrast at ${where}`).toEqual([]);
}

// What each behaviour of the approver's set shows (task 8): kept on top of the golden match
const SHOWN: Record<string, (last: Locator) => Promise<void>> = {
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

for (const role of ROLES) {
  test(`the demo set as ${role.user}: every question ends as that role's golden response, contrast holds`, async ({
    page,
  }) => {
    test.setTimeout(240_000);
    await page.goto("/");
    await page.getByLabel("Demo user").selectOption(role.user);
    await contrastHolds(page, "the empty screen");
    let asked = 0;
    for (const e of await examples(page)) {
      if (!e.follows) {
        await page.getByRole("button", { name: "+ New conversation" }).click();
        asked = 0;
      }
      await page.getByTestId("examples").getByRole("button", { name: e.question, exact: true }).click();
      asked += 1;
      await expect(page.getByTestId("exchange")).toHaveCount(asked, { timeout: 20_000 });
      const last = page.getByTestId("exchange").last();
      await matchesGolden(last, golden(e.question, role.file));
      if (role.user === "AP-01") for (const s of e.shows) await SHOWN[s](last);
      await contrastHolds(page, `${role.user}: ${e.question}`);
    }
  });
}

/** Tab until the locator holds the focus; the keyboard alone, as Test 40 asks. */
async function tabTo(page: Page, target: Locator, what: string) {
  for (let i = 0; i < 120; i++) {
    if (await target.evaluate((el) => el === document.activeElement)) return;
    await page.keyboard.press("Tab");
  }
  throw new Error(`the keyboard does not reach ${what}`);
}

test("the demo set from the keyboard alone (Test 40)", async ({ page }) => {
  test.setTimeout(240_000);
  await page.goto("/");
  await expect(page.getByTestId("examples")).toBeVisible();
  let asked = 0;
  for (const e of await examples(page)) {
    if (!e.follows) {
      await tabTo(page, page.getByRole("button", { name: "+ New conversation" }), "+ New conversation");
      await page.keyboard.press("Enter");
      asked = 0;
    }
    const button = page.getByTestId("examples").getByRole("button", { name: e.question, exact: true });
    await expect(button).toBeVisible();
    await tabTo(page, button, e.question);
    await page.keyboard.press("Enter");
    asked += 1;
    await expect(page.getByTestId("exchange")).toHaveCount(asked, { timeout: 20_000 });
    await expect(page.getByTestId("exchange").last()).toHaveAttribute("data-outcome", e.outcome);
    if (e.outcome === "answered" && e.shows.includes("two_readings")) {
      const up = page.getByTestId("exchange").last().getByLabel("Rate up");
      await tabTo(page, up, "Rate up");
      await page.keyboard.press("Enter");
      await expect(up).toHaveAttribute("aria-pressed", "true");
    }
  }
  // the question box and the demo user, from the keyboard too
  await tabTo(page, page.getByLabel("Question"), "the question box");
  await page.keyboard.type("Why is S-014 late so often?");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("exchange").last()).toHaveAttribute("data-outcome", "referred_to_investigation", {
    timeout: 20_000,
  });
  await tabTo(page, page.getByLabel("Demo user"), "the demo user");
  await page.keyboard.type("PL-02");
  await expect(page.getByText("Planner", { exact: true })).toBeVisible();
});

// Grayscale (Test 40, task 9 B3a): one capture of each kind of state, for Evyatar to read by eye. One
// engine is enough for the record; captured where the run is local or on CI's Chromium
const STATES = [
  ["01-empty", null],
  ["02-two-readings", "Which suppliers were late in weeks 1 to 13?"],
  ["03-grouped", "Which purchase orders were delivered late in September?"],
  ["04-gap", "Which open lines are past due, and who approved them?"],
  ["05-referral", "Why is S-014 late so often?"],
  ["06-not-held", "What did S-099 supply?"],
  ["07-withheld", "Which parts are below their reorder point?"],
  ["08-request-for-input", "Which supplier delivered the most lines on time in weeks 1 to 13?"],
  ["09-action-declined", "Update the promised date of PO-87213 to 20 October."],
] as const;

test("each kind of state, captured in grayscale for review", async ({ page }, info) => {
  test.skip(!["edge", "chromium"].includes(info.project.name), "one engine is enough for the captures");
  test.setTimeout(240_000);
  const dir = join(info.project.outputDir, "grayscale");
  mkdirSync(dir, { recursive: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");
  await page.addStyleTag({ content: "html { filter: grayscale(1); }" });
  for (const [name, question] of STATES) {
    if (question) {
      await page.getByRole("button", { name: "+ New conversation" }).click();
      await page.getByTestId("examples").getByRole("button", { name: question, exact: true }).click();
      await expect(page.getByTestId("exchange")).toHaveCount(1, { timeout: 20_000 });
    }
    await page.screenshot({ path: join(dir, `${name}.png`) });
  }
});
