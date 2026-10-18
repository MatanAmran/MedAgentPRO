"use client";

import { useId } from "react";
import { DEMO_USERS, type Example } from "@/lib/types";

/** Task 8, A4: the questions the scripted model answers, so the demo is never one typo away from "not
 * completed". Scripted model only: the server lists none on the real model. "Follow up" offers the scripted
 * follow-up of the last question (demo question 2), as before the UI refresh.
 *
 * An example scripted for another demo user than the one the screen starts as (question 14, "my" lines, as
 * PL-02) says so on its chip: "as PL-02" (chat 19.10). Clicking it asks as the current user; it never switches
 * the user by itself. The chip's name stays the question; the user is its description. */
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
  const id = useId();
  if (examples.length === 0) return null;
  return (
    <section className="flex flex-col gap-2.5" data-testid="examples" aria-label="Scripted examples">
      <div className="flex items-center gap-2 text-sm font-semibold text-ink-3">
        <span>{title}</span>
        <span className="rounded-full border border-line bg-white px-2 py-0.5 text-xs font-medium text-ink-3">scripted examples</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {examples.map((e, i) => {
          const asOther = e.user && e.user !== DEMO_USERS[0].id ? `as ${e.user}` : null;
          return (
            <button
              key={e.question}
              type="button"
              disabled={disabled}
              onClick={() => onAsk(e.question)}
              aria-label={e.question}
              aria-describedby={asOther ? `${id}-as-${i}` : undefined}
              className="inline-flex min-h-11 items-center gap-2 rounded-xl border border-blush-3 bg-blush px-3.5 text-left text-sm font-semibold text-brand-dark hover:border-brand hover:bg-white disabled:opacity-50"
            >
              <span>{e.question}</span>
              {asOther && (
                <span
                  id={`${id}-as-${i}`}
                  data-testid="example-user"
                  className="shrink-0 rounded-full border border-blush-3 bg-white px-2 py-0.5 text-xs font-medium text-ink-2"
                >
                  {asOther}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </section>
  );
}
