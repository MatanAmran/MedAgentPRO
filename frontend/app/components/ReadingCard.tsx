"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { FILTER_TAGS_OVER, answerText, foldAnswer, formatCell, groupRows, isTagReading, readingCount, readingCsv,
  readingCsvName, shownNotice } from "@/lib/present";
import type { Cell, Reading } from "@/lib/types";

// UI refresh (Main.dc.html): a reading as a card. Number and title, how many rows, Copy (the answer as
// shown) and Export (the rows as shown, with their headers), the table folded after six rows, and
// Examined at the foot.

export const FOLD_AFTER = 6;

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-block rounded-md border border-line bg-canvas px-2 py-0.5 font-mono text-[13px] font-semibold text-ink">
      {children}
    </span>
  );
}

function Value({ column, value, ids }: { column: string; value: Cell; ids: string[] }) {
  return ids.includes(column) && value !== null ? <Tag>{formatCell(value)}</Tag> : <>{formatCell(value)}</>;
}

// Phase C: the row a value of the answer came from, marked while that value is pointed at or focused
const HOT_ROW = "bg-blush shadow-[inset_3px_0_0_#B91C1C]";

function Table({ reading, rows, hot, rowId }: { reading: Reading; rows: Record<string, Cell>[]; hot: number | null;
                                                rowId: (n: number) => string }) {
  const cols = reading.columns ?? [];
  const ids = reading.identifiers ?? [];
  return (
    <div className="max-w-full overflow-x-auto">
      <table className="w-full border-separate border-spacing-0 text-left text-sm" data-testid="reading-table">
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c} className="border-b border-line px-3 py-2 text-xs font-bold uppercase tracking-[0.06em] text-ink-4">
                {reading.headers?.[c] ?? c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} id={rowId(i + 1)} data-row={i + 1} data-hot={hot === i + 1 || undefined}
                className={hot === i + 1 ? HOT_ROW : undefined}>
              {cols.map((c) => (
                <td key={c} className="border-b border-[#F5EFEE] px-3 py-2.5 text-ink">
                  <Value column={c} value={row[c] ?? null} ids={ids} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Grouped({ reading, by, groups, hot, rowId }: { reading: Reading; hot: number | null; rowId: (n: number) => string }
  & NonNullable<ReturnType<typeof groupRows>>) {
  const ids = reading.identifiers ?? [];
  const all = reading.rows ?? [];
  const rest = (reading.columns ?? []).filter((c) => c !== by);
  return (
    <div className="flex flex-col gap-2" data-testid="reading-groups">
      {groups.map((g, i) => (
        <div key={i} className="grid grid-cols-[6rem_1fr] items-baseline gap-2" data-testid="reading-group">
          <span className="font-mono font-semibold text-ink">{formatCell(g.key)}</span>
          <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
            {g.rows.map((row, j) => {
              const n = all.indexOf(row) + 1;
              return (
              <span key={j} id={rowId(n)} data-row={n} data-hot={hot === n || undefined}
                    className={`inline-flex items-baseline gap-1 rounded-md px-1 ${hot === n ? HOT_ROW : ""}`}>
                {rest.map((c) =>
                  ids.includes(c) ? (
                    <Value key={c} column={c} value={row[c] ?? null} ids={ids} />
                  ) : (
                    <span key={c} className="text-xs text-ink-3">
                      {reading.headers?.[c] ?? c} {formatCell(row[c] ?? null)}
                    </span>
                  ),
                )}
              </span>
              );
            })}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Long list, item 3: a reading whose only column is an identifier, as tags six to a row. Past
 * FILTER_TAGS_OVER tags, a filter over the values shown, on the screen only. A tag is not a tab stop:
 * pointing at one marks the values of the answer that came from its row, and those values mark it. */
function Tags({ reading, hot, onHot, rowId }: { reading: Reading; hot: number | null;
                                               onHot: (row: number | null) => void; rowId: (n: number) => string }) {
  const [filter, setFilter] = useState("");
  const col = (reading.columns ?? [])[0];
  const rows = reading.rows ?? [];
  const q = filter.trim().toLowerCase();
  const tags = rows.map((row, i) => ({ n: i + 1, text: formatCell(row[col] ?? null) }));
  const kept = q ? tags.filter((t) => t.text.toLowerCase().includes(q)) : tags;
  return (
    <div className="flex flex-col gap-2" data-testid="reading-tags">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs font-extrabold uppercase tracking-[0.06em] text-ink-4">{reading.headers?.[col] ?? col}</span>
        {tags.length > FILTER_TAGS_OVER && (
          <label className="ml-auto flex min-h-9 items-center gap-2 rounded-[10px] border border-line bg-canvas px-2.5 text-[13px] text-ink-4">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
              <circle cx="11" cy="11" r="7" />
              <path d="M20 20l-3.5-3.5" />
            </svg>
            <input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter these rows"
              aria-label="Filter these rows"
              data-testid="tag-filter"
              className="w-36 border-0 bg-transparent text-ink outline-none placeholder:text-ink-4"
            />
          </label>
        )}
      </div>
      {q && (
        <p className="m-0 text-[13px] text-ink-4" role="status">
          {kept.length} of {tags.length} shown
        </p>
      )}
      <ul className="m-0 grid list-none grid-cols-2 gap-2 p-0 min-[480px]:grid-cols-3 sm:grid-cols-4 md:grid-cols-6">
        {kept.map((t) => (
          <li
            key={t.n}
            id={rowId(t.n)}
            data-row={t.n}
            data-hot={hot === t.n || undefined}
            onMouseEnter={() => onHot(t.n)}
            onMouseLeave={() => onHot(null)}
            className={`truncate rounded-[10px] border py-2 text-center font-mono text-[13px] font-semibold ${
              hot === t.n ? "border-brand bg-brand text-white shadow-[0_6px_14px_-6px_rgba(185,28,28,.7)]"
                          : "border-line bg-canvas text-ink-2"}`}
          >
            {t.text}
          </li>
        ))}
      </ul>
    </div>
  );
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

const tool = "min-h-9 rounded-[10px] border border-line bg-white px-3 text-[13px] font-semibold text-ink-2 hover:bg-canvas";

// Punctuation that closes on the value before it: kept on the value's line (long list, item 1)
const CLOSING = /^[.,;:!?)\]}%»”’]+/;

const ANSWER = "m-0 px-5 pb-1.5 pt-[18px] text-base leading-[1.8] text-ink";

/** Phase C: the answer with each filled value marked; pointing at or focusing one marks its row.
 * Long list: past FOLD_REFS values it folds after its first sentences (foldAnswer); the whole checked
 * text is one click away, and an arrow onto a folded value unfolds it. */
function Answer({ reading, hot, onHot, rowId }: { reading: Reading; hot: number | null;
                                                  onHot: (row: number | null, focus: boolean) => void;
                                                  rowId: (n: number) => string }) {
  // One tab stop per answer (Test 40 on every engine; CI, 18.10): the value last focused, else the first.
  // Arrows, Home and End move between the values of this answer
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  const focusNext = useRef<number | null>(null);   // a folded value to focus once it is shown
  const box = useRef<HTMLParagraphElement | null>(null);
  const folded = useMemo(() => foldAnswer(reading.answer_segments ?? []), [reading.answer_segments]);
  const segments = reading.answer_segments ?? [];

  const focusRef = (n: number) => box.current?.querySelectorAll<HTMLButtonElement>("button.ref")[n]?.focus();
  useEffect(() => {
    if (focusNext.current === null) return;
    focusRef(focusNext.current);
    focusNext.current = null;
  }, [open]);

  if (!segments.length) return <p className={ANSWER} data-testid="answer">{reading.answer}</p>;
  const shown = folded && !open ? folded : segments;
  const total = segments.filter((s) => s.ref).length;
  const visible = shown.filter((s) => s.ref).length;

  function move(e: React.KeyboardEvent<HTMLButtonElement>, at: number) {
    const to = { ArrowRight: at + 1, ArrowDown: at + 1, ArrowLeft: at - 1, ArrowUp: at - 1, Home: 0,
                 End: total - 1 }[e.key];
    if (to === undefined) return;
    e.preventDefault();
    const next = Math.max(0, Math.min(total - 1, to));
    if (next < visible) {
      focusRef(next);
    } else {   // a folded value is reached by unfolding
      focusNext.current = next;
      setOpen(true);
    }
  }

  // Each value with the closing punctuation right after it, held on one line
  const parts: React.ReactNode[] = [];
  let at = 0;
  for (let i = 0; i < shown.length; i++) {
    const s = shown[i];
    if (!s.ref) {
      parts.push(<span key={i}>{s.text}</span>);
      continue;
    }
    const place = at++;
    const row = s.ref.row;
    const after = shown[i + 1];
    const close = after && !after.ref ? (after.text.match(CLOSING)?.[0] ?? "") : "";
    const value = (
      <button
        type="button"
        className={`ref ${hot === row ? "ref-hot" : ""}`}
        aria-describedby={rowId(row)}
        data-ref-row={row}
        tabIndex={place === Math.min(active, visible - 1) ? 0 : -1}
        onMouseEnter={() => onHot(row, false)}
        onMouseLeave={() => onHot(null, false)}
        onFocus={() => {
          setActive(place);
          onHot(row, true);
        }}
        onBlur={() => onHot(null, false)}
        onKeyDown={(e) => move(e, place)}
      >
        {s.text}
      </button>
    );
    if (close) {
      parts.push(<span key={i} className="whitespace-nowrap">{value}{close}</span>);
      parts.push(<span key={`${i}-rest`}>{after.text.slice(close.length)}</span>);
      i++;
    } else {
      parts.push(<span key={i}>{value}</span>);
    }
  }

  return (
    <>
      <p ref={box} className={ANSWER} data-testid="answer">
        {parts}
        {folded && !open && <span aria-hidden="true"> …</span>}
      </p>
      {folded && (
        <div className="px-5 pb-1">
          <button
            type="button"
            aria-expanded={open}
            data-testid="answer-fold"
            className="min-h-9 rounded-[10px] text-sm font-bold text-brand-dark hover:underline"
            onClick={() => setOpen(!open)}
          >
            {open ? "Show the first sentences ↑" : `Read the full answer (${total} references) ↓`}
          </button>
        </div>
      )}
    </>
  );
}

export function ReadingCard({
  reading: given,
  index = 1,
  onShowAll,
}: {
  reading: Reading;
  index?: number;
  // Phase B task 5: every record of a cut reading, from the server; no model. The answer is unchanged
  onShowAll?: () => Promise<Reading>;
}) {
  const [full, setFull] = useState<Reading | null>(null);
  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const reading: Reading = full
    ? { ...given, rows: full.rows, row_count: full.row_count, total: full.total, notice: full.notice }
    : given;
  const cut = (given.total ?? 0) > (given.rows?.length ?? 0) && (given.rows?.length ?? 0) > 0;
  const [asTable, setAsTable] = useState(false);
  const [unfolded, setUnfolded] = useState(false);
  const [copied, setCopied] = useState(false);
  const [hot, setHot] = useState<number | null>(null);
  const uid = useId();
  const rowId = (n: number) => `${uid}-row-${n}`;
  const tags = isTagReading(reading);
  const grouped = tags ? null : groupRows(reading);
  const rows = reading.rows ?? [];
  const hasRows = rows.length > 0;
  const folds = !tags && !(grouped && !asTable) && rows.length > FOLD_AFTER;
  const notice = shownNotice(reading);
  const shownRows = folds && !unfolded ? rows.slice(0, FOLD_AFTER) : rows;

  function point(row: number | null, focus: boolean) {
    setHot(row);
    if (focus && row !== null && row > FOLD_AFTER) setUnfolded(true);   // a focused value shows its row
  }

  async function showAll() {
    if (!onShowAll) return;
    setFetching(true);
    setFetchError(null);
    try {
      setFull(await onShowAll());
      setUnfolded(true);
    } catch {
      setFetchError("The records could not be fetched.");
    } finally {
      setFetching(false);
    }
  }

  async function copy() {
    const text = answerText(reading);
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <section
      className="overflow-hidden rounded-[20px] border border-line bg-white shadow-card"
      data-testid="reading"
      data-label={reading.label}
    >
      <div className="flex flex-wrap items-center gap-2.5 border-b border-[#F5E9E7] bg-gradient-to-b from-[#FFF7F7] to-white px-5 py-4">
        <span
          aria-hidden="true"
          className={`flex h-7 w-7 items-center justify-center rounded-lg text-[13px] font-extrabold text-white ${
            index % 2 === 1 ? "bg-brand" : "bg-brand-deep"
          }`}
        >
          {index}
        </span>
        <h3 className="m-0 text-[17px] font-extrabold text-ink">{reading.title}</h3>
        <span className="ml-auto rounded-full bg-blush px-2.5 py-1 text-[13px] font-bold text-brand-deep" data-testid="reading-count">
          {readingCount(reading)}
        </span>
        {reading.answer && (
          <button type="button" className={tool} onClick={copy} aria-label={`Copy the answer of reading ${index}`}>
            {copied ? "Copied" : "Copy"}
          </button>
        )}
        {hasRows && (
          <button
            type="button"
            className={tool}
            onClick={() => download(readingCsvName(reading), readingCsv(reading))}
            data-testid="export-csv"
          >
            Export {rows.length === 1 ? "1 row" : `${rows.length} rows`}
          </button>
        )}
      </div>
      {reading.answer && <Answer reading={reading} hot={hot} onHot={point} rowId={rowId} />}
      {reading.answer_segments?.some((s) => s.ref) && (
        <p className="px-5 pb-1 text-xs text-ink-4">Every marked value is filled in by code from a record. Point at one to see its row.</p>
      )}
      {(notice || reading.ordered_by) && (
        <p className="px-5 pb-1.5 pt-2 text-[13px] text-ink-4" data-testid="reading-notice">
          {[notice, hasRows && reading.ordered_by ? `${reading.ordered_by[0].toUpperCase()}${reading.ordered_by.slice(1)}.` : null]
            .filter(Boolean)
            .join(" ")}
        </p>
      )}
      {hasRows && (
        <div className="px-5 pb-1 pt-2">
          {tags ? (
            <Tags reading={reading} hot={hot} onHot={(n) => setHot(n)} rowId={rowId} />
          ) : grouped && !asTable ? (
            <Grouped reading={reading} {...grouped} hot={hot} rowId={rowId} />
          ) : (
            <Table reading={reading} rows={shownRows} hot={hot} rowId={rowId} />
          )}
        </div>
      )}
      {(folds || grouped || (cut && onShowAll && !full)) && (
        <div className="flex flex-wrap gap-2 px-5 pb-3.5 pt-1.5">
          {cut && onShowAll && !full && (
            <button
              type="button"
              className="min-h-10 rounded-[10px] border border-blush-3 px-3.5 text-sm font-bold text-brand-dark hover:bg-blush disabled:opacity-50"
              onClick={showAll}
              disabled={fetching}
              data-testid="show-all-records"
            >
              {fetching ? "Fetching the records…" : `Show all ${given.total} records`}
            </button>
          )}
          {fetchError && (
            <span role="alert" className="self-center text-sm text-brand-deep">
              {fetchError}
            </span>
          )}
          {folds && (
            <button
              type="button"
              aria-expanded={unfolded}
              className="min-h-10 rounded-[10px] px-3.5 text-sm font-bold text-brand-dark hover:bg-blush"
              onClick={() => setUnfolded(!unfolded)}
            >
              {unfolded ? `Show the first ${FOLD_AFTER} rows ↑` : `Show all ${rows.length} rows ↓`}
            </button>
          )}
          {grouped && (
            <button
              type="button"
              className="min-h-10 rounded-[10px] px-3.5 text-sm font-semibold text-ink-3 underline underline-offset-2 hover:text-ink"
              onClick={() => setAsTable(!asTable)}
            >
              {asTable ? "Show grouped" : "Show as table"}
            </button>
          )}
        </div>
      )}
      <div className="flex items-start gap-2.5 border-t border-line-3 bg-[#FAF7F6] px-5 py-3.5 text-[13px] leading-normal text-ink-2">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#991B1B" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="mt-px shrink-0" aria-hidden="true">
          <path d="M9 11l3 3 8-8" />
          <path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9" />
        </svg>
        <span data-testid="examined">{reading.checked}</span>
      </div>
    </section>
  );
}
