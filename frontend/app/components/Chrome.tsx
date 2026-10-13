"use client";

import { DEMO_USERS, type ConversationSummary, type DemoUserId } from "@/lib/types";

export function Header({
  user,
  onUser,
  mode,
  locked,
}: {
  user: DemoUserId;
  onUser: (u: DemoUserId) => void;
  mode: string | null;
  locked: boolean;
}) {
  const role = DEMO_USERS.find((u) => u.id === user)?.role;
  return (
    <header className="flex items-center justify-between border-b-4 border-red-700 bg-white px-6 py-3">
      <div className="flex items-center gap-3">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-red-700">
          <span className="h-3.5 w-3.5 rounded-sm bg-white" />
        </span>
        <span className="text-2xl font-bold tracking-tight text-zinc-900">MedAgentPro</span>
        {mode && (
          <span className="ml-2 rounded bg-zinc-100 px-2 py-0.5 text-xs text-zinc-500" data-testid="mode">
            {mode === "real" ? "real model" : "scripted model"}
          </span>
        )}
      </div>
      <div className="flex items-center gap-3 text-zinc-500">
        <span className="hidden sm:inline">Supply chain</span>
        <span className="hidden text-zinc-300 sm:inline">|</span>
        <span className="font-semibold text-zinc-900">{role}</span>
        <label className="flex flex-col items-end">
          <select
            aria-label="Demo user"
            className="rounded-full border-2 border-zinc-900 px-2 py-1 font-semibold text-zinc-900 disabled:opacity-50"
            value={user}
            disabled={locked}
            onChange={(e) => onUser(e.target.value as DemoUserId)}
          >
            {DEMO_USERS.map((u) => (
              <option key={u.id} value={u.id}>
                {u.id}
              </option>
            ))}
          </select>
          <span className="mt-0.5 text-[10px] uppercase tracking-wide text-zinc-400">demo, no authentication</span>
        </label>
      </div>
    </header>
  );
}

export function Sidebar({
  conversations,
  current,
  onSelect,
  onNew,
  locked,
}: {
  conversations: ConversationSummary[];
  current: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  locked: boolean;
}) {
  return (
    <nav className="flex w-72 shrink-0 flex-col gap-4 border-r border-zinc-200 bg-stone-100 p-4">
      <button
        type="button"
        onClick={onNew}
        disabled={locked}
        className="rounded-lg border-2 border-zinc-900 bg-white py-2 font-semibold text-zinc-900 disabled:opacity-50"
      >
        + New conversation
      </button>
      <div className="text-sm font-semibold text-zinc-500">Conversations</div>
      <ul className="flex flex-col gap-1" data-testid="conversations">
        {conversations.map((c) => (
          <li key={c.conversation_id}>
            <button
              type="button"
              disabled={locked}
              onClick={() => onSelect(c.conversation_id)}
              aria-current={c.conversation_id === current ? "page" : undefined}
              className={`w-full rounded-lg px-3 py-2 text-left text-zinc-900 disabled:opacity-60 ${
                c.conversation_id === current ? "border-2 border-zinc-900 bg-white font-semibold" : "hover:bg-white"
              }`}
            >
              {c.title ?? "New conversation"}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
