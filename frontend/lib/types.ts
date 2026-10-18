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
  entity?: string; // [demo extension] L4: the catalog label of the FROM table, plain-column readings only
  ordered_by?: string;
  not_held?: { value: string; checked_in: string }[];
  notice?: string;
  answer?: string;
  // [demo extension] UI refresh C: the answer cut at the references code filled; joined, it is answer
  answer_segments?: AnswerSegment[];
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

export interface AnswerSegment {
  text: string;
  ref?: { alias: string; row: number }; // the 1-based row of this reading's rows; never on free text (NFR 21)
}

export interface Cost {
  exchange_usd: number;
  remaining_usd: number;
}

export type Rating = "up" | "down";

// Task 10 [demo extension]: "How this was answered", one row per model call. Metadata only (api/trace.py)
export interface TraceCall {
  stage: string;
  prompt_identity: string;
  model: string;
  attempt_number: number;
  feedback_attempt: boolean;
  status: "accepted" | "rejected" | "failed";
  finish_reason: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  reasoning_tokens: number | null;
  claims: number | null; // the gate only
  max_tokens: number | null;
  duration_ms: number | null;
  check: string | null; // the name of the check that rejected the output, never its message
  cost_usd: number | null; // null is unknown, never free
}

export interface Trace {
  calls: TraceCall[];
  total: { input_tokens: number | null; output_tokens: number | null; reasoning_tokens: number | null; usd: number | null };
  unpriced_usd: number | null;
  not_sent: "rate_limit" | "budget" | null;
  note: "plan_decided" | "no_records" | null;
}

// One item of GET /api/conversations/{id}, and the "result" event of the stream
export interface ExchangeItem {
  exchange_id: string;
  posed: string;
  recorded: boolean;
  response: ExchangeResponse;
  cost: Cost | null;
  trace?: Trace; // task 10: only where the server runs with --panel
  at?: string;
  rating: Rating | null;
}

export interface ConversationSummary {
  conversation_id: string;
  created_at: string;
  title: string | null;
  exchanges: number;
  pinned?: boolean; // UI refresh, phase B [demo extension]
  archived?: boolean;
}

// UI refresh, phase B: PATCH /api/conversations/{id}. Archiving hides from the list; the record stays
export type ConversationChange = { title?: string; pinned?: boolean; archived?: boolean };

// Task 8, A4: a scripted demo question (GET /api/examples), in the demo's order
export interface Example {
  question: string;
  follows: string | null; // asked after this question, in the same conversation
  outcome: Outcome;
  shows: string[];
}

export interface Health {
  mode: "scripted" | "real";
  model: string;
  authentication: string;
  budget_remaining_usd?: number;
  panel?: boolean; // task 10
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
