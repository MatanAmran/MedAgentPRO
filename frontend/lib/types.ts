// The shapes the chat API returns (backend/api/routes.py) and the exchange response contract
// (backend/query_assistance/medagentpro_schemas.json, "exchange_response"). Written by hand;
// lib/types.test.ts checks them against the server's golden snapshots, so a contract change breaks
// the build here rather than on screen.

export type Outcome =
  | "answered"
  | "referred_to_investigation"
  | "no_supported_assertion"
  | "source_unreachable"
  | "request_not_completed";

export type Cell = string | number | null;

export interface Reading {
  label: string;
  title: string; // [demo extension] Q2: built in code, never model text
  checked: string; // "Examined: ...", on every reading
  row_count: number;
  result?: "rows_returned" | "empty_no_match" | "empty_unknown_entity";
  columns?: string[];
  headers?: Record<string, string>; // [demo extension] the name each column is shown by, from the catalog
  identifiers?: string[]; // [demo extension] Q3: columns that select an identifier
  rows?: Record<string, Cell>[];
  total?: number;
  ordered_by?: string;
  not_held?: { value: string; checked_in: string }[];
  notice?: string;
  answer?: string;
  evidence?: string[];
}

export interface Gap {
  asked: string;
  missing: { kind: string; entity?: string };
  text?: string;
}

export interface ExchangeResponse {
  outcome: Outcome;
  readings: Reading[];
  gaps: Gap[];
  offer_investigation?: boolean;
  understood_as?: string;
  notice?: string;
  withheld?: string;
  ambiguous_terms?: string[]; // [demo extension] Q4
  request_for_input?: { text: string; readings: { label: string; checked: string }[] };
}

export interface Cost {
  exchange_usd: number;
  remaining_usd: number;
}

export type Rating = "up" | "down";

// One item of GET /api/conversations/{id}, and the "result" event of the stream
export interface ExchangeItem {
  exchange_id: string;
  posed: string;
  recorded: boolean;
  response: ExchangeResponse;
  cost: Cost | null;
  at?: string;
  rating: Rating | null;
}

export interface ConversationSummary {
  conversation_id: string;
  created_at: string;
  title: string | null;
  exchanges: number;
}

export interface Health {
  mode: "scripted" | "real";
  model: string;
  authentication: string;
  budget_remaining_usd?: number;
}

export type Stage =
  | "waiting" // T7-B1: the conversation is finishing an earlier question
  | "rewriting"
  | "intent"
  | "retrieval"
  | "synthesis"
  | "gate"
  | "regeneration"
  | "contract";

export const DEMO_USERS = [
  { id: "AP-01", role: "Approver" },
  { id: "PL-01", role: "Planner" },
  { id: "PL-02", role: "Planner" },
  { id: "PL-03", role: "Planner" },
  { id: "PL-04", role: "Planner" },
  { id: "PL-05", role: "Planner" },
] as const;

export type DemoUserId = (typeof DEMO_USERS)[number]["id"];
