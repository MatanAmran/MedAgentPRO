import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { goldenResponses, item, reading } from "@/lib/fixtures";
import { agentSteps, readingCsv, readingCsvName } from "@/lib/present";
import type { ConversationSummary, Reading, Trace } from "@/lib/types";
import { conversationMeta, groupConversations, Header, Sidebar } from "./Chrome";
import { ReadingCard, FOLD_AFTER } from "./ReadingCard";
import { STAGE_STEP, StageTimeline, stagesToShow } from "./StageTimeline";
import { UserPicker } from "./UserPicker";

// UI refresh, phase A (18.10, docs/design/ui-refresh): the screen only; no API change.

afterEach(cleanup);

const GOLDEN = goldenResponses();
const late = GOLDEN["which_suppliers_were_late_in_weeks_1_to_13__AP-01"];

describe("the user picker", () => {
  function open() {
    const onUser = vi.fn();
    render(<UserPicker user="AP-01" onUser={onUser} disabled={false} />);
    return { onUser, button: screen.getByTestId("user-picker") };
  }

  it("names the user, the role and what the role sees; closed until asked for", () => {
    const { button } = open();
    expect(button.getAttribute("aria-haspopup")).toBe("listbox");
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(button.textContent).toContain("Approver · sees all orders");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("opens from the keyboard, moves with the arrows, and chooses with Enter", () => {
    const { onUser, button } = open();
    fireEvent.keyDown(button, { key: "ArrowDown" });
    const list = screen.getByRole("listbox");
    expect(document.activeElement).toBe(list);
    expect(list.getAttribute("aria-activedescendant")).toContain("AP-01");
    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "ArrowDown" });
    expect(list.getAttribute("aria-activedescendant")).toContain("PL-02");
    fireEvent.keyDown(list, { key: "End" });
    expect(list.getAttribute("aria-activedescendant")).toContain("PL-05");
    fireEvent.keyDown(list, { key: "Home" });
    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "Enter" });
    expect(onUser).toHaveBeenCalledWith("PL-01");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(document.activeElement).toBe(button);
  });

  it("closes on Escape without choosing, and returns focus", () => {
    const { onUser, button } = open();
    fireEvent.click(button);
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    expect(onUser).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(document.activeElement).toBe(button);
  });

  it("groups the approver and the planners, the chosen one marked", () => {
    const { button } = open();
    fireEvent.click(button);
    const options = screen.getAllByRole("option");
    expect(options.map((o) => o.textContent?.match(/(AP|PL)-\d{2}/)?.[0])).toEqual(["AP-01", "PL-01", "PL-02", "PL-03", "PL-04", "PL-05"]);
    expect(options[0].getAttribute("aria-selected")).toBe("true");
    expect(screen.getAllByRole("group").map((g) => g.getAttribute("aria-label"))).toEqual([
      "Approver",
      "Planners, own orders only",
    ]);
  });
});

describe("the header", () => {
  it("shows the mode, and the budget on the live model only", () => {
    render(<Header user="AP-01" onUser={() => {}} mode="real" budget={3.5613} locked={false} />);
    expect(screen.getByTestId("mode").textContent).toBe("Live model");
    expect(screen.getByTestId("budget").textContent).toContain("$3.56");
    expect(screen.getByTestId("demo-strip").textContent).toBe("DEMO · NO AUTHENTICATION · SYNTHETIC DATA");
    cleanup();
    render(<Header user="AP-01" onUser={() => {}} mode="scripted" budget={null} locked={false} />);
    expect(screen.getByTestId("mode").textContent).toBe("Scripted model");
    expect(screen.queryByTestId("budget")).toBeNull();
    expect(screen.queryByText(/Complaints/)).toBeNull();
  });
});

