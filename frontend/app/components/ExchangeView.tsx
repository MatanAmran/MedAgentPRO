"use client";

import { formatUsd, readingsSentence } from "@/lib/present";
import type { ExchangeItem, Rating, Stage } from "@/lib/types";
import { ReadingCard } from "./ReadingCard";

export const STAGE_TEXT: Record<Stage, string> = {
  waiting: "Finishing the previous question",
  rewriting: "Reading the question in context",
  intent: "Planning what to look up",
  retrieval: "Retrieving records",
  synthesis: "Writing the answer",
  gate: "Checking each statement against the records",
  regeneration: "Rewriting without the unsupported statements",
  contract: "Assembling the answer",
};

export function Question({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <p className="max-w-[80%] rounded-2xl bg-zinc-100 px-4 py-2.5 text-zinc-900" data-testid="question">
        {text}
      </p>
    </div>
  );
}

/** The exchange while it runs: the question and the stage reached. Never model text (decision 8). */
export function Pending({ question, stage, error }: { question: string; stage: Stage | null; error: string | null }) {
  return (
    <div className="flex flex-col gap-3" data-testid="pending">
      <Question text={question} />
      {error ? (
        <p className="rounded-lg border border-zinc-300 px-4 py-3 text-zinc-800" role="alert">
          {error}
        </p>
      ) : (
        <p className="animate-pulse text-sm text-zinc-500" data-testid="stage">
          {stage ? STAGE_TEXT[stage] : "Sending the question"}…
        </p>
      )}
    </div>
  );
}

function Thumbs({ value, onRate, busy }: { value: Rating | null; onRate: (r: Rating) => void; busy: boolean }) {
  const btn = (r: Rating, label: string, glyph: string) => (
    <button
      type="button"
      aria-label={label}
      aria-pressed={value === r}
      disabled={busy}
      onClick={() => onRate(r)}
      className={`flex h-9 w-9 items-center justify-center rounded-lg border text-lg transition-colors disabled:opacity-50 ${
        value === r ? "border-zinc-900 bg-zinc-900 text-white" : "border-zinc-300 hover:bg-zinc-100"
      }`}
    >
      {glyph}
    </button>
  );
  return (
    <div className="flex items-center gap-2" data-testid="rating">
      <span className="text-sm text-zinc-500">Rate this answer</span>
      {btn("up", "Rate up", "\u{1F44D}︎")}
      {btn("down", "Rate down", "\u{1F44E}︎")}
    </div>
  );
}

export function ExchangeView({
  item,
  onRate,
  rating,
}: {
  item: ExchangeItem;
  onRate: (r: Rating) => void;
  rating: { busy: boolean; error: string | null };
}) {
  const r = item.response;
  const sentence = readingsSentence(r);
  // K4: thumbs under an answered exchange only; the conversation is the asker's own (S3)
  const ratable = r.outcome === "answered" && item.recorded;
  return (
    <div className="flex flex-col gap-3" data-testid="exchange" data-outcome={r.outcome}>
      <Question text={item.posed} />
      {r.understood_as && (
        <p className="text-right text-sm text-zinc-500" data-testid="understood-as">
          <span className="font-semibold text-zinc-800">Understood as:</span> {r.understood_as}
        </p>
      )}
      {sentence && <p className="text-zinc-800">{sentence}</p>}
      {r.readings.map((reading) => (
        <ReadingCard key={reading.label} reading={reading} />
      ))}
      {r.gaps.map((g, i) => (
        <p key={i} className="border-l-4 border-zinc-900 pl-3 text-zinc-900" data-testid="gap">
          {g.text}
        </p>
      ))}
      {r.withheld && (
        <p className="text-sm italic text-zinc-600" data-testid="withheld">
          {r.withheld}
        </p>
      )}
      {r.request_for_input && (
        <div className="rounded-xl border border-zinc-300 p-4" data-testid="request-for-input">
          <p className="mb-2 text-zinc-900">{r.request_for_input.text}</p>
          <ul className="flex flex-col gap-1 text-sm text-zinc-600">
            {r.request_for_input.readings.map((x) => (
              <li key={x.label}>{x.checked}</li>
            ))}
          </ul>
        </div>
      )}
      {r.offer_investigation ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-zinc-300 p-4" data-testid="referral">
          <p className="text-zinc-900">{r.notice}</p>
          <div className="flex flex-col items-end gap-1">
            {/* Q1: shown and disabled; the demo has no investigation */}
            <button
              type="button"
              disabled
              title="Investigations are not part of this demo"
              className="cursor-not-allowed rounded-lg bg-red-700 px-4 py-2 font-semibold text-white opacity-50"
            >
              Open an investigation
            </button>
            <span className="text-xs text-zinc-500">Investigations are not part of this demo</span>
          </div>
        </div>
      ) : (
        r.notice && (
          <p className="rounded-lg border border-zinc-300 px-4 py-3 text-zinc-800" data-testid="notice">
            {r.notice}
          </p>
        )
      )}
      {ratable && <Thumbs value={item.rating} onRate={onRate} busy={rating.busy} />}
      {rating.error && <p className="text-sm text-red-700">{rating.error}</p>}
      {item.cost && (
        <p className="text-xs text-zinc-400" data-testid="cost">
          This answer cost {formatUsd(item.cost.exchange_usd)} · {formatUsd(item.cost.remaining_usd)} of the demo budget
          left
        </p>
      )}
    </div>
  );
}
