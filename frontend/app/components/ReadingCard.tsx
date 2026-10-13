"use client";

import { useState } from "react";
import { formatCell, groupRows } from "@/lib/present";
import type { Cell, Reading } from "@/lib/types";

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-block rounded-md border border-zinc-200 bg-zinc-100 px-2 py-0.5 font-mono text-sm text-zinc-800">
      {children}
    </span>
  );
}

function Value({ column, value, ids }: { column: string; value: Cell; ids: string[] }) {
  return ids.includes(column) && value !== null ? <Tag>{formatCell(value)}</Tag> : <>{formatCell(value)}</>;
}

function Table({ reading }: { reading: Reading }) {
  const cols = reading.columns ?? [];
  const ids = reading.identifiers ?? [];
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm" data-testid="reading-table">
        <thead>
          <tr className="border-b border-zinc-200 text-zinc-500">
            {cols.map((c) => (
              <th key={c} className="py-1 pr-4 font-medium">
                {reading.headers?.[c] ?? c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {(reading.rows ?? []).map((row, i) => (
            <tr key={i} className="border-b border-zinc-100 last:border-0">
              {cols.map((c) => (
                <td key={c} className="py-1.5 pr-4">
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
          <span className="font-mono font-semibold">{formatCell(g.key)}</span>
          <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
            {g.rows.map((row, j) => (
              <span key={j} className="inline-flex items-baseline gap-1">
                {rest.map((c) =>
                  ids.includes(c) ? (
                    <Value key={c} column={c} value={row[c] ?? null} ids={ids} />
                  ) : (
                    <span key={c} className="text-xs text-zinc-400">
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

export function ReadingCard({ reading }: { reading: Reading }) {
  const [asTable, setAsTable] = useState(false);
  const grouped = groupRows(reading);
  const hasRows = (reading.rows?.length ?? 0) > 0;
  return (
    <section className="rounded-xl border border-zinc-200 bg-white p-4" data-testid="reading" data-label={reading.label}>
      <h3 className="mb-2 text-base font-semibold text-zinc-900">{reading.title}</h3>
      {reading.answer && <p className="mb-3 text-zinc-900">{reading.answer}</p>}
      {(reading.notice || reading.ordered_by) && (
        <p className="mb-2 text-sm text-zinc-500" data-testid="reading-notice">
          {[reading.notice, hasRows && reading.ordered_by ? `${reading.ordered_by[0].toUpperCase()}${reading.ordered_by.slice(1)}.` : null]
            .filter(Boolean)
            .join(" ")}
        </p>
      )}
      {hasRows && (grouped && !asTable ? <Grouped reading={reading} {...grouped} /> : <Table reading={reading} />)}
      {grouped && (
        <button
          type="button"
          className="mt-2 text-sm text-zinc-500 underline underline-offset-2 hover:text-zinc-800"
          onClick={() => setAsTable(!asTable)}
        >
          {asTable ? "Show grouped" : "Show as table"}
        </button>
      )}
      <p className="mt-3 border-t border-zinc-200 pt-2 text-sm text-zinc-500" data-testid="examined">
        {reading.checked}
      </p>
    </section>
  );
}
