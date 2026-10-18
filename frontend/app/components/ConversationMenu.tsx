"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { ConversationSummary } from "@/lib/types";

// UI refresh, phase B (ConversationMenu.dc.html) [demo extension]: rename, pin and archive a conversation.
// A menu driven from the keyboard: arrows, Home and End move, Enter chooses, Escape closes and returns
// focus. Archiving hides the conversation from the list; every exchange stays in the audit record.

export function ConversationMenu({
  conversation,
  onRename,
  onPin,
  onArchive,
  onUnarchive,
  disabled,
  tabbable = true,
}: {
  conversation: ConversationSummary;
  onRename: () => void;
  onPin: (pinned: boolean) => void;
  onArchive: () => void;
  onUnarchive?: () => void; // long list, L5: an archived conversation, shown by "Show archived"
  disabled: boolean;
  tabbable?: boolean; // only the list's current conversation is a tab stop (roving, Chrome.Sidebar)
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const button = useRef<HTMLButtonElement | null>(null);
  const menu = useRef<HTMLDivElement | null>(null);
  const title = conversation.title ?? "New conversation";

  const items: { label: string; act: () => void; danger?: boolean }[] = [
    { label: "Rename", act: onRename },
    { label: conversation.pinned ? "Unpin" : "Pin to top", act: () => onPin(!conversation.pinned) },
    conversation.archived
      ? { label: "Unarchive", act: onUnarchive ?? (() => {}) }
      : { label: "Archive", act: onArchive, danger: true },
  ];

  useEffect(() => {
    if (!open) return;
    menu.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
    function outside(e: MouseEvent) {
      if (!menu.current?.contains(e.target as Node) && !button.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, [open]);

  function choose(act: () => void) {
    setOpen(false);
    act();
  }

  function onKey(e: React.KeyboardEvent) {
    const all = [...(menu.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? [])];
    const at = all.indexOf(document.activeElement as HTMLButtonElement);
    const go = (i: number) => all[(i + all.length) % all.length]?.focus();
    const keys: Record<string, () => void> = {
      ArrowDown: () => go(at + 1),
      ArrowUp: () => go(at - 1),
      Home: () => go(0),
      End: () => go(all.length - 1),
      Escape: () => {
        setOpen(false);
        button.current?.focus();
      },
      Tab: () => setOpen(false),
    };
    const act = keys[e.key];
    if (!act) return;
    if (e.key !== "Tab") e.preventDefault();
    act();
  }

  return (
    <div className="relative shrink-0">
      <button
        ref={button}
        type="button"
        aria-label={`Options for ${title}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? `${id}-menu` : undefined}
        disabled={disabled}
        tabIndex={tabbable || open ? 0 : -1}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => {
          if (!open && e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
          }
        }}
        data-testid="conversation-options"
        className={`flex h-8 w-8 items-center justify-center rounded-lg disabled:opacity-50 ${
          open ? "bg-brand" : "bg-white shadow-[0_1px_3px_rgba(28,25,23,0.12)] hover:bg-canvas"
        }`}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill={open ? "#FFFFFF" : "#57534E"} aria-hidden="true">
          <circle cx="5" cy="12" r="2" />
          <circle cx="12" cy="12" r="2" />
          <circle cx="19" cy="12" r="2" />
        </svg>
      </button>
      {open && (
        <div
          ref={menu}
          id={`${id}-menu`}
          role="menu"
          aria-label={`Options for ${title}`}
          onKeyDown={onKey}
          className="absolute right-0 z-30 mt-1.5 flex w-64 flex-col rounded-2xl border border-line bg-white p-1.5 shadow-pop"
        >
          {items.map((it, i) => (
            <div key={it.label}>
              {it.danger && i > 0 && <div aria-hidden="true" className="mx-1.5 my-1 h-px bg-line-3" />}
              <button
                type="button"
                role="menuitem"
                onClick={() => choose(it.act)}
                className={`flex min-h-11 w-full items-center gap-2.5 rounded-[10px] px-3 text-left text-sm ${
                  it.danger ? "font-bold text-brand-dark hover:bg-blush focus:bg-blush" : "font-semibold text-ink hover:bg-canvas focus:bg-canvas"
                }`}
              >
                {it.label}
              </button>
            </div>
          ))}
          <p className="px-3 pb-2.5 pt-1.5 text-xs leading-snug text-ink-4">
            Archive hides it from this list. Every exchange stays in the audit record.
          </p>
        </div>
      )}
    </div>
  );
}

/** Rename in place: Enter saves, Escape cancels. At most 200 characters, as the server takes. */
export function RenameField({
  initial,
  onSave,
  onCancel,
}: {
  initial: string;
  onSave: (title: string) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState(initial);
  const field = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    field.current?.focus();
    field.current?.select();
  }, []);
  const title = text.trim();
  return (
    <form
      className="flex min-w-0 flex-1 flex-col gap-1.5"
      onSubmit={(e) => {
        e.preventDefault();
        if (title) onSave(title);
      }}
    >
      <input
        ref={field}
        aria-label="Conversation name"
        value={text}
        maxLength={200}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            onCancel();
          }
        }}
        className="min-h-9 rounded-lg border border-line-2 bg-white px-2 text-sm text-ink"
      />
      <div className="flex gap-1.5">
        <button type="submit" disabled={!title} className="min-h-8 rounded-lg bg-brand px-2.5 text-xs font-bold text-white disabled:opacity-50">
          Save
        </button>
        <button type="button" onClick={onCancel} className="min-h-8 rounded-lg border border-line bg-white px-2.5 text-xs font-semibold text-ink-2">
          Cancel
        </button>
      </div>
    </form>
  );
}
