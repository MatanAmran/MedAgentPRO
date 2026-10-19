import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { reading } from "@/lib/fixtures";
import { FILTER_TAGS_OVER, FOLD_REFS, foldAnswer, isTagReading, readingCount } from "@/lib/present";
import type { AnswerSegment, ConversationSummary, Reading } from "@/lib/types";
import { Sidebar } from "./Chrome";
import { ConversationMenu } from "./ConversationMenu";
import { ReadingCard } from "./ReadingCard";

// Long list (chat 18.10, 20:30; docs/design/ui-refresh/LongList.dc.html): the screen only, plus the
// catalog's entity name (L4).

afterEach(cleanup);

const ids = (n: number) => Array.from({ length: n }, (_, i) => `P-${10100 + i}`);

/** "Parts below the reorder point include P-10100, P-10101 … and P-10(n-1). Others are …": one sentence
 * per `per` references, each reference to its own row. */
function listAnswer(n: number, per: number): AnswerSegment[] {
  const out: AnswerSegment[] = [];
  ids(n).forEach((id, i) => {
    if (i % per === 0) out.push({ text: i === 0 ? "Parts include " : " Others are " });
    out.push({ ref: { alias: "part_id", row: i + 1 }, text: id });
    out.push({ text: (i + 1) % per === 0 || i === n - 1 ? "." : ", " });
  });
  return out;
}

function partsReading(n: number, segments: AnswerSegment[] = listAnswer(n, 4), over: Partial<Reading> = {}): Reading {
  return reading({
    label: "below_reorder", title: "Stock on hand is below the reorder point",
    answer: segments.map((s) => s.text).join(""), answer_segments: segments,
    columns: ["part_id"], headers: { part_id: "part" }, identifiers: ["part_id"],
    rows: ids(n).map((part_id) => ({ part_id })), row_count: n, total: n, ...over,
  });
}

const join = (s: AnswerSegment[]) => s.map((x) => x.text).join("");

describe("folding a long answer", () => {
  it("does not fold at FOLD_REFS references or fewer", () => {
    expect(foldAnswer(listAnswer(FOLD_REFS, 4))).toBeNull();
  });

  it("folds after the last sentence that holds no more than FOLD_REFS references", () => {
    const folded = foldAnswer(listAnswer(30, 4))!;
    expect(folded.filter((s) => s.ref)).toHaveLength(8);
    expect(join(folded).endsWith("P-10107.")).toBe(true);
    expect(join(listAnswer(30, 4)).startsWith(join(folded))).toBe(true);   // a prefix: nothing changed
  });

  it("cuts after the FOLD_REFS-th reference where the first sentence alone holds more", () => {
    const folded = foldAnswer(listAnswer(30, 30))!;
    expect(folded.filter((s) => s.ref)).toHaveLength(FOLD_REFS);
    expect(folded[folded.length - 1].ref).toBeTruthy();
  });

  it("cuts a sentence end inside a text segment, never inside a reference", () => {
    const segs: AnswerSegment[] = [
      ...listAnswer(5, 5).slice(0, -1), { text: ". Then more. And " }, ...listAnswer(9, 9).slice(1),
    ];
    const folded = foldAnswer(segs)!;
    expect(join(folded).endsWith("Then more.")).toBe(true);
  });
});

describe("references in the answer", () => {
  it("keeps the punctuation after a value on the value's line, the answer's text unchanged", () => {
    const r = partsReading(3, listAnswer(3, 4));
    render(<ReadingCard reading={r} />);
    const p = screen.getByTestId("answer");
    expect(p.textContent).toBe(r.answer);
    const first = screen.getByRole("button", { name: "P-10100" });
    expect(first.parentElement?.className).toContain("whitespace-nowrap");
    expect(first.parentElement?.textContent).toBe("P-10100,");
  });

  it("shows a value as a plain value, red only while pointed at", () => {
    render(<ReadingCard reading={partsReading(3, listAnswer(3, 4))} />);
    const ref = screen.getByRole("button", { name: "P-10101" });
    expect(ref.className).not.toContain("ref-hot");
    fireEvent.mouseEnter(ref);
    expect(ref.className).toContain("ref-hot");
  });

  it("folds a long answer, and the whole checked text is one click away", () => {
    const r = partsReading(30);
    render(<ReadingCard reading={r} />);
    const fold = screen.getByTestId("answer-fold");
    expect(fold.textContent).toBe("Read the full answer (30 references) ↓");
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByTestId("answer").querySelectorAll("button.ref")).toHaveLength(8);
    fireEvent.click(fold);
    expect(fold.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("answer").textContent).toBe(r.answer);
    expect(screen.getByTestId("answer").querySelectorAll("button.ref")).toHaveLength(30);
  });

  it("unfolds when an arrow reaches a folded value, and focuses it", () => {
    render(<ReadingCard reading={partsReading(30)} />);
    const refs = () => [...screen.getByTestId("answer").querySelectorAll<HTMLButtonElement>("button.ref")];
    refs()[7].focus();
    fireEvent.keyDown(refs()[7], { key: "ArrowRight" });
    expect(screen.getByTestId("answer-fold").getAttribute("aria-expanded")).toBe("true");
    expect(document.activeElement?.textContent).toBe("P-10108");
    fireEvent.keyDown(document.activeElement!, { key: "End" });
    expect(document.activeElement?.textContent).toBe("P-10129");
  });

  it("keeps one tab stop for the values of a folded answer", () => {
    render(<ReadingCard reading={partsReading(30)} />);
    const refs = [...screen.getByTestId("answer").querySelectorAll<HTMLButtonElement>("button.ref")];
    expect(refs.filter((b) => b.tabIndex === 0)).toHaveLength(1);
  });
});

