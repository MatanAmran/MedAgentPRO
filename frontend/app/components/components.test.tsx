import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { goldenResponses, item, reading } from "@/lib/fixtures";
import type { ExchangeResponse } from "@/lib/types";
import { Composer } from "./Composer";
import { ExchangeView } from "./ExchangeView";
import { ReadingCard } from "./ReadingCard";

afterEach(cleanup);

const NO_RATING = { busy: false, error: null };
const GOLDEN = goldenResponses();

function show(response: ExchangeResponse, over = {}) {
  return render(<ExchangeView item={item(response, over)} onRate={() => {}} rating={NO_RATING} />);
}

describe("every golden response renders", () => {
  it.each(Object.keys(GOLDEN))("%s: Examined on every reading, thumbs only under an answer", (name) => {
    const r = GOLDEN[name];
    show(r);
    expect(screen.queryAllByTestId("examined")).toHaveLength(r.readings.length);
    screen.queryAllByTestId("examined").forEach((e, i) => expect(e.textContent).toBe(r.readings[i].checked));
    expect(screen.queryAllByTestId("rating")).toHaveLength(r.outcome === "answered" && r.readings.length > 0 ? 1 : 0);
    r.readings.forEach((x) => expect(screen.getByText(x.title)).toBeTruthy());
  });
});