describe("the conversation list", () => {
  const now = new Date("2026-10-18T16:30:00");
  const conv = (id: string, at: string, title: string | null, exchanges = 1): ConversationSummary => ({
    conversation_id: id, created_at: at, title, exchanges,
  });
  const list = [
    conv("CV-1", "2026-10-18T16:19:00", "Which suppliers were late in weeks 1 to 13?", 2),
    conv("CV-2", "2026-10-14T09:00:00", "Which open lines are past due?"),
    conv("CV-3", "2026-10-18T16:24:00", null, 0),
  ];

  it("groups Today and Earlier, and searches by title", () => {
    expect(groupConversations(list, "", now).today.map((c) => c.conversation_id)).toEqual(["CV-1", "CV-3"]);
    expect(groupConversations(list, "", now).earlier.map((c) => c.conversation_id)).toEqual(["CV-2"]);
    expect(groupConversations(list, "PAST due", now).today).toEqual([]);
    expect(groupConversations(list, "PAST due", now).earlier.map((c) => c.conversation_id)).toEqual(["CV-2"]);
    expect(groupConversations(list, "new conv", now).today.map((c) => c.conversation_id)).toEqual(["CV-3"]);
  });

  it("says how many questions and when, from the list alone", () => {
    expect(conversationMeta(list[0], now)).toMatch(/^2 questions · \d{2}:\d{2}$/);
    expect(conversationMeta(list[1], now)).toBe("1 question · 14 Oct");
  });

  it("filters as the search is typed", () => {
    render(<Sidebar conversations={list} current="CV-1" onSelect={() => {}} onNew={() => {}} locked={false} />);
    expect(screen.getAllByTestId("conversation-item")).toHaveLength(3);
    fireEvent.change(screen.getByLabelText("Search conversations"), { target: { value: "suppliers" } });
    expect(screen.getAllByTestId("conversation-item")).toHaveLength(1);
    fireEvent.change(screen.getByLabelText("Search conversations"), { target: { value: "nothing like it" } });
    expect(screen.getByText("No conversation matches.")).toBeTruthy();
  });
});

describe("the stage timeline", () => {
  it("shows the stages seen, then those ahead; a follow-up starts by reading the follow-up", () => {
    expect(stagesToShow([], false)).toEqual(["intent", "retrieval", "synthesis", "gate", "contract"]);
    expect(stagesToShow([], true)[0]).toBe("rewriting");
    expect(stagesToShow(["intent", "retrieval", "synthesis", "regeneration", "gate"], false)).toEqual([
      "intent", "retrieval", "synthesis", "regeneration", "gate", "contract",
    ]);
  });

  it("marks done, active and not started, with fixed text only, and stops on request", () => {
    const onStop = vi.fn();
    render(<StageTimeline seen={["intent", "retrieval"]} followUp={false} onStop={onStop} stopping={false} />);
    const states = [...screen.getByTestId("timeline").querySelectorAll("li")].map((li) => li.getAttribute("data-state"));
    expect(states).toEqual(["done", "active", "todo", "todo", "todo"]);
    expect(screen.getByTestId("stage").textContent).toBe(STAGE_STEP.retrieval.title);
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(onStop).toHaveBeenCalledOnce();
  });

  it("leaves the motion to CSS that prefers-reduced-motion turns off", () => {
    render(<StageTimeline seen={["intent"]} followUp={false} onStop={() => {}} stopping={false} />);
    expect(document.querySelector(".stage-live")).toBeTruthy();
    expect(document.querySelector(".stage-shimmer")).toBeTruthy();
  });
});

describe("a reading card", () => {
  const rows = (n: number) => Array.from({ length: n }, (_, i) => ({ supplier_id: `S-${100 + i}`, n: i }));
  const many: Reading = reading({
    label: "late_by_delivery", title: "Delivered late", answer: "S-100 had the most.", columns: ["supplier_id", "n"],
    headers: { supplier_id: "supplier", n: "number of records" }, identifiers: ["supplier_id"],
    rows: rows(10), row_count: 10, total: 28,
  });

  it("exports the rows shown under their headers; how many of how many is in the name, not the file", () => {
    expect(readingCsvName(many)).toBe("late_by_delivery_10_of_28.csv");
    const csv = readingCsv(many);
    expect(csv.split("\r\n")[0]).toBe("supplier,number of records");
    expect(csv.trim().split("\r\n")).toHaveLength(11);
    expect(csv).not.toMatch(/of 28|Showing/);
    expect(readingCsv(reading({ columns: ["t"], rows: [{ t: 'a "b", c' }], row_count: 1 }))).toBe('t\r\n"a ""b"", c"\r\n');
  });

  it("names the export by its rows, folds after six, and copies the answer as shown", async () => {
    const write = vi.fn(async () => {});
    Object.assign(navigator, { clipboard: { writeText: write } });
    render(<ReadingCard reading={many} index={1} />);
    expect(screen.getByTestId("export-csv").textContent).toBe("Export 10 rows");
    expect(screen.getByTestId("reading-count").textContent).toBe("28 rows");
    expect(screen.getAllByRole("row")).toHaveLength(FOLD_AFTER + 1);
    fireEvent.click(screen.getByRole("button", { name: "Show all 10 rows ↓" }));
    expect(screen.getAllByRole("row")).toHaveLength(11);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /Copy the answer/ })));
    expect(write).toHaveBeenCalledWith("S-100 had the most.");
  });

  it("offers no copy without an answer, and no export without rows", () => {
    render(<ReadingCard reading={reading({ rows: [], row_count: 0 })} />);
    expect(screen.queryByRole("button", { name: /Copy/ })).toBeNull();
    expect(screen.queryByTestId("export-csv")).toBeNull();
  });
});

