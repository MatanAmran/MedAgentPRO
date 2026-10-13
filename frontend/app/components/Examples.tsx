"use client";

import type { Example } from "@/lib/types";

/** Task 8, A4: the questions the scripted model answers, so the demo is never one typo away from "not
 * completed". Scripted model only: the server lists none on the real model. */
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
    <section className="flex flex-col gap-2" data-testid="examples" aria-label="Scripted examples">
      <div className="flex items-center gap-2 text-sm text-zinc-500">
        <span>{title}</span>
        <span className="rounded bg-zinc-100 px-2 py-0.5 text-xs">scripted examples</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {examples.map((e) => (
          <button
            key={e.question}
            type="button"
            disabled={disabled}
            onClick={() => onAsk(e.question)}
            className="rounded-full border border-zinc-300 bg-white px-3 py-1.5 text-left text-sm text-zinc-800 hover:border-zinc-900 disabled:opacity-50"
          >
            {e.question}
          </button>
        ))}
      </div>
    </section>
  );
}
