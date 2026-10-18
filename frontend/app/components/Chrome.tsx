"use client";

import { useMemo, useState } from "react";
import { formatUsd } from "@/lib/present";
import type { ConversationSummary, DemoUserId } from "@/lib/types";
import { UserPicker } from "./UserPicker";

// UI refresh (Main.dc.html): the header, the demo strip and the conversation list.

export function Header({
  user,
  onUser,
  mode,
  budget,
  locked,
}: {
  user: DemoUserId;
  onUser: (u: DemoUserId) => void;
  mode: string | null;
  budget: number | null;
  locked: boolean;
}) {
  return (
    <>
      <header className="relative z-20 flex flex-wrap items-center gap-x-7 gap-y-3 border-b border-line bg-white/95 px-7 py-3.5 shadow-header">
        <div aria-hidden="true" className="rule-gradient absolute inset-x-0 -bottom-px h-[3px]" />
        <div className="flex items-center gap-3">
          <span className="bg-mark-gradient flex h-[38px] w-[38px] items-center justify-center rounded-[11px] shadow-[0_6px_16px_-4px_rgba(185,28,28,0.55),inset_0_1px_0_rgba(255,255,255,0.35)]">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 3v18M3 12h18" />
              <circle cx="12" cy="12" r="4" />
            </svg>
          </span>
          <span className="flex flex-col leading-tight">
            <span className="text-xl font-extrabold tracking-tight text-ink">MedAgentPro</span>
            <span className="text-xs font-medium text-ink-4">Query assistant</span>
          </span>
          {mode && (
            <span
              className="ml-2 inline-flex items-center gap-1.5 rounded-full border border-blush-3 bg-blush px-2.5 py-1 text-xs font-bold text-brand-dark"
              data-testid="mode"
            >
              <span aria-hidden="true" className="h-[7px] w-[7px] rounded-full bg-brand-hi shadow-[0_0_0_3px_rgba(220,38,38,0.18)]" />
              {mode === "real" ? "Live model" : "Scripted model"}
            </span>
          )}
        </div>
        <span className="rounded-xl border border-line bg-canvas px-3.5 py-1.5 text-sm font-bold text-ink">Supply chain</span>
        <div className="flex-1" />
        {mode === "real" && budget !== null && (
          <span className="flex items-center gap-2 rounded-[10px] border border-line bg-white px-3 py-1.5 text-[13px] text-ink-3" data-testid="budget">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#991B1B" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <rect x="3" y="6" width="18" height="13" rx="2" />
              <path d="M3 10h18" />
            </svg>
            Demo budget <strong className="text-ink">{formatUsd(budget)}</strong> left
          </span>
        )}
        <UserPicker user={user} onUser={onUser} disabled={locked} />
      </header>
      <div className="bg-ink px-7 py-1 text-right text-xs tracking-[0.04em] text-[#E7E5E4]" data-testid="demo-strip">
        DEMO · NO AUTHENTICATION · SYNTHETIC DATA
      </div>
    </>
  );
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** "N questions · 16:19" today, "· 14 Oct" before; counts from the list, which holds no outcome (point 2). */
export function conversationMeta(c: ConversationSummary, now: Date = new Date()): string {
  const at = new Date(c.created_at);
  const n = c.exchanges === 1 ? "1 question" : `${c.exchanges} questions`;
  const when = sameDay(at, now)
    ? at.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })
    : at.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  return `${n} · ${when}`;
}

/** Today and Earlier, each in the order given; a search keeps the conversations whose title holds it. */
export function groupConversations(list: ConversationSummary[], search: string, now: Date = new Date()) {
  const q = search.trim().toLowerCase();
  const kept = q ? list.filter((c) => (c.title ?? "new conversation").toLowerCase().includes(q)) : list;
  return {
    today: kept.filter((c) => sameDay(new Date(c.created_at), now)),
    earlier: kept.filter((c) => !sameDay(new Date(c.created_at), now)),
  };
}

export function Sidebar({
  conversations,
  current,
  onSelect,
  onNew,
  locked,
  renderOptions,
}: {
  conversations: ConversationSummary[];
  current: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  locked: boolean;
  renderOptions?: (c: ConversationSummary) => React.ReactNode;
}) {
  const [search, setSearch] = useState("");
  const groups = useMemo(() => groupConversations(conversations, search), [conversations, search]);

  const item = (c: ConversationSummary) => {
    const selected = c.conversation_id === current;
    return (
      <li
        key={c.conversation_id}
        data-testid="conversation-item"
        className={`relative flex items-start gap-2.5 rounded-[14px] border p-3 ${
          selected ? "border-blush-3 bg-blush shadow-[0_6px_16px_-10px_rgba(185,28,28,0.45)]" : "border-transparent hover:bg-white"
        }`}
      >
        <span aria-hidden="true" className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${selected ? "bg-brand" : "bg-[#D6D3D1]"}`} />
        <button
          type="button"
          disabled={locked}
          onClick={() => onSelect(c.conversation_id)}
          aria-current={selected ? "page" : undefined}
          className="flex min-w-0 flex-1 flex-col gap-1 text-left disabled:opacity-60"
        >
          <span className={`text-sm leading-snug ${selected ? "font-bold text-ink" : "font-semibold text-ink-2"}`}>
            {c.pinned ? "📌︎ " : ""}
            {c.title ?? "New conversation"}
          </span>
          <span className={`text-xs ${selected ? "font-semibold text-brand-dark" : "text-ink-4"}`}>{conversationMeta(c)}</span>
        </button>
        {renderOptions?.(c)}
      </li>
    );
  };

  const group = (label: string, list: ConversationSummary[]) =>
    list.length > 0 && (
      <section className="flex flex-col gap-1.5" aria-label={label}>
        <h2 className="px-2 text-xs font-extrabold tracking-[0.08em] text-ink-4">{label.toUpperCase()}</h2>
        <ul className="flex flex-col gap-1">{list.map(item)}</ul>
      </section>
    );

  return (
    <nav
      aria-label="Conversations"
      className="flex w-80 shrink-0 flex-col gap-4 overflow-y-auto border-r border-line bg-gradient-to-b from-white to-sheet px-4 py-5"
    >
      <button
        type="button"
        onClick={onNew}
        disabled={locked}
        className="bg-brand-gradient flex min-h-12 items-center justify-center gap-2 rounded-[14px] text-[15px] font-bold text-white shadow-button disabled:opacity-50"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
          <path d="M12 5v14M5 12h14" />
        </svg>
        New conversation
      </button>
      <label className="flex min-h-11 items-center gap-2 rounded-xl border border-line bg-canvas px-3 text-sm text-ink-4 focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-ink">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#78716C" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <circle cx="11" cy="11" r="7" />
          <path d="M20 20l-3.5-3.5" />
        </svg>
        <input
          type="search"
          placeholder="Search conversations"
          aria-label="Search conversations"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="min-w-0 flex-1 bg-transparent text-ink outline-none placeholder:text-ink-4"
        />
      </label>
      <div className="flex flex-col gap-4" data-testid="conversations">
        {group("Today", groups.today)}
        {group("Earlier", groups.earlier)}
        {search && groups.today.length + groups.earlier.length === 0 && (
          <p className="px-2 text-sm text-ink-4">No conversation matches.</p>
        )}
      </div>
    </nav>
  );
}