describe("how the agent worked", () => {
  const gate = (claims: number, status: "accepted" | "rejected" = "accepted") => ({
    stage: "support_gate", prompt_identity: "support_scoring@5", model: "m", attempt_number: 1, feedback_attempt: false,
    status, finish_reason: "stop", input_tokens: 1, output_tokens: 1, reasoning_tokens: null, claims, max_tokens: 3000,
    duration_ms: 4200, check: null, cost_usd: 0.01,
  });

  it("counts readings and rows from the response, and claims only where a trace came", () => {
    const rows = late.readings.reduce((s, r) => s + (r.row_count ?? 0), 0);
    expect(agentSteps(item(late)).steps.map((s) => s.label)).toEqual([
      "Planned 2 readings", `Retrieved ${rows} rows`, "Drafted with references",
    ]);
    expect(agentSteps(item(late)).seconds).toBeNull();
    const trace: Trace = {
      calls: [gate(5, "rejected"), gate(8)], total: { input_tokens: 2, output_tokens: 2, reasoning_tokens: null, usd: 0.02 },
      unpriced_usd: null, not_sent: null, note: null,
    };
    const withTrace = agentSteps(item(late, { trace }));
    expect(withTrace.steps.at(-1)?.label).toBe("Checked 8 claims");
    expect(withTrace.seconds).toBe(8.4);
  });

  it("shows the row under an answer with readings only", () => {
    const view = (r: typeof late) => {
      cleanup();
      return render(<div>{agentSteps(item(r)).steps.map((s) => s.label).join("|")}</div>);
    };
    view(GOLDEN["why_is_s_014_late_so_often__AP-01"]);
    expect(screen.queryByText(/Planned/)).toBeNull();
    const card = within(document.body);
    expect(card.queryByText(/Retrieved/)).toBeNull();
  });
});

// --------------------------------------------------------------------------
// phase B [demo extension]: rename, pin, archive
// --------------------------------------------------------------------------

import { ConversationMenu, RenameField } from "./ConversationMenu";