describe("a reading of identifiers as tags", () => {
  it("lays out a single identifier column as tags, and any other reading as a table", () => {
    expect(isTagReading(partsReading(3))).toBe(true);
    expect(isTagReading(partsReading(3, undefined, { identifiers: [] }))).toBe(false);
    expect(isTagReading(partsReading(3, undefined, { columns: ["part_id", "on_hand"] }))).toBe(false);
    render(<ReadingCard reading={partsReading(30)} />);
    expect(screen.getByTestId("reading-tags")).toBeTruthy();
    expect(screen.queryByTestId("reading-table")).toBeNull();
    expect(screen.getAllByRole("listitem")).toHaveLength(30);   // tags do not fold
  });

  it("offers a filter only past FILTER_TAGS_OVER tags, and filters on the screen", () => {
    render(<ReadingCard reading={partsReading(FILTER_TAGS_OVER)} />);
    expect(screen.queryByTestId("tag-filter")).toBeNull();
    cleanup();
    render(<ReadingCard reading={partsReading(30)} />);
    fireEvent.change(screen.getByRole("searchbox", { name: "Filter these rows" }), { target: { value: "p-1011" } });
    expect(screen.getAllByRole("listitem").map((t) => t.textContent)).toEqual(ids(30).filter((i) => i.startsWith("P-1011")));
    expect(screen.getByRole("status").textContent).toBe("10 of 30 shown");
  });

  it("links a tag and the values from its row, both ways; a tag is not a tab stop", () => {
    render(<ReadingCard reading={partsReading(30)} />);
    const ref = screen.getByRole("button", { name: "P-10102" });
    fireEvent.focus(ref);
    const tag = document.getElementById(ref.getAttribute("aria-describedby")!)!;
    expect(tag.textContent).toBe("P-10102");
    expect(tag.getAttribute("data-hot")).toBe("true");
    fireEvent.blur(ref);
    fireEvent.mouseEnter(tag);
    expect(ref.className).toContain("ref-hot");
    fireEvent.mouseLeave(tag);
    expect(ref.className).not.toContain("ref-hot");
    expect(screen.getAllByRole("listitem").every((t) => t.tabIndex < 0)).toBe(true);
  });
});

describe("the header count", () => {
  it("names the entity where the server sent it, and how many are shown where the store holds more", () => {
    expect(readingCount(partsReading(30, undefined, { total: 31, entity: "parts" }))).toBe("31 parts · 30 shown");
    expect(readingCount(partsReading(30, undefined, { entity: "parts" }))).toBe("30 parts");
    expect(readingCount(partsReading(30, undefined, { total: 31 }))).toBe("31 rows · 30 shown");
    expect(readingCount(partsReading(1, undefined, { entity: "parts" }))).toBe("1 row");
    expect(readingCount(reading({ row_count: 0, total: 0 }))).toBe("0 rows");
  });
});

