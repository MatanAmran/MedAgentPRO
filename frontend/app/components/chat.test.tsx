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
      conversations: vi.fn(async () => []),
      newConversation: vi.fn(async () => ({ conversation_id: "CV-1" })),
      conversation: vi.fn(async () => ({ conversation_id: "CV-1", exchanges: [...held] })),
      rate: vi.fn(),
      ask: vi.fn(
        (_u: string, _c: string, question: string, opts: { onStage?: (s: string) => void }) =>
          new Promise((resolve) => {
            opts.onStage?.("retrieval");
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
const { Chat } = await import("./Chat");

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
    await waitFor(() => expect(screen.getByTestId("stage").textContent).toMatch(/Retrieving records/));

    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "Another?" } });
    expect((screen.getByLabelText("Send") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("Demo user") as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "+ New conversation" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(screen.getByLabelText("Question"), { key: "Enter" });
    expect(api.ask).toHaveBeenCalledTimes(1);

    await act(async () => release?.());
    await waitFor(() => expect(screen.getAllByTestId("exchange")).toHaveLength(1));
    expect(screen.queryByTestId("pending")).toBeNull();
    expect((screen.getByLabelText("Demo user") as HTMLSelectElement).disabled).toBe(false);
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
