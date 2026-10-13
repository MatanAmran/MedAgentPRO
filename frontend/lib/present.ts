// What the screen derives from a response, by structure only: no word of model text is read here.

import type { Cell, ExchangeResponse, Reading } from "./types";

export interface Group {
  key: Cell;
  rows: Record<string, Cell>[];
}

/** Q3: group by the first identifier column, only where the table has another identifier column and
 * some value of the first repeats across rows. Otherwise null, and the reading is shown as a table.
 * Groups keep the order in which their key first appears, so the ordering (ordered_by) holds. */
export function groupRows(reading: Reading): { by: string; groups: Group[] } | null {
  const ids = reading.identifiers ?? [];
  const rows = reading.rows ?? [];
  if (ids.length < 2 || rows.length < 2) return null;
  const by = ids[0];
  const groups = new Map<string, Group>();
  for (const row of rows) {
    const k = JSON.stringify(row[by] ?? null);
    let g = groups.get(k);
    if (!g) groups.set(k, (g = { key: row[by] ?? null, rows: [] }));
    g.rows.push(row);
  }
  if (![...groups.values()].some((g) => g.rows.length > 1)) return null;
  return { by, groups: [...groups.values()] };
}

/** Q4: the sentence above more than one reading. The term is named only where exactly one was
 * declared (the server passes only spans of the question as posed); otherwise the fixed sentence. */
export function readingsSentence(response: ExchangeResponse): string | null {
  const n = response.readings.length;
  if (response.outcome !== "answered" || n < 2) return null;
  const terms = response.ambiguous_terms ?? [];
  if (terms.length === 1) return `“${terms[0]}” can be read in ${n} ways, so each is shown.`;
  return `The question can be read in ${n} ways, so each is shown.`;
}

export function formatCell(v: Cell): string {
  if (v === null) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return v;
}

export function formatUsd(v: number): string {
  return `$${v.toFixed(v < 0.1 ? 4 : 2)}`;
}