describe("archived conversations (L5)", () => {
  const conv = (id: string, over: Partial<ConversationSummary> = {}): ConversationSummary => ({
    conversation_id: id, created_at: "2026-10-18T10:00:00", title: `Question ${id}`, exchanges: 1, ...over });
  const tabStops = (root: Element) =>
    [...root.querySelectorAll<HTMLElement>("button, input, [tabindex]")].filter((e) => e.tabIndex >= 0);

  it("lists the archived ones apart only while Show archived is pressed, in the same single tab stop", () => {
    const toggle = vi.fn();
    const props = { conversations: [conv("A")], current: "A", onSelect: () => {}, onNew: () => {}, locked: false,
                    onShowArchived: toggle };
    const { rerender } = render(<Sidebar {...props} archived={null} />);
    const button = screen.getByRole("button", { name: "Show archived" });
    expect(button.getAttribute("aria-pressed")).toBe("false");
    expect(screen.queryByRole("region", { name: "Archived" })).toBeNull();
    fireEvent.click(button);
    expect(toggle).toHaveBeenCalledWith(true);
    rerender(<Sidebar {...props} archived={[conv("Z", { archived: true })]} />);
    expect(button.getAttribute("aria-pressed")).toBe("true");
    const group = screen.getByRole("region", { name: "Archived" });
    expect(within(group).getByText("Question Z")).toBeTruthy();
    expect(tabStops(screen.getByTestId("conversations")).map((e) => e.getAttribute("data-conv-id"))).toEqual(["A"]);
    fireEvent.keyDown(screen.getByRole("button", { name: /Question A/ }), { key: "ArrowDown" });
    expect(document.activeElement?.getAttribute("data-conv-id")).toBe("Z");
    fireEvent.click(button);
    expect(toggle).toHaveBeenLastCalledWith(false);
  });

  it("says when nothing is archived", () => {
    render(<Sidebar conversations={[]} current={null} onSelect={() => {}} onNew={() => {}} locked={false}
                    archived={[]} onShowArchived={() => {}} />);
    expect(screen.getByText("No archived conversation.")).toBeTruthy();
  });

  it("offers Unarchive, not Archive, on an archived conversation", () => {
    const unarchive = vi.fn();
    const archive = vi.fn();
    render(<ConversationMenu conversation={conv("Z", { archived: true })} onRename={() => {}} onPin={() => {}}
                             onArchive={archive} onUnarchive={unarchive} disabled={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Options for Question Z" }));
    expect(screen.getAllByRole("menuitem").map((i) => i.textContent)).toEqual(["Rename", "Pin to top", "Unarchive"]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Unarchive" }));
    expect(unarchive).toHaveBeenCalledOnce();
    expect(archive).not.toHaveBeenCalled();
  });
});

describe("fix round, screen (U-1, U-2)", () => {
  it("drops the server's Showing notice only where the header count says it (U-1)", () => {
    const cut = partsReading(30, undefined, { total: 31, notice: "Showing 30 of 31." });
    render(<ReadingCard reading={cut} />);
    expect(screen.getByTestId("reading-count").textContent).toBe("31 rows · 30 shown");
    expect(screen.queryByTestId("reading-notice")).toBeNull();
    cleanup();
    render(<ReadingCard reading={partsReading(3, undefined, { notice: "No records matched." })} />);
    expect(screen.getByTestId("reading-notice").textContent).toBe("No records matched.");
  });

  it("marks an archived conversation by an icon and in words, not by colour alone (U-2)", () => {
    render(<Sidebar conversations={[]} current={null} onSelect={() => {}} onNew={() => {}} locked={false}
                    archived={[{ conversation_id: "Z", created_at: "2026-10-18T10:00:00", title: "Old", exchanges: 1,
                                 archived: true }]} onShowArchived={() => {}} />);
    const group = screen.getByRole("region", { name: "Archived" });
    expect(within(group).getByTestId("archived-icon")).toBeTruthy();
    // jsdom drops the space before the sr-only text in the computed name; a browser keeps it
    expect(within(group).getByRole("button", { name: /Old\s*\(archived\)/ })).toBeTruthy();
  });
});

describe("Show all records (Phase B task 5)", () => {
  const rows = (n: number) => Array.from({ length: n }, (_, i) => ({ po_id: `PO-${80000 + i}` }));
  const cut = reading({ columns: ["po_id"], rows: rows(3), row_count: 3, total: 5, notice: "Showing 3 of 5." });

  it("offers every record of a cut reading, fetched from the server, and shows them", async () => {
    const onShowAll = vi.fn(async () => reading({ columns: ["po_id"], rows: rows(5), row_count: 5, total: 5 }));
    render(<ReadingCard reading={cut} onShowAll={onShowAll} />);
    expect(screen.getAllByRole("row")).toHaveLength(4); // the header and 3 rows
    fireEvent.click(screen.getByTestId("show-all-records"));
    expect(await screen.findByText("PO-80004")).toBeTruthy();
    expect(onShowAll).toHaveBeenCalledTimes(1);
    expect(screen.getAllByRole("row")).toHaveLength(6);
    expect(screen.queryByTestId("show-all-records")).toBeNull();
    expect(screen.getByTestId("reading-count").textContent).toBe("5 rows");
  });

  it("is not offered where nothing was cut, or where there is no server to ask", () => {
    render(<ReadingCard reading={reading({ columns: ["po_id"], rows: rows(3), row_count: 3, total: 3 })} onShowAll={vi.fn()} />);
    expect(screen.queryByTestId("show-all-records")).toBeNull();
    cleanup();
    render(<ReadingCard reading={cut} />);
    expect(screen.queryByTestId("show-all-records")).toBeNull();
  });

  it("says so when the records could not be fetched, and keeps what was shown", async () => {
    render(<ReadingCard reading={cut} onShowAll={vi.fn(async () => Promise.reject(new Error("503")))} />);
    fireEvent.click(screen.getByTestId("show-all-records"));
    expect((await screen.findByRole("alert")).textContent).toBe("The records could not be fetched.");
    expect(screen.getAllByRole("row")).toHaveLength(4);
  });
});
