"use client";

import { useState } from "react";
import { agentSteps, formatUsd, readingsSentence } from "@/lib/present";
import type { ExchangeItem, Rating, Reading, ReferredPart, Stage } from "@/lib/types";
import { HowAnswered } from "./HowAnswered";
import { ReadingCard } from "./ReadingCard";
import { StageTimeline } from "./StageTimeline";

export function Question({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <p
        className="max-w-[70%] rounded-[18px_18px_6px_18px] bg-ink px-[18px] py-3.5 text-base font-medium text-white shadow-bubble"
        data-testid="question"
      >
        {text}
      </p>
    </div>
  );
}

/** The exchange while it runs: the question and the stages reached. Never model text (decision 8). */
export function Pending({
  question,
  seen,
  followUp,
  error,
  onStop,
  stopping,
}: {
  question: string;
  seen: Stage[];
  followUp: boolean;
  error: string | null;
  onStop: () => void;
  stopping: boolean;
}) {
  return (
    <div className="flex flex-col gap-5" data-testid="pending">
      <Question text={question} />
      {error ? (
        <p className="rounded-xl border border-line bg-white px-4 py-3 text-ink-2" role="alert">
          {error}
        </p>
      ) : (
        <StageTimeline seen={seen} followUp={followUp} onStop={onStop} stopping={stopping} />
      )}
    </div>
  );
}

function Thumbs({ value, onRate, busy }: { value: Rating | null; onRate: (r: Rating) => void; busy: boolean }) {
  const btn = (r: Rating, label: string, path: string) => (
    <button
      type="button"
      aria-label={label}
      aria-pressed={value === r}
      disabled={busy}
      onClick={() => onRate(r)}
      className={`flex h-11 w-11 items-center justify-center rounded-xl border transition-colors disabled:opacity-50 ${
        value === r ? "border-brand-deep bg-brand-deep text-white" : "border-line bg-white text-ink-2 hover:bg-canvas"
      }`}
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d={path} />
      </svg>
    </button>
  );
  return (
    <div className="flex items-center gap-2.5" data-testid="rating">
      <span className="text-sm font-semibold text-ink-3">Was this useful?</span>
      {btn("up", "Rate up", "M7 10v11H4V10zM7 10l4-7a2 2 0 0 1 3 2l-1 5h6a2 2 0 0 1 2 2.3l-1.4 7A2 2 0 0 1 17.6 21H7")}
      {btn("down", "Rate down", "M17 14V3h3v11zM17 14l-4 7a2 2 0 0 1-3-2l1-5H5a2 2 0 0 1-2-2.3l1.4-7A2 2 0 0 1 6.4 3H17")}
    </div>
  );
}

function AgentRow({ item }: { item: ExchangeItem }) {
  const { steps, seconds } = agentSteps(item);
  if (steps.length === 0) return null;
  return (
    <div
      className="flex flex-wrap items-center gap-2 rounded-[14px] border border-line bg-white px-3 py-2.5"
      data-testid="agent-row"
      aria-label="How the agent worked"
      role="group"
    >
      <span className="pr-1 text-xs font-extrabold tracking-[0.06em] text-brand-dark">HOW THE AGENT WORKED</span>
      {steps.map((s) => (
        <span key={s.label} className="inline-flex items-center gap-1.5 rounded-full bg-blush px-2.5 py-1 text-[13px] font-semibold text-brand-deep">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#B91C1C" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M5 12l5 5 9-10" />
          </svg>
          {s.label}
        </span>
      ))}
      {seconds !== null && seconds > 0 && <span className="ml-auto text-[13px] text-ink-4">{seconds} s in model calls</span>}
    </div>
  );
}

/** Task 4: a part referred to an investigation. Its records are a click away, from the plan the server kept,
 * with no model call; they come back saying they establish no cause (contract.SHOW_RECORDED). */
