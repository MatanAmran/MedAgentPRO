"use client";

import { useState } from "react";
import { formatCell, groupRows, readingCsv, readingCsvName } from "@/lib/present";
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

function Table({ reading, rows }: { reading: Reading; rows: Record<string, Cell>[] }) {
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
            <tr key={i} data-row={i + 1}>
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

function Grouped({ reading, by, groups }: { reading: Reading } & NonNullable<ReturnType<typeof groupRows>>) {
  const ids = reading.identifiers ?? [];
  const rest = (reading.columns ?? []).filter((c) => c !== by);
  return (
    <div className="flex flex-col gap-2" data-testid="reading-groups">
      {groups.map((g, i) => (
        <div key={i} className="grid grid-cols-[6rem_1fr] items-baseline gap-2" data-testid="reading-group">
          <span className="font-mono font-semibold text-ink">{formatCell(g.key)}</span>
          <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
            {g.rows.map((row, j) => (
              <span key={j} className="inline-flex items-baseline gap-1">
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
            ))}
          </span>
        </div>
      ))}
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

export function ReadingCard({ reading, index = 1 }: { reading: Reading; index?: number }) {
  const [asTable, setAsTable] = useState(false);
  const [unfolded, setUnfolded] = useState(false);
  const [copied, setCopied] = useState(false);
  const grouped = groupRows(reading);
  const rows = reading.rows ?? [];
  const hasRows = rows.length > 0;
  const total = reading.total ?? rows.length;
  const folds = !(grouped && !asTable) && rows.length > FOLD_AFTER;
  const shownRows = folds && !unfolded ? rows.slice(0, FOLD_AFTER) : rows;

  async function copy() {
    if (!reading.answer) return;
    try {
      await navigator.clipboard.writeText(reading.answer);
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
        <span className="ml-auto text-[13px] text-ink-4" data-testid="reading-count">
          {total === 1 ? "1 row" : `${total} rows`}
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
      {reading.answer && <p className="m-0 px-5 pb-1.5 pt-[18px] text-base leading-[1.8] text-ink">{reading.answer}</p>}
      {(reading.notice || reading.ordered_by) && (
        <p className="px-5 pb-1.5 pt-2 text-[13px] text-ink-4" data-testid="reading-notice">
          {[reading.notice, hasRows && reading.ordered_by ? `${reading.ordered_by[0].toUpperCase()}${reading.ordered_by.slice(1)}.` : null]
            .filter(Boolean)
            .join(" ")}
        </p>
      )}
      {hasRows && (
        <div className="px-5 pb-1 pt-2">
          {grouped && !asTable ? <Grouped reading={reading} {...grouped} /> : <Table reading={reading} rows={shownRows} />}
        </div>
      )}
      {(folds || grouped) && (
        <div className="flex flex-wrap gap-2 px-5 pb-3.5 pt-1.5">
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
