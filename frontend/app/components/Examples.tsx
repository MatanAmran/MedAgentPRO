"use client";

import type { Example } from "@/lib/types";

/** Task 8, A4: the questions the scripted model answers, so the demo is never one typo away from "not
 * completed". Scripted model only: the server lists none on the real model. "Follow up" offers the scripted
 * follow-up of the last question (demo question 2), as before the UI refresh. */
export function Examples({
  examples,
  title,
  onAsk,
  disabled,
}: {
  examples: Example[];
  title: string;
  onAsk: (question: string) => void;
  disabled: boolean;
}) {
  if (examples.length === 0) return null;
  return (
    <section className="flex flex-col gap-2.5" data-testid="examples" aria-label="Scripted examples">
      <div className="flex items-center gap-2 text-sm font-semibold text-ink-3">
        <span>{title}</span>
        <span className="rounded-full border border-line bg-white px-2 py-0.5 text-xs font-medium text-ink-3">scripted examples</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {examples.map((e) => (
          <button
            key={e.question}
            type="button"
            disabled={disabled}
            onClick={() => onAsk(e.question)}
            className="min-h-11 rounded-xl border border-blush-3 bg-blush px-3.5 text-left text-sm font-semibold text-brand-dark hover:border-brand hover:bg-white disabled:opacity-50"
          >
            {e.question}
          </button>
        ))}
      </div>
    </section>
  );
}
