import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { item, reading } from "@/lib/fixtures";
import type { ExchangeResponse } from "@/lib/types";
import { ExchangeView } from "./ExchangeView";

// Task 4, the coverage check (91_COVERAGE_PLAN; chat 20.10, 22:30 to 22:50) [proposal, not in the book]. The
// button shows the person's own words; the model's standalone question is sent by the server and never shown
// here. A referred part's records are a click away and say they establish no cause.

afterEach(cleanup);

const FOLLOW_UP = "What is the value of the open orders of the suppliers that were late in September?";
const SHOW_RECORDED = "These are the records retrieved for this part. They do not establish why it happened; an investigation does.";

function view(response: Partial<ExchangeResponse>, props: Partial<Parameters<typeof ExchangeView>[0]> = {}) {
  const r: ExchangeResponse = { outcome: "answered", readings: [], gaps: [], ...response };
  render(<ExchangeView item={item(r)} onRate={() => undefined} rating={{ busy: false, error: null }} {...props} />);
}

describe("a part not answered", () => {
  it("is said, and asking it on its own sends its place, never the model's question", () => {
    const onAskPart = vi.fn();
    view(
      {
        unanswered_parts: [
          {
            asked: "value of their open orders",
            text: "Your question also asked “value of their open orders”; that part was not covered by the records retrieved for this answer.",
            ask: 'Ask: "value of their open orders"',
            follow_up: FOLLOW_UP,
          },
        ],
      },
      { onAskPart },
    );
    const part = screen.getByTestId("unanswered-part");
    expect(part.textContent).toContain("that part was not covered by the records retrieved for this answer.");
    expect(document.body.textContent).not.toContain(FOLLOW_UP);
    fireEvent.click(screen.getByRole("button", { name: 'Ask: "value of their open orders"' }));
    expect(onAskPart).toHaveBeenCalledWith(0, "value of their open orders");
  });

  it("offers no button while a question runs", () => {
    view({ unanswered_parts: [{ asked: "x", text: "Part of your question was not answered from the records: \"x\".", ask: 'Ask: "x"', follow_up: "x" }] });
    expect(screen.queryByRole("button", { name: 'Ask: "x"' })).toBeNull();
  });
});

describe("a part referred to an investigation", () => {
  it("shows what was recorded on a click, with no answer", async () => {
    const onShowAll = vi.fn().mockResolvedValue(reading({ label: "recorded_reasons", notice: SHOW_RECORDED }));
    view(
      {
        outcome: "referred_to_investigation",
        offer_investigation: true,
        notice: "This asks for a cause, which an investigation determines.",
        referred_parts: [
          {
            asked: "What caused the delays at S-038?",
            asks: "cause",
            text: 'The part "What caused the delays at S-038?" asks for a cause, which an investigation determines.',
            show_recorded: ["recorded_reasons"],
          },
        ],
      },
      { onShowAll },
    );
    expect(screen.getByTestId("referred-part").textContent).toContain("asks for a cause");
    fireEvent.click(screen.getByRole("button", { name: "Show what was recorded" }));
    await waitFor(() => expect(screen.getByTestId("reading-notice").textContent).toContain(SHOW_RECORDED));
    expect(onShowAll).toHaveBeenCalledWith("recorded_reasons");
  });
});

describe("the notes", () => {
  it("say a statement was replaced, and a check that could not be completed", () => {
    view({
      readings: [reading()],
      replaced: "A statement that presented a recorded value as the cause of what happened was replaced by the value as recorded.",
      coverage_notice: "The check that every part of the question was answered could not be completed.",
    });
    expect(screen.getByTestId("replaced").textContent).toContain("replaced by the value as recorded");
    expect(screen.getByTestId("coverage-notice").textContent).toContain("could not be completed");
  });
});