describe("the exchange", () => {
  const late = GOLDEN["which_suppliers_were_late_in_weeks_1_to_13__AP-01"];

  it("names the ambiguous term over the two readings (Q4)", () => {
    show(late);
    expect(screen.getByText("“late” can be read in 2 ways, so each is shown.")).toBeTruthy();
  });

  it("shows Understood as only where the response carries it (K6)", () => {
    show(late);
    expect(screen.queryByTestId("understood-as")).toBeNull();
    cleanup();
    show({ ...late, understood_as: "Which suppliers were late in weeks 1 to 13?" });
    expect(screen.getByTestId("understood-as").textContent).toContain("Which suppliers were late in weeks 1 to 13?");
  });

  it("shows the withheld notice", () => {
    show({ ...late, withheld: "Some statements were withheld because the records do not support them." });
    expect(screen.getByTestId("withheld").textContent).toMatch(/withheld/);
  });

  it("no thumbs under a declined action, which holds no records (task 8 review)", () => {
    show({ outcome: "answered", readings: [], gaps: [], notice: "This assistant reads records. It does not change them." });
    expect(screen.getByTestId("notice").textContent).toBe("This assistant reads records. It does not change them.");
    expect(screen.queryByTestId("rating")).toBeNull();
  });

  it("no thumbs under an answer that was not recorded", () => {
    show(late, { recorded: false });
    expect(screen.queryByTestId("rating")).toBeNull();
  });

  it("marks the rating given and reports the one clicked", () => {
    const onRate = vi.fn();
    render(<ExchangeView item={item(late, { rating: "down" })} onRate={onRate} rating={NO_RATING} />);
    expect(screen.getByLabelText("Rate down").getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByLabelText("Rate up"));
    expect(onRate).toHaveBeenCalledWith("up");
  });

  it("offers the investigation, disabled in the demo (Q1)", () => {
    show(GOLDEN["why_is_s_014_late_so_often__AP-01"]);
    const box = screen.getByTestId("referral");
    const button = within(box).getByRole("button", { name: "Open an investigation" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(within(box).getByText("Investigations are not part of this demo")).toBeTruthy();
  });

  it("shows the cost on the real model only (T3)", () => {
    show(late);
    expect(screen.queryByTestId("cost")).toBeNull();
    cleanup();
    show(late, { cost: { exchange_usd: 0.0412, remaining_usd: 9.12 } });
    expect(screen.getByTestId("cost").textContent).toBe("This answer cost $0.0412 · $9.12 of the demo budget left");
  });

  it("replaces the answer with a request for input (NFR 19)", () => {
    show({
      outcome: "no_supported_assertion",
      readings: [],
      gaps: [],
      request_for_input: { text: "Which of these did you mean?", readings: [{ label: "a", checked: "Examined: a" }] },
    });
    expect(screen.getByTestId("request-for-input").textContent).toContain("Examined: a");
    expect(screen.queryByTestId("rating")).toBeNull();
  });

  it("shows a failure notice", () => {
    show({ outcome: "source_unreachable", readings: [], gaps: [], notice: "demo.db could not be reached." });
    expect(screen.getByTestId("notice").textContent).toBe("demo.db could not be reached.");
  });
});

describe("a reading (Q3)", () => {
  const rows = [
    { supplier_id: "S-031", po_id: "PO-87710" },
    { supplier_id: "S-044", po_id: "PO-88019" },
    { supplier_id: "S-031", po_id: "PO-87954" },
  ];
  const grouped = reading({
    columns: ["supplier_id", "po_id"],
    identifiers: ["supplier_id", "po_id"],
    rows,
    row_count: 3,
    total: 9,
    notice: "Showing 3 of 9.",
  });

  it("groups, keeps the order, and says it is cut above the groups", () => {
    render(<ReadingCard reading={grouped} />);
    const groups = screen.getAllByTestId("reading-group").map((g) => g.textContent);
    expect(groups).toEqual(["S-031PO-87710PO-87954", "S-044PO-88019"]);
    const card = screen.getByTestId("reading");
    const notice = screen.getByTestId("reading-notice");
    expect(notice.textContent).toContain("Showing 3 of 9.");
    expect(notice.compareDocumentPosition(screen.getByTestId("reading-groups")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(card).toBeTruthy();
  });

  it("shows every column as a table on request", () => {
    render(<ReadingCard reading={grouped} />);
    fireEvent.click(screen.getByRole("button", { name: "Show as table" }));
    const table = screen.getByTestId("reading-table");
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(screen.queryByTestId("reading-groups")).toBeNull();
  });

  it("is a plain table where the rule does not hold", () => {
    render(<ReadingCard reading={{ ...grouped, identifiers: ["supplier_id"] }} />);
    expect(screen.getByTestId("reading-table")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Show as table" })).toBeNull();
  });

  it("an empty reading is a finding, with Examined", () => {
    render(<ReadingCard reading={reading({ notice: "No records matched.", rows: [], columns: ["po_id"] })} />);
    expect(screen.getByText("No records matched.")).toBeTruthy();
    expect(screen.queryByTestId("reading-table")).toBeNull();
    expect(screen.getByTestId("examined")).toBeTruthy();
  });
});

describe("the question box (R8)", () => {
  it("sends once, then is cleared", () => {
    const onSend = vi.fn();
    render(<Composer locked={false} onSend={onSend} />);
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "  Which suppliers?  " } });
    fireEvent.keyDown(screen.getByLabelText("Question"), { key: "Enter" });
    expect(onSend).toHaveBeenCalledExactlyOnceWith("Which suppliers?");
    expect((screen.getByLabelText("Question") as HTMLTextAreaElement).value).toBe("");
  });

  it("sends nothing while locked, by button or by Enter", () => {
    const onSend = vi.fn();
    render(<Composer locked onSend={onSend} />);
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "Which suppliers?" } });
    expect((screen.getByLabelText("Send") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(screen.getByLabelText("Question"), { key: "Enter" });
    fireEvent.click(screen.getByLabelText("Send"));
    expect(onSend).not.toHaveBeenCalled();
  });

  it("sends nothing empty", () => {
    const onSend = vi.fn();
    render(<Composer locked={false} onSend={onSend} />);
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "   " } });
    expect((screen.getByLabelText("Send") as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("readable names (task 7 review, item 2)", () => {
  it("heads each column by its name from the catalog, not the model's alias", () => {
    render(
      <ReadingCard
        reading={reading({
          columns: ["supplier_id", "late_count"],
          headers: { supplier_id: "supplier", late_count: "number of records" },
          rows: [{ supplier_id: "S-008", late_count: 4 }],
          row_count: 1,
        })}
      />,
    );
    const heads = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(heads).toEqual(["supplier", "number of records"]);
  });
});
