import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { goldenResponses, item } from "@/lib/fixtures";
import type { Trace, TraceCall } from "@/lib/types";
import { ExchangeView } from "./ExchangeView";
import { HowAnswered, NOT_SENT_TEXT, NOTE_TEXT, usd } from "./HowAnswered";

afterEach(cleanup);

function call(over: Partial<TraceCall> = {}): TraceCall {
  return {
    stage: "intent_analysis", prompt_identity: "intent_analysis_demo@8", model: "claude-sonnet-5-5",
    attempt_number: 1, feedback_attempt: false, status: "accepted", finish_reason: "stop", input_tokens: 3800,
    output_tokens: 900, reasoning_tokens: 300, claims: null, max_tokens: 3000, duration_ms: 5200, check: null,
    cost_usd: 0.0166, ...over,
  };
}

function trace(over: Partial<Trace> = {}): Trace {
  return {
    calls: [call()], total: { input_tokens: 3800, output_tokens: 900, reasoning_tokens: 300, usd: 0.0166 },
    unpriced_usd: null, not_sent: null, note: null, ...over,
  };
}

function open(t: Trace) {
  render(<HowAnswered trace={t} />);
  const button = screen.getByRole("button", { name: /How this was answered/ });
  fireEvent.click(button);
  return button;
}

describe("How this was answered (task 10)", () => {
  it("is closed until asked for, and says so to assistive technology", () => {
    render(<HowAnswered trace={trace()} />);
    const button = screen.getByRole("button", { name: /How this was answered/ });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("how-answered-panel")).toBeNull();
    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("how-answered-panel").id).toBe(button.getAttribute("aria-controls"));
    fireEvent.click(button);
    expect(screen.queryByTestId("how-answered-panel")).toBeNull();
  });

  it("shows one row per call, with the gate's claims, the cap sent and the check that rejected", () => {
    open(trace({
      calls: [
        call(),
        call({ stage: "support_gate", prompt_identity: "support_scoring@5", status: "rejected", claims: 4,
               check: "GateOutputError", max_tokens: 3000, cost_usd: 0.02 }),
        call({ stage: "support_gate", prompt_identity: "support_scoring@5", attempt_number: 2,
               feedback_attempt: true, claims: 4, cost_usd: 0.02 }),
      ],
    }));
    const rows = screen.getAllByTestId("how-answered-row");
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText("Planning")).toBeTruthy();
    expect(within(rows[1]).getByText("GateOutputError")).toBeTruthy();
    expect(within(rows[1]).getByText("4")).toBeTruthy();
    expect(within(rows[2]).getByText("2 (with feedback)")).toBeTruthy();
    expect(within(rows[0]).getByText("3,800 / 900 (300)")).toBeTruthy();
    expect(screen.getByTestId("how-answered-total").textContent).toContain("$0.0166");
  });

  it("shows a scripted call as free, and an unpriced call as unknown, never as free", () => {
    expect(usd(0)).toBe("$0.0000");
    expect(usd(null)).toBe("—");
    open(trace({
      calls: [call({ model: "scripted:scripted-demo", cost_usd: 0 })],
      total: { input_tokens: 10, output_tokens: 5, reasoning_tokens: null, usd: 0 },
    }));
    const row = screen.getByTestId("how-answered-row");
    expect(within(row).getByText("scripted")).toBeTruthy();
    expect(within(row).getByText("$0.0000")).toBeTruthy();
    expect(row.textContent).toContain("≈");
    expect(screen.getByText(/Scripted model: no cost/)).toBeTruthy();
    cleanup();
    open(trace({ calls: [call({ cost_usd: null })], total: { input_tokens: 1, output_tokens: 1, reasoning_tokens: null, usd: null } }));
    expect(within(screen.getByTestId("how-answered-row")).getByText("—", { selector: "td:nth-child(7)" })).toBeTruthy();
    expect(screen.queryByText(/Scripted model/)).toBeNull();
  });

  it("says what was reserved for calls whose cost is unknown", () => {
    open(trace({ calls: [call({ status: "failed", cost_usd: null, check: "timeout" })], unpriced_usd: 0.04 }));
    expect(screen.getByText("$0.0400 reserved for calls whose cost is unknown.")).toBeTruthy();
  });

  it.each([
    [{ not_sent: "rate_limit" as const }, NOT_SENT_TEXT.rate_limit],
    [{ not_sent: "budget" as const }, NOT_SENT_TEXT.budget],
    [{ not_sent: null }, "No model call"],
  ])("with no call: %o", (over, says) => {
    open(trace({ calls: [], ...over }));
    expect(screen.getByTestId("how-answered-empty").textContent).toBe(says);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it.each([["plan_decided" as const], ["no_records" as const]])("says why no answer was drafted: %s", (note) => {
    open(trace({ note }));
    expect(screen.getByTestId("how-answered-note").textContent).toBe(NOTE_TEXT[note]);
    expect(screen.getAllByTestId("how-answered-row")).toHaveLength(1);
  });

  it("appears under an answer only where the server sent a trace", () => {
    const r = Object.values(goldenResponses())[0];
    render(<ExchangeView item={item(r)} onRate={() => {}} rating={{ busy: false, error: null }} />);
    expect(screen.queryByTestId("how-answered")).toBeNull();
    cleanup();
    render(<ExchangeView item={item(r, { trace: trace() })} onRate={() => {}} rating={{ busy: false, error: null }} />);
    expect(screen.getByTestId("how-answered")).toBeTruthy();
  });
});
