"use client";

import { useId, useState } from "react";
import type { Trace, TraceCall } from "@/lib/types";

// Task 10 [demo extension]: how an answer was built, under it, closed until asked for. Metadata only: the
// server sends no prompt, no model text, no record value and no rejection message (api/trace.py).

export const NOT_SENT_TEXT: Record<NonNullable<Trace["not_sent"]>, string> = {
  rate_limit: "Not sent: rate limit",
  budget: "Not sent: budget",
};

export const NOTE_TEXT: Record<NonNullable<Trace["note"]>, string> = {
  plan_decided: "No answer was drafted: the plan alone decided this.",
  no_records: "No answer was drafted: no records matched.",
};

const STAGE_LABEL: Record<string, string> = {
  question_rewriting: "Rewriting",
  intent_analysis: "Planning",
  synthesis: "Drafting",
  support_gate: "Checking",
  regeneration: "Redrafting",
};

function isScripted(call: TraceCall): boolean {
  return call.model.startsWith("scripted");
}

function tokens(v: number | null, estimate: boolean): string {
  if (v === null) return "—";
  return `${estimate ? "≈" : ""}${v.toLocaleString("en-US")}`;
}

export function usd(v: number | null): string {
  if (v === null) return "—"; // unknown, never shown as free
  return `$${v.toFixed(4)}`;
}

function seconds(ms: number | null): string {
  return ms === null ? "—" : `${(ms / 1000).toFixed(1)} s`;
}

export function HowAnswered({ trace }: { trace: Trace }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const scripted = trace.calls.length > 0 && trace.calls.every(isScripted);

  return (
    <div className="text-xs text-ink-2" data-testid="how-answered">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
        className="min-h-11 rounded-xl border border-line bg-white px-3.5 text-sm font-bold text-ink-2 hover:bg-canvas"
      >
        How this was answered {open ? "▾" : "▸"}
      </button>
      {open && (
        <div id={id} className="mt-2 flex flex-col gap-2 rounded-[14px] border border-line bg-white p-3" data-testid="how-answered-panel">
          {trace.calls.length === 0 ? (
            <p data-testid="how-answered-empty">{trace.not_sent ? NOT_SENT_TEXT[trace.not_sent] : "No model call"}</p>
          ) : (
            <div className="max-w-full overflow-x-auto">
              <table className="min-w-full border-collapse text-left">
                <caption className="sr-only">One row per model call</caption>
                <thead>
                  <tr className="border-b border-line text-ink-3">
                    <th scope="col" className="py-1 pr-3 font-medium">Stage</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Model</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Attempt</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Tokens in / out (thinking)</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Output cap</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Claims</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Cost</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Time</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Finish</th>
                    <th scope="col" className="py-1 font-medium">Rejected by</th>
                  </tr>
                </thead>
                <tbody>
                  {trace.calls.map((c, i) => {
                    const est = isScripted(c);
                    return (
                      <tr key={i} className="border-b border-line-3" data-testid="how-answered-row">
                        <td className="py-1 pr-3">{STAGE_LABEL[c.stage] ?? c.stage}</td>
                        <td className="py-1 pr-3">{est ? "scripted" : c.model}</td>
                        <td className="py-1 pr-3">
                          {c.attempt_number}
                          {c.feedback_attempt ? " (with feedback)" : ""}
                        </td>
                        <td className="py-1 pr-3">
                          {tokens(c.input_tokens, est)} / {tokens(c.output_tokens, est)}
                          {c.reasoning_tokens ? ` (${tokens(c.reasoning_tokens, est)})` : ""}
                        </td>
                        <td className="py-1 pr-3">{c.max_tokens ?? "—"}</td>
                        <td className="py-1 pr-3">{c.claims ?? ""}</td>
                        <td className="py-1 pr-3">{usd(c.cost_usd)}</td>
                        <td className="py-1 pr-3">{seconds(c.duration_ms)}</td>
                        <td className="py-1 pr-3">{c.finish_reason ?? "—"}</td>
                        <td className="py-1 font-mono">{c.check ?? ""}</td>
                      </tr>
                    );
                  })}
                </tbody>
                <tfoot>
                  <tr className="font-medium" data-testid="how-answered-total">
                    <td className="py-1 pr-3" colSpan={3}>
                      Total
                    </td>
                    <td className="py-1 pr-3">
                      {tokens(trace.total.input_tokens, scripted)} / {tokens(trace.total.output_tokens, scripted)}
                      {trace.total.reasoning_tokens ? ` (${tokens(trace.total.reasoning_tokens, scripted)})` : ""}
                    </td>
                    <td className="py-1 pr-3" colSpan={2} />
                    <td className="py-1 pr-3">{usd(trace.total.usd)}</td>
                    <td className="py-1" colSpan={3} />
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
          {trace.unpriced_usd !== null && (
            <p>{usd(trace.unpriced_usd)} reserved for calls whose cost is unknown.</p>
          )}
          {trace.note && <p data-testid="how-answered-note">{NOTE_TEXT[trace.note]}</p>}
          {scripted && <p className="text-ink-3">Scripted model: no cost, and tokens are estimates (≈).</p>}
        </div>
      )}
    </div>
  );
}