function ReferredPartView({
  part,
  onShowAll,
}: {
  part: ReferredPart;
  onShowAll?: (label: string) => Promise<Reading>;
}) {
  const [shown, setShown] = useState<Reading[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function show() {
    if (!onShowAll) return;
    setBusy(true);
    setError(null);
    try {
      setShown(await Promise.all(part.show_recorded.map((label) => onShowAll(label))));
    } catch {
      setError("The records could not be shown.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex flex-col gap-3 rounded-[20px] border border-line bg-white p-5 shadow-card" data-testid="referred-part">
      <p className="text-ink">{part.text}</p>
      {onShowAll && part.show_recorded.length > 0 && !shown && (
        <button
          type="button"
          onClick={show}
          disabled={busy}
          className="self-start rounded-xl border border-line bg-white px-4 py-2 font-bold text-ink hover:bg-canvas disabled:opacity-50"
        >
          Show what was recorded
        </button>
      )}
      {error && <p className="text-sm text-brand-dark">{error}</p>}
      {shown?.map((reading, i) => <ReadingCard key={reading.label} reading={reading} index={i + 1} />)}
    </div>
  );
}

export function ExchangeView({
  item,
  onRate,
  rating,
  onShowAll,
  onAskPart,
}: {
  item: ExchangeItem;
  onRate: (r: Rating) => void;
  rating: { busy: boolean; error: string | null };
  onShowAll?: (label: string) => Promise<Reading>; // Phase B task 5
  onAskPart?: (part: number, asked: string) => void; // task 4: the part as the person wrote it
}) {
  const r = item.response;
  const sentence = readingsSentence(r);
  // K4: thumbs under an answered exchange only, with at least one reading (task 8 review: a declined
  // action holds no records to rate); the conversation is the asker's own (S3)
  const ratable = r.outcome === "answered" && item.recorded && r.readings.length > 0;
  return (
    <div className="flex flex-col gap-[18px]" data-testid="exchange" data-outcome={r.outcome}>
      <Question text={item.posed} />
      {r.understood_as && (
        <p className="rounded-xl border border-blush-3 bg-blush px-3.5 py-3 text-sm text-brand-deep" data-testid="understood-as">
          <strong>Understood as:</strong> {r.understood_as}
        </p>
      )}
      <AgentRow item={item} />
      {sentence && <h2 className="text-[22px] font-extrabold tracking-tight text-ink">{sentence}</h2>}
      {r.readings.map((reading, i) => (
        <ReadingCard
          key={reading.label}
          reading={reading}
          index={i + 1}
          onShowAll={onShowAll && item.recorded ? () => onShowAll(reading.label) : undefined}
        />
      ))}
      {r.gaps.map((g, i) => (
        <p key={i} className="rounded-r-xl border-l-4 border-brand-deep bg-white py-2.5 pl-3.5 pr-3 text-ink" data-testid="gap">
          {g.text}
        </p>
      ))}
      {r.withheld && (
        <p className="text-sm italic text-ink-3" data-testid="withheld">
          {r.withheld}
        </p>
      )}
      {r.replaced && (
        <p className="text-sm italic text-ink-3" data-testid="replaced">
          {r.replaced}
        </p>
      )}
      {r.unanswered_parts?.map((part, i) => (
        <div
          key={`${part.asked}-${i}`}
          className="flex flex-col gap-2.5 rounded-r-xl border-l-4 border-brand-deep bg-white py-2.5 pl-3.5 pr-3"
          data-testid="unanswered-part"
        >
          <p className="text-ink">{part.text}</p>
          {onAskPart && item.recorded && part.ask && (
            <button
              type="button"
              onClick={() => onAskPart(i, part.asked)}
              className="self-start rounded-xl border border-line bg-white px-3.5 py-1.5 text-sm font-bold text-ink hover:bg-canvas"
            >
              {part.ask}
            </button>
          )}
        </div>
      ))}
      {r.referred_parts?.map((part, i) => (
        <ReferredPartView key={`${part.asked}-${i}`} part={part} onShowAll={item.recorded ? onShowAll : undefined} />
      ))}
      {r.coverage_notice && (
        <p className="text-sm italic text-ink-3" data-testid="coverage-notice">
          {r.coverage_notice}
        </p>
      )}
      {r.request_for_input && (
        <div className="rounded-[20px] border border-line bg-white p-5 shadow-card" data-testid="request-for-input">
          <p className="mb-2 text-ink">{r.request_for_input.text}</p>
          <ul className="flex flex-col gap-1 text-sm text-ink-3">
            {r.request_for_input.readings.map((x) => (
              <li key={x.label}>{x.checked}</li>
            ))}
          </ul>
        </div>
      )}
      {r.offer_investigation ? (
        <div
          className="flex flex-wrap items-center justify-between gap-3 rounded-[20px] border border-line bg-white p-5 shadow-card"
          data-testid="referral"
        >
          <p className="text-ink">{r.notice}</p>
          <div className="flex flex-col items-end gap-1">
            {/* Q1: shown and disabled; the demo has no investigation */}
            <button
              type="button"
              disabled
              title="Investigations are not part of this demo"
              className="bg-brand-gradient cursor-not-allowed rounded-xl px-4 py-2 font-bold text-white opacity-60"
            >
              Open an investigation
            </button>
            <span className="text-xs text-ink-4">Investigations are not part of this demo</span>
          </div>
        </div>
      ) : (
        r.notice && (
          <p className="rounded-xl border border-line bg-white px-4 py-3 text-ink-2" data-testid="notice">
            {r.notice}
          </p>
        )
      )}
      {(ratable || item.cost) && (
        <div className="flex flex-wrap items-center gap-x-3.5 gap-y-2.5 px-0.5 py-1">
          {ratable && <Thumbs value={item.rating} onRate={onRate} busy={rating.busy} />}
          {item.cost && (
            <span className="text-[13px] text-ink-4" data-testid="cost">
              {formatUsd(item.cost.exchange_usd)} for this answer · {formatUsd(item.cost.remaining_usd)} of the demo budget left
            </span>
          )}
        </div>
      )}
      {rating.error && <p className="text-sm text-brand-dark">{rating.error}</p>}
      {item.trace && <HowAnswered trace={item.trace} />}
    </div>
  );
}
