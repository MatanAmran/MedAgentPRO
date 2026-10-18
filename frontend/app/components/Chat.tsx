"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, newRequestId } from "@/lib/api";
import type { ConversationSummary, DemoUserId, Example, ExchangeItem, Rating, Stage } from "@/lib/types";
import { Header, Sidebar } from "./Chrome";
import { Composer } from "./Composer";
import { Examples } from "./Examples";
import { ExchangeView, Pending } from "./ExchangeView";

interface PendingState {
  conversationId: string;
  question: string;
  seen: Stage[]; // the stages started, in order; one seen again moves to the end (the gate after a redraft)
  followUp: boolean;
  error: string | null;
  stopping?: boolean;
}

// UI refresh: Stop aborts the request; the server then refuses the exchange's next model call (S1) and
// records it as not completed. The conversation is free again once that exchange ends (T7-B1)
export const STOPPED = "Stopped. The question was not answered.";

function message(e: unknown): string {
  if (e instanceof ApiError) return e.status === 0 ? e.message : `${e.message}`;
  return "The server could not be reached.";
}

export function Chat() {
  const [user, setUser] = useState<DemoUserId>("AP-01");
  const [mode, setMode] = useState<string | null>(null);
  const [budget, setBudget] = useState<number | null>(null);
  const [examples, setExamples] = useState<Example[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [items, setItems] = useState<ExchangeItem[]>([]);
  const [pending, setPending] = useState<PendingState | null>(null);
  const [ratings, setRatings] = useState<Record<string, { busy: boolean; error: string | null }>>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);
  const composer = useRef<HTMLTextAreaElement | null>(null);

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
    const last = [...c.exchanges].reverse().find((x) => x.cost);
    if (last?.cost) setBudget(last.cost.remaining_usd);
    return c.exchanges;
  }, []);

  useEffect(() => {
    api.health().then(
      (h) => {
        setMode(h.mode);
        setBudget(h.budget_remaining_usd ?? null);
      },
      () => setLoadError("The chat server could not be reached."),
    );
    api.examples().then(setExamples, () => setExamples([]));
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
    const followUp = items.length > 0;
    setPending({ conversationId: id ?? "", question, seen: [], followUp, error: null });
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
          onStage: (stage) =>
            setPending((p) => (p ? { ...p, conversationId, seen: [...p.seen.filter((s) => s !== stage), stage] } : p)),
        });
        setPending(null);
      } catch (e) {
        if (ac.signal.aborted) {
          setPending((p) => (p ? { ...p, error: STOPPED, stopping: false } : p));
          await loadConversations(user).catch(() => undefined);
          return;
        }
        // the server may have recorded the exchange anyway (it ends every exchange itself); the
        // conversation as the server holds it is what is shown
        const after = await loadConversation(user, conversationId).catch(() => null);
        setPending(after && after.length > before ? null : { conversationId, question, seen: [], followUp, error: message(e) });
        await loadConversations(user).catch(() => undefined);
        return;
      }
      await loadConversation(user, conversationId);
      await loadConversations(user);
    } catch (e) {
      setPending({ conversationId: id ?? "", question, seen: [], followUp, error: message(e) });
    }
  }

  function stop() {
    setPending((p) => (p ? { ...p, stopping: true } : p));
    abort.current?.abort();
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
    <div className="flex h-screen flex-col overflow-hidden bg-canvas">
      <Header user={user} onUser={setUser} mode={mode} budget={budget} locked={locked} />
      <div className="flex min-h-0 flex-1">
        <Sidebar conversations={conversations} current={current} onSelect={select} onNew={startNew} locked={locked} />
        <main className="flex min-w-0 flex-1 flex-col">
          {/* relative: an absolutely placed child (an sr-only caption or status) is placed in the thread, not
              against the page, which otherwise grew a second scrollbar with the panel open */}
          <div className="relative min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-7 py-7">
            <div className="mx-auto flex w-full max-w-[924px] flex-col gap-8" data-testid="thread">
              {loadError && (
                <p className="rounded-xl border border-blush-3 bg-blush px-4 py-3 text-brand-dark" role="alert">
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
              {pending && (
                <Pending
                  question={pending.question}
                  seen={pending.seen}
                  followUp={pending.followUp}
                  error={pending.error}
                  onStop={stop}
                  stopping={pending.stopping ?? false}
                />
              )}
              {/* task 8, A4: the scripted questions on an empty screen, and a follow-up under its question */}
              {items.length === 0 && !pending && (
                <Examples
                  title="Try a question"
                  examples={examples.filter((e) => !e.follows)}
                  onAsk={send}
                  disabled={locked}
                />
              )}
              {items.length > 0 && !pending && (
                <Examples
                  title="Follow up"
                  examples={examples.filter((e) => e.follows === items[items.length - 1].posed)}
                  onAsk={send}
                  disabled={locked}
                />
              )}
              <div ref={bottom} />
            </div>
          </div>
          <div className="bg-gradient-to-b from-transparent to-canvas to-35% px-7 pb-5 pt-3.5">
            <div className="mx-auto w-full max-w-[924px]">
              <Composer ref={composer} locked={locked} onSend={send} />
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
