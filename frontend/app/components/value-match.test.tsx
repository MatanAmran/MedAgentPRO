import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { reading } from "@/lib/fixtures";
import { ReadingCard } from "./ReadingCard";

// A name the user wrote (chat 20.10, 81_VALUE_MATCH) [demo extension]: the server sends the records a name
// matched as text for the screen (V5). The screen shows them as a plain list, with no link and no action.

afterEach(cleanup);

describe("a name that matched several records", () => {
  it("lists the candidates and how many more, as text only", () => {
    render(
      <ReadingCard
        reading={reading({
          label: "named", result: "name_ambiguous", rows: [], row_count: 0, total: 0,
          notice: "'Apex' matches several supplier names. Ask again with the full name.",
          candidates: [{ term: "Apex", column: "supplier name", values: ["Apex Polymers (S-001)", "Apex Tubing (S-020)"], more: 3 }],
        })}
      />,
    );
    expect(screen.getByTestId("reading-notice").textContent).toContain("Ask again with the full name.");
    const list = screen.getByTestId("reading-candidates");
    expect(list.textContent).toContain("supplier name matching “Apex”:");
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Apex Polymers (S-001)", "Apex Tubing (S-020)", "and 3 more",
    ]);
    expect(within(list).queryAllByRole("link")).toEqual([]);
    expect(within(list).queryAllByRole("button")).toEqual([]);
  });

  it("shows no list for a name that matched nothing", () => {
    render(
      <ReadingCard
        reading={reading({ label: "named", result: "name_not_matched", rows: [], row_count: 0, total: 0,
          notice: "No supplier name matches 'Zephyr'." })}
      />,
    );
    expect(screen.getByTestId("reading-notice").textContent).toContain("No supplier name matches 'Zephyr'.");
    expect(screen.queryByTestId("reading-candidates")).toBeNull();
  });
});
