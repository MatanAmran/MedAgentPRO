"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, newRequestId } from "@/lib/api";
import type { ConversationSummary, DemoUserId, ExchangeItem, Rating, Stage } from "@/lib/types";
import { Header, Sidebar } from "./Chrome";
import { Composer } from "./Composer";
import { ExchangeView, Pending } from "./ExchangeView";

interface PendingState {
  conversationId: string;
  question: string;
  stage: Stage | null;
  error: string | null;
}

function message(e: unknown): string {
  if (e instanceof ApiError) return e.status === 0 ? e.message : `${e.message}`;
  return "The server could not be reached.";
}

export function Chat() {
  const [user, setUser] = useState<DemoUserId>("AP-01");
  const [mode, setMode] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [items, setItems] = useState<ExchangeItem[]>([]);
  const [pending, setPending] = useState<PendingState | null>(null);
  const [ratings, setRatings] = useState<Record<string, { busy: boolean; error: string | null }>>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);

  // R8: locked from the moment a question is sent until its exchange ends, one way or another
  const locked = pending !== null && pending.error === null;

  const loadConversations = useCallback(async (u: DemoUserId) => {
    const list = await api.conversations(u);
    setConversations([...list].reverse());
    return list;
  }, []);

  const loadConversation = useCallback(async (u: DemoUserId, id: string) => {
    const c = await api.conversation(u, id);
    setItems(c.exchanges);
    return c.exchanges;
  }, []);

  useEffect(() => {
    api.health().then((h) => setMode(h.mode), () => setLoadError("The chat server could not be reached."));
    return () => abort.current?.abort();
  }, []);

  useEffect(() => {
    setCurrent(null);
    setItems([]);
    setPending(null);
    loadConversations(user).then(
      () => setLoadError(null),
      (e) => setLoadError(message(e)),
    );
  }, [user, loadConversations]);

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: "end" });
  }, [items, pending]);

  async function select(id: string) {
    setCurrent(id);
    setPending(null);
    setItems([]);
    try {
      await loadConversation(user, id);
    } catch (e) {
      setLoadError(message(e));
    }
  }

  async function startNew() {
    setPending(null);
    try {
      const { conversation_id } = await api.newConversation(user);
      setCurrent(conversation_id);
      setItems([]);
      await loadConversations(user);
    } catch (e) {
      setLoadError(message(e));
    }
  }

  async function send(question: string) {
    if (locked) return;
    let id = current;
    setPending({ conversationId: id ?? "", question, stage: null, error: null });
    try {
      if (!id) {
        id = (await api.newConversation(user)).conversation_id;
        setCurrent(id);
      }
      const before = items.length;
      const conversationId = id;
      const ac = new AbortController();
      abort.current = ac;
      try {
        await api.ask(user, conversationId, question, {
          requestId: newRequestId(), // R8: a new id for every question, never reused
          signal: ac.signal,
          onStage: (stage) => setPending((p) => (p ? { ...p, conversationId, stage } : p)),
        });
        setPending(null);
      } catch (e) {
        if (ac.signal.aborted) return;
        // the server may have recorded the exchange anyway (it ends every exchange itself); the
        // conversation as the server holds it is what is shown
        const after = await loadConversation(user, conversationId).catch(() => null);
        setPending(after && after.length > before ? null : { conversationId, question, stage: null, error: message(e) });
        await loadConversations(user).catch(() => undefined);
        return;
      }
      await loadConversation(user, conversationId);
      await loadConversations(user);
    } catch (e) {
      setPending({ conversationId: id ?? "", question, stage: null, error: message(e) });
    }
  }

  async function rate(exchangeId: string, rating: Rating) {
    setRatings((r) => ({ ...r, [exchangeId]: { busy: true, error: null } }));
    try {
      await api.rate(user, exchangeId, rating);
      setItems((xs) => xs.map((x) => (x.exchange_id === exchangeId ? { ...x, rating } : x)));
      setRatings((r) => ({ ...r, [exchangeId]: { busy: false, error: null } }));
    } catch (e) {
      setRatings((r) => ({ ...r, [exchangeId]: { busy: false, error: message(e) } }));
    }
  }

  return (
    <div className="flex h-screen flex-col bg-white">
      <Header user={user} onUser={setUser} mode={mode} locked={locked} />
      <div className="flex min-h-0 flex-1">
        <Sidebar conversations={conversations} current={current} onSelect={select} onNew={startNew} locked={locked} />
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 overflow-y-auto px-8 py-6">
            <div className="mx-auto flex max-w-3xl flex-col gap-8" data-testid="thread">
              {loadError && (
                <p className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-red-800" role="alert">
                  {loadError}
                </p>
              )}
              {items.map((item) => (
                <ExchangeView
                  key={item.exchange_id}
                  item={item}
                  onRate={(r) => rate(item.exchange_id, r)}
                  rating={ratings[item.exchange_id] ?? { busy: false, error: null }}
                />
              ))}
              {pending && <Pending question={pending.question} stage={pending.stage} error={pending.error} />}
              <div ref={bottom} />
            </div>
          </div>
          <div className="mx-auto w-full max-w-3xl px-8 pb-6">
            <Composer locked={locked} onSend={send} />
          </div>
        </main>
      </div>
    </div>
  );
}
