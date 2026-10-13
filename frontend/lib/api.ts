// The chat API client. Every request names its demo user in X-Demo-User (S2, no authentication).
// A question is sent once: a new request_id each time, no retry of any kind (R8). The answer arrives
// only in the "result" event, after the gate (decision 8); "stage" events carry progress only.

import { createSseParser } from "./sse";
import type {
  ConversationSummary,
  DemoUserId,
  ExchangeItem,
  Health,
  Rating,
  Stage,
} from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export function newRequestId(): string {
  return crypto.randomUUID().replaceAll("-", "");
}

async function call<T>(user: DemoUserId, path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", "X-Demo-User": user, ...init.headers },
  });
  if (!res.ok) throw new ApiError(res.status, await detail(res));
  return (await res.json()) as T;
}

async function detail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}`;
  }
}

export const api = {
  health: () => fetch(`${API_BASE}/api/health`).then((r) => r.json() as Promise<Health>),

  conversations: (user: DemoUserId) => call<ConversationSummary[]>(user, "/api/conversations"),

  newConversation: (user: DemoUserId) =>
    call<{ conversation_id: string }>(user, "/api/conversations", { method: "POST" }),

  conversation: (user: DemoUserId, id: string) =>
    call<{ conversation_id: string; exchanges: ExchangeItem[] }>(user, `/api/conversations/${id}`),

  rate: (user: DemoUserId, exchangeId: string, rating: Rating) =>
    call<{ rating: Rating }>(user, `/api/exchanges/${exchangeId}/rating`, {
      method: "POST",
      body: JSON.stringify({ rating }),
    }),

  /** One question. Resolves with the result event; rejects on an error event, a refusal, or a stream
   * that ends without a result. Never retried. */
  async ask(
    user: DemoUserId,
    conversationId: string,
    question: string,
    opts: { requestId: string; onStage?: (s: Stage) => void; signal?: AbortSignal },
  ): Promise<Omit<ExchangeItem, "posed" | "rating" | "at">> {
    const res = await fetch(`${API_BASE}/api/conversations/${conversationId}/exchanges`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Demo-User": user },
      body: JSON.stringify({ question, request_id: opts.requestId }),
      signal: opts.signal,
    });
    if (!res.ok || !res.body) throw new ApiError(res.status, await detail(res));

    let result: Omit<ExchangeItem, "posed" | "rating" | "at"> | null = null;
    let error: string | null = null;
    const parser = createSseParser(({ event, data }) => {
      const payload = JSON.parse(data);
      if (event === "stage") opts.onStage?.(payload.stage as Stage);
      else if (event === "result") result = payload;
      else if (event === "error") error = payload.error ?? "The request could not be completed.";
    });
    const reader = res.body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.push(value);
    }
    if (error) throw new ApiError(500, error);
    if (!result) throw new ApiError(0, "The answer did not arrive.");
    return result;
  },
};
