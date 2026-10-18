import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { goldenResponses } from "@/lib/fixtures";
import type { ExchangeItem } from "@/lib/types";

const late = goldenResponses()["which_suppliers_were_late_in_weeks_1_to_13__AP-01"];

const held: ExchangeItem[] = [];
let release: (() => void) | null = null;

vi.mock("@/lib/api", async (original) => {
  const real = await original<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      health: vi.fn(async () => ({ mode: "scripted", model: "scripted", authentication: "none (demo)" })),
      examples: vi.fn(async () => [
        { question: "Which suppliers were late in weeks 1 to 13?", follows: null, outcome: "answered", shows: [] },
        { question: "And in weeks 14 to 26?", follows: "Which suppliers were late in weeks 1 to 13?",
          outcome: "answered", shows: [] },
        { question: "Why is S-014 late so often?", follows: null, outcome: "referred_to_investigation", shows: [] },
      ]),
      conversations: vi.fn(async () => []),
      newConversation: vi.fn(async () => ({ conversation_id: "CV-1" })),
      conversation: vi.fn(async () => ({ conversation_id: "CV-1", exchanges: [...held] })),
      rate: vi.fn(),
      ask: vi.fn(
        (_u: string, _c: string, question: string, opts: { onStage?: (s: string) => void; signal?: AbortSignal }) =>
          new Promise((resolve, reject) => {
            opts.onStage?.("retrieval");
            opts.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
            release = () => {
              const done = { exchange_id: `EX-${held.length + 1}`, recorded: true, response: late, cost: null };
              held.push({ ...done, posed: question, rating: null });
              resolve(done);
            };
          }),
      ),
    },
  };
});

const { api } = await import("@/lib/api");
const { Chat, STOPPED } = await import("./Chat");

beforeEach(() => {
  held.length = 0;
  release = null;
  vi.mocked(api.ask).mockClear();
});
afterEach(cleanup);

function ask(text: string) {
  fireEvent.change(screen.getByLabelText("Question"), { target: { value: text } });
  fireEvent.keyDown(screen.getByLabelText("Question"), { key: "Enter" });
}

describe("one question at a time (R8)", () => {
  it("locks sending, the user and the conversations until the answer arrives, then unlocks", async () => {
    render(<Chat />);
    ask("Which suppliers were late in weeks 1 to 13?");
    await waitFor(() => expect(screen.getByTestId("stage").textContent).toMatch(/^Retrieving$/));

    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "Another?" } });
    expect((screen.getByLabelText("Send") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId("user-picker") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "New conversation" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(screen.getByLabelText("Question"), { key: "Enter" });
    expect(api.ask).toHaveBeenCalledTimes(1);

    await act(async () => release?.());
    await waitFor(() => expect(screen.getAllByTestId("exchange")).toHaveLength(1));
    expect(screen.queryByTestId("pending")).toBeNull();
    expect((screen.getByTestId("user-picker") as HTMLButtonElement).disabled).toBe(false);
  });

  it("sends a new request id for every question", async () => {
    render(<Chat />);
    ask("First?");
    await waitFor(() => expect(api.ask).toHaveBeenCalledTimes(1));
    await act(async () => release?.());
    await waitFor(() => expect(screen.getAllByTestId("exchange")).toHaveLength(1));
    ask("Second?");
    await waitFor(() => expect(api.ask).toHaveBeenCalledTimes(2));
    const ids = vi.mocked(api.ask).mock.calls.map((c) => c[3].requestId);
    expect(new Set(ids).size).toBe(2);
    ids.forEach((id) => expect(id).toMatch(/^[A-Za-z0-9_-]{8,100}$/));
  });

  it("shows a refusal under the question, and unlocks without sending again", async () => {
    const { ApiError } = await import("@/lib/api");
    vi.mocked(api.ask).mockRejectedValueOnce(new ApiError(409, "a question in this conversation is still being answered"));
    render(<Chat />);
    ask("First?");
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/still being answered/));
    expect(api.ask).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "Again?" } });
    expect((screen.getByLabelText("Send") as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("scripted examples (task 8, A4)", () => {
  it("offers the questions that stand alone on an empty screen, and sends one on a click", async () => {
    render(<Chat />);
    const box = await screen.findByTestId("examples");
    const offered = Array.from(box.querySelectorAll("button")).map((b) => b.textContent);
    expect(offered).toEqual(["Which suppliers were late in weeks 1 to 13?", "Why is S-014 late so often?"]);
    expect(box.textContent).toContain("scripted examples");
    fireEvent.click(screen.getByRole("button", { name: "Which suppliers were late in weeks 1 to 13?" }));
    await waitFor(() => expect(api.ask).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.ask).mock.calls[0][2]).toBe("Which suppliers were late in weeks 1 to 13?");
    expect(screen.queryByTestId("examples")).toBeNull();   // not while the question runs
  });

  it("offers a follow-up under the question it follows, once answered", async () => {
    render(<Chat />);
    fireEvent.click(await screen.findByRole("button", { name: "Which suppliers were late in weeks 1 to 13?" }));
    await waitFor(() => expect(api.ask).toHaveBeenCalledTimes(1));
    await act(async () => release?.());
    await waitFor(() => expect(screen.getAllByTestId("exchange")).toHaveLength(1));
    const box = await screen.findByTestId("examples");
    expect(Array.from(box.querySelectorAll("button")).map((b) => b.textContent)).toEqual(["And in weeks 14 to 26?"]);
  });

  it("offers nothing where the server lists none (the real model)", async () => {
    vi.mocked(api.examples).mockResolvedValueOnce([]);
    render(<Chat />);
    await screen.findByLabelText("Question");
    await waitFor(() => expect(api.examples).toHaveBeenCalled());
    expect(screen.queryByTestId("examples")).toBeNull();
  });

  it("says what can be asked on the real model, as text only (long list, item 6)", async () => {
    vi.mocked(api.health).mockResolvedValueOnce({ mode: "real", model: "m", authentication: "none (demo)" } as never);
    vi.mocked(api.examples).mockResolvedValueOnce([]);
    render(<Chat />);
    const text = await screen.findByTestId("can-ask");
    for (const w of ["suppliers", "purchase orders", "parts", "stock on hand", "forecast"]) expect(text.textContent).toContain(w);
    expect(text.querySelectorAll("button, a")).toHaveLength(0);
  });

  it("says nothing of the kind on the scripted model, which offers its examples", async () => {
    render(<Chat />);
    await screen.findByTestId("examples");
    expect(screen.queryByTestId("can-ask")).toBeNull();
  });
});

describe("Stop (UI refresh)", () => {
  it("aborts the question, says it was stopped, and frees the screen", async () => {
    render(<Chat />);
    ask("Which suppliers were late in weeks 1 to 13?");
    await waitFor(() => expect(screen.getByTestId("timeline")).toBeTruthy());
    expect(screen.getByTestId("timeline").querySelector('[data-state="active"]')?.textContent).toContain("Retrieving");
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(STOPPED));
    expect((screen.getByTestId("user-picker") as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByTestId("timeline")).toBeNull();
  });
});
