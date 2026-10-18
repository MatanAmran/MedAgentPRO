import { expect, type Page } from "@playwright/test";

// UI refresh (18.10): the demo user is a listbox, not a select. Chosen as a person would: open it, click.
export async function pickUser(page: Page, user: string) {
  await page.getByTestId("user-picker").click();
  await page.getByRole("listbox", { name: "Demo user" }).getByRole("option", { name: new RegExp(`^${user}`) }).click();
  await expect(page.getByTestId("user-picker")).toContainText(user);
}

export const NEW_CONVERSATION = { name: "New conversation", exact: true } as const;
