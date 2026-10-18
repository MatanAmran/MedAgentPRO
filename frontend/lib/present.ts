// What the screen derives from a response, by structure only: no word of model text is read here.

import type { Cell, ExchangeItem, ExchangeResponse, Reading } from "./types";

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

// --------------------------------------------------------------------------
// UI refresh (18.10): copy, export and the "How the agent worked" row, from the response's structure only
// --------------------------------------------------------------------------

function csvField(v: Cell): string {
  if (v === null) return "";
  const s = String(v);
  return /[",\r\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
}

/** The reading's rows as shown, under the headers shown. Never more than the screen holds; how many of
 * how many is in the file name, not in the file (chat, 18.10). */
export function readingCsv(reading: Reading): string {
  const cols = reading.columns ?? [];
  const lines = [cols.map((c) => csvField(reading.headers?.[c] ?? c)).join(",")];
  for (const row of reading.rows ?? []) lines.push(cols.map((c) => csvField(row[c] ?? null)).join(","));
  return lines.join("\r\n") + "\r\n";
}

/** What Copy copies: the answer as shown. With segments, their joined text, which is the answer (phase C). */
export function answerText(reading: Reading): string | null {
  if (reading.answer_segments?.length) return reading.answer_segments.map((s) => s.text).join("");
  return reading.answer ?? null;
}

export function readingCsvName(reading: Reading): string {
  const shown = reading.rows?.length ?? 0;
  const total = reading.total ?? shown;
  const label = reading.label.replace(/[^A-Za-z0-9_-]+/g, "_");
  return `${label}_${shown}_of_${total}.csv`;
}

export interface AgentStep {
  label: string;
}

/** The steps an answered exchange took, counted from what the screen holds: the readings and their rows,
 * and, where the server sent a trace (--panel), the claims the gate checked and the model's time. */
export function agentSteps(item: ExchangeItem): { steps: AgentStep[]; seconds: number | null } {
  const r = item.response;
  const steps: AgentStep[] = [];
  if (r.understood_as) steps.push({ label: "Read the follow-up" });
  const n = r.readings.length;
  if (n > 0) {
    steps.push({ label: n === 1 ? "Planned 1 reading" : `Planned ${n} readings` });
    const rows = r.readings.reduce((sum, x) => sum + (x.row_count ?? 0), 0);
    steps.push({ label: rows === 1 ? "Retrieved 1 row" : `Retrieved ${rows} rows` });
  }
  if (r.readings.some((x) => x.answer)) steps.push({ label: "Drafted with references" });
  const gate = (item.trace?.calls ?? []).filter((c) => c.stage === "support_gate" && c.status === "accepted");
  if (gate.length > 0) {
    const claims = gate.reduce((sum, c) => sum + (c.claims ?? 0), 0);
    steps.push({ label: claims === 1 ? "Checked 1 claim" : `Checked ${claims} claims` });
  }
  const ms = item.trace?.calls.map((c) => c.duration_ms).filter((v): v is number => v !== null) ?? [];
  return { steps, seconds: item.trace && ms.length > 0 ? Math.round(ms.reduce((a, b) => a + b, 0) / 100) / 10 : null };
}