describe("the conversation menu", () => {
  const c: ConversationSummary = { conversation_id: "CV-1", created_at: "2026-10-18T16:19:00", title: "Late suppliers",
    exchanges: 1, pinned: false };

  function open(over: Partial<ConversationSummary> = {}) {
    const calls = { rename: vi.fn(), pin: vi.fn(), archive: vi.fn() };
    render(<ConversationMenu conversation={{ ...c, ...over }} onRename={calls.rename} onPin={calls.pin}
                             onArchive={calls.archive} disabled={false} />);
    const button = screen.getByRole("button", { name: "Options for Late suppliers" });
    return { calls, button };
  }

  it("opens from the keyboard on its first item, moves with the arrows, and Escape returns focus", () => {
    const { button } = open();
    expect(button.getAttribute("aria-haspopup")).toBe("menu");
    fireEvent.keyDown(button, { key: "ArrowDown" });
    const items = screen.getAllByRole("menuitem");
    expect(items.map((i) => i.textContent)).toEqual(["Rename", "Pin to top", "Archive"]);
    expect(document.activeElement).toBe(items[0]);
    fireEvent.keyDown(screen.getByRole("menu"), { key: "ArrowUp" });
    expect(document.activeElement).toBe(items[2]);
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Home" });
    expect(document.activeElement).toBe(items[0]);
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(button);
  });

  it("says archiving keeps the record, and each item does what it names", () => {
    const { calls, button } = open({ pinned: true });
    fireEvent.click(button);
    expect(screen.getByRole("menu").textContent).toContain("Every exchange stays in the audit record.");
    fireEvent.click(screen.getByRole("menuitem", { name: "Unpin" }));
    expect(calls.pin).toHaveBeenCalledWith(false);
    fireEvent.click(button);
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    expect(calls.archive).toHaveBeenCalledOnce();
    fireEvent.click(button);
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    expect(calls.rename).toHaveBeenCalledOnce();
  });

  it("renames in place: Enter saves the trimmed name, Escape cancels, an empty name is not sent", () => {
    const save = vi.fn();
    const cancel = vi.fn();
    render(<RenameField initial="Late suppliers" onSave={save} onCancel={cancel} />);
    const field = screen.getByLabelText("Conversation name");
    expect(document.activeElement).toBe(field);
    fireEvent.change(field, { target: { value: "   " } });
    fireEvent.submit(field.closest("form")!);
    expect(save).not.toHaveBeenCalled();
    fireEvent.change(field, { target: { value: "  Weeks 1 to 13  " } });
    fireEvent.submit(field.closest("form")!);
    expect(save).toHaveBeenCalledWith("Weeks 1 to 13");
    fireEvent.keyDown(field, { key: "Escape" });
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("lists pinned conversations first, and never an archived one", () => {
    const now = new Date("2026-10-18T17:00:00");
    const g = groupConversations(
      [
        { ...c, conversation_id: "A" },
        { ...c, conversation_id: "B", pinned: true, created_at: "2026-10-01T10:00:00" },
        { ...c, conversation_id: "C", archived: true },
      ],
      "",
      now,
    );
    expect([g.pinned, g.today, g.earlier].map((x) => x.map((y) => y.conversation_id))).toEqual([["B"], ["A"], []]);
  });
});

// --------------------------------------------------------------------------
// phase C [demo extension]: a value in the answer points at its row
// --------------------------------------------------------------------------

import { answerText } from "@/lib/present";

describe("provenance in the answer", () => {
  const late1 = late.readings[0];
  const reasons = GOLDEN["what_delay_reasons_were_recorded_for_s_038_s_late___AP-01"].readings[0];

  it("marks each filled value; pointing at one marks its row", () => {
    render(<ReadingCard reading={late1} index={1} />);
    const refs = screen.getByTestId("answer").querySelectorAll("button.ref");
    expect(refs.length).toBe(late1.answer_segments!.filter((s) => s.ref).length);
    const first = refs[0] as HTMLButtonElement;
    fireEvent.mouseEnter(first);
    expect(first.className).toContain("ref-hot");
    const row = document.getElementById(first.getAttribute("aria-describedby")!)!;
    expect(row.getAttribute("data-row")).toBe(first.getAttribute("data-ref-row"));
    expect(row.getAttribute("data-hot")).toBe("true");
    fireEvent.mouseLeave(first);
    expect(row.getAttribute("data-hot")).toBeNull();
  });

  it("copies the joined segments, which are the answer", async () => {
    expect(answerText(late1)).toBe(late1.answer);
    const write = vi.fn(async () => {});
    Object.assign(navigator, { clipboard: { writeText: write } });
    render(<ReadingCard reading={late1} index={1} />);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /Copy the answer/ })));
    expect(write).toHaveBeenCalledWith(late1.answer);
  });

  it("unfolds the table when a value from a folded row is focused", () => {
    const rows = Array.from({ length: 9 }, (_, i) => ({ supplier_id: `S-${100 + i}`, n: i }));
    const r: Reading = reading({
      label: "r", answer: "S-108 had 8.", columns: ["supplier_id", "n"], identifiers: ["supplier_id"], rows,
      row_count: 9, total: 9,
      answer_segments: [{ ref: { alias: "supplier_id", row: 9 }, text: "S-108" }, { text: " had " },
                        { ref: { alias: "n", row: 9 }, text: "8" }, { text: "." }],
    });
    render(<ReadingCard reading={r} />);
    expect(screen.getAllByRole("row")).toHaveLength(FOLD_AFTER + 1);
    fireEvent.focus(screen.getByRole("button", { name: "S-108" }));
    expect(screen.getAllByRole("row")).toHaveLength(10);
    expect(document.querySelector('[data-row="9"]')?.getAttribute("data-hot")).toBe("true");
  });

  it("never marks a free-text value: a recorded reason stays plain text", () => {
    render(<ReadingCard reading={reasons} index={1} />);
    const marked = [...screen.getByTestId("answer").querySelectorAll("button.ref")].map((b) => b.textContent);
    const texts = (reasons.rows ?? []).map((row) => String(row.delay_reason));
    expect(marked.some((t) => texts.includes(t ?? ""))).toBe(false);
    expect(screen.getByTestId("answer").textContent).toBe(reasons.answer);
  });
});
