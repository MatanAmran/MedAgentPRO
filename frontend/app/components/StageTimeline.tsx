"use client";

import type { Stage } from "@/lib/types";

// UI refresh (Working.dc.html): the stages of the exchange as they start, from the stream's stage events
// alone (decision 8). The details are fixed text: the screen has no count and no rewrite before the
// answer, so it shows none (plan, point 1). Unchecked model text never reaches it.

export const STAGE_STEP: Record<Stage, { title: string; detail: string }> = {
  waiting: { title: "Waiting", detail: "The previous question in this conversation is finishing" },
  rewriting: { title: "Reading the follow-up", detail: "Read with the conversation so far" },
  intent: { title: "Planning", detail: "What to look up; code checks the plan before anything runs" },
  retrieval: { title: "Retrieving", detail: "From the records, every reading at once" },
  synthesis: { title: "Drafting", detail: "Text with references; code fills every value" },
  gate: { title: "Checking", detail: "Every sentence against its records" },
  regeneration: { title: "Redrafting", detail: "Without the statements the records do not support" },
  contract: { title: "Presenting", detail: "Only what the check supports" },
};

const AHEAD: Stage[] = ["intent", "retrieval", "synthesis", "gate", "contract"];

/** The stages to show: those seen, in order, then those still ahead in the usual order. A follow-up starts
 * with rewriting; a redraft appears only once it starts. */
export function stagesToShow(seen: Stage[], followUp: boolean): Stage[] {
  const base: Stage[] = followUp ? ["rewriting", ...AHEAD] : [...AHEAD];
  const ahead = base.filter((s) => !seen.includes(s));
  const last = seen[seen.length - 1];
  const from = last ? base.indexOf(last) : -1;
  return [...seen, ...ahead.filter((s) => base.indexOf(s) > from)];
}

export function StageTimeline({
  seen,
  followUp,
  onStop,
  stopping,
}: {
  seen: Stage[];
  followUp: boolean;
  onStop: () => void;
  stopping: boolean;
}) {
  const steps = stagesToShow(seen, followUp);
  const current = seen[seen.length - 1] ?? null;
  return (
    <section
      aria-label="Working on it"
      className="flex flex-col gap-4 rounded-[20px] border border-line bg-white px-6 py-5 shadow-card"
      data-testid="timeline"
    >
      <div className="flex flex-wrap items-center gap-3">
        <span aria-hidden="true" className="stage-live h-3 w-3 rounded-full bg-brand-hi" />
        <span className="text-[17px] font-extrabold text-ink">{stopping ? "Stopping" : "Working on it"}</span>
        <span className="text-sm text-ink-4">usually 20 to 60 seconds on the live model</span>
        <button
          type="button"
          onClick={onStop}
          disabled={stopping}
          className="ml-auto flex min-h-11 items-center gap-2 rounded-xl border border-line bg-white px-4 text-sm font-bold text-ink-2 hover:bg-canvas disabled:opacity-50"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="#44403C" aria-hidden="true">
            <rect x="5" y="5" width="14" height="14" rx="2" />
          </svg>
          Stop
        </button>
      </div>
      <p className="sr-only" aria-live="polite" data-testid="stage">
        {current ? STAGE_STEP[current].title : "Sending the question"}
      </p>
      <ol className="m-0 flex list-none flex-col p-0">
        {steps.map((s, i) => {
          const done = seen.includes(s) && s !== current;
          const active = s === current;
          return (
            <li key={`${s}-${i}`} className="flex items-stretch gap-3.5" data-state={done ? "done" : active ? "active" : "todo"}>
              <div className="flex w-7 flex-col items-center">
                <span
                  className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${
                    done
                      ? "bg-mark-gradient shadow-[0_4px_10px_-4px_rgba(185,28,28,0.7)]"
                      : active
                        ? "border-[3px] border-brand-hi bg-white"
                        : "border-2 border-[#E7E5E4] bg-white"
                  }`}
                >
                  {done && (
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M5 12l5 5 9-10" />
                    </svg>
                  )}
                </span>
                {i < steps.length - 1 && <span className={`min-h-[18px] w-0.5 flex-1 ${done ? "bg-brand-hi" : "bg-[#E7E5E4]"}`} />}
              </div>
              <div className="flex flex-1 flex-col gap-0.5 pb-[18px] pt-[3px]">
                <span className={`text-[15px] ${done || active ? "font-extrabold text-ink" : "font-semibold text-ink-4"}`}>
                  {STAGE_STEP[s].title}
                  <span className="sr-only">{done ? ", done" : active ? ", in progress" : ", not started"}</span>
                </span>
                <span className="text-[13px] text-ink-4">{STAGE_STEP[s].detail}</span>
                {active && <div aria-hidden="true" className="stage-shimmer mt-2 h-2.5 w-[70%] rounded-[5px]" />}
              </div>
            </li>
          );
        })}
      </ol>
      <p className="text-xs text-ink-4">Nothing is shown until the check is done. Unchecked model text never reaches this screen.</p>
    </section>
  );
}
