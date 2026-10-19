"use client";

import { useEffect, useId, useRef, useState } from "react";
import { DEMO_USERS, type DemoUserId } from "@/lib/types";

// UI refresh (UserPicker.dc.html): the demo user, with what each role sees. A listbox driven from the
// keyboard: arrows, Home and End move, Enter or Space chooses, Escape closes and returns focus. Demo only,
// no authentication (S2). D6 (18.10): both roles see every record; "my" is the planner's own lines (D7).

export const ROLE_SEES: Record<string, { short: string; long: string }> = {
  Approver: { short: "sees all records", long: "Sees every record" },
  Planner: { short: "sees all records", long: "Sees every record; “my” means the lines they plan" },
};

const ORDER = DEMO_USERS.map((u) => u.id);

function Avatar({ id, small }: { id: string; small?: boolean }) {
  const approver = id.startsWith("AP");
  return (
    <span
      aria-hidden="true"
      className={`flex shrink-0 items-center justify-center font-extrabold ${
        small ? "h-8 w-8 rounded-[9px] text-xs" : "h-9 w-9 rounded-[10px] text-[13px]"
      } ${approver ? "bg-avatar-gradient text-white" : "border border-line bg-[#F5EFEE] text-brand-deep"}`}
    >
      {id.slice(0, 2)}
    </span>
  );
}

export function UserPicker({
  user,
  onUser,
  disabled,
}: {
  user: DemoUserId;
  onUser: (u: DemoUserId) => void;
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(ORDER.indexOf(user));
  const id = useId();
  const button = useRef<HTMLButtonElement | null>(null);
  const list = useRef<HTMLDivElement | null>(null);
  const role = DEMO_USERS.find((u) => u.id === user)?.role ?? "";

  useEffect(() => {
    if (!open) return;
    list.current?.focus();
    function outside(e: MouseEvent) {
      if (!list.current?.contains(e.target as Node) && !button.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, [open]);

  function show() {
    setActive(ORDER.indexOf(user));
    setOpen(true);
  }

  function choose(i: number) {
    setOpen(false);
    onUser(ORDER[i]);
    button.current?.focus();
  }

  function onListKey(e: React.KeyboardEvent) {
    const last = ORDER.length - 1;
    const keys: Record<string, () => void> = {
      ArrowDown: () => setActive((a) => Math.min(last, a + 1)),
      ArrowUp: () => setActive((a) => Math.max(0, a - 1)),
      Home: () => setActive(0),
      End: () => setActive(last),
      Enter: () => choose(active),
      " ": () => choose(active),
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

  const option = (u: (typeof DEMO_USERS)[number], i: number) => {
    const selected = u.id === user;
    return (
      <div
        key={u.id}
        id={`${id}-${u.id}`}
        role="option"
        aria-selected={selected}
        onClick={() => choose(i)}
        onMouseEnter={() => setActive(i)}
        className={`flex cursor-pointer items-center gap-3 rounded-xl px-2.5 py-2 ${
          i === active ? "bg-blush outline outline-2 outline-brand-dark" : selected ? "bg-blush" : ""
        }`}
      >
        <Avatar id={u.id} small={u.role !== "Approver"} />
        <span className="flex flex-1 flex-col">
          <span className="text-sm font-extrabold text-ink">{u.id}</span>
          {u.role === "Approver" && <span className="text-xs text-ink-3">{ROLE_SEES.Approver.long}</span>}
        </span>
        {u.role !== "Approver" && <span className="text-xs text-ink-4">Planner</span>}
        {selected && (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#B91C1C" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M5 12l5 5 9-10" />
          </svg>
        )}
      </div>
    );
  };

  return (
    <div className="relative">
      <button
        ref={button}
        type="button"
        aria-label={`Demo user ${user}, ${role}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? `${id}-list` : undefined}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={(e) => {
          if (!open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
            e.preventDefault();
            show();
          }
        }}
        data-testid="user-picker"
        className={`flex min-h-12 items-center gap-2.5 rounded-[14px] border bg-white py-1.5 pl-1.5 pr-3 text-left disabled:opacity-50 ${
          open ? "border-brand shadow-[0_0_0_4px_rgba(220,38,38,0.15)]" : "border-line shadow-[0_2px_8px_-2px_rgba(28,25,23,0.12)]"
        }`}
      >
        <Avatar id={user} />
        <span className="flex flex-col leading-tight">
          <span className="text-sm font-extrabold text-ink">{user}</span>
          <span className="text-xs text-ink-4">
            {role} · {ROLE_SEES[role]?.short}
          </span>
        </span>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#57534E" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <path d={open ? "M18 15l-6-6-6 6" : "M6 9l6 6 6-6"} />
        </svg>
      </button>
      {open && (
        <div
          ref={list}
          id={`${id}-list`}
          role="listbox"
          tabIndex={-1}
          aria-label="Demo user"
          aria-activedescendant={`${id}-${ORDER[active]}`}
          onKeyDown={onListKey}
          className="absolute right-0 z-30 mt-2 flex w-80 flex-col gap-0.5 rounded-[18px] border border-line bg-white p-2 shadow-pop outline-none"
        >
          <div role="group" aria-label="Approver">
            <div aria-hidden="true" className="px-2.5 pb-1 pt-2 text-xs font-extrabold tracking-[0.08em] text-ink-4">APPROVER</div>
            {DEMO_USERS.filter((u) => u.role === "Approver").map((u) => option(u, ORDER.indexOf(u.id)))}
          </div>
          <div role="group" aria-label="Planners">
            <div aria-hidden="true" className="px-2.5 pb-1 pt-2.5 text-xs font-extrabold tracking-[0.08em] text-ink-4">
              PLANNERS · “MY” MEANS THEIR OWN LINES
            </div>
            {DEMO_USERS.filter((u) => u.role === "Planner").map((u) => option(u, ORDER.indexOf(u.id)))}
          </div>
          <div aria-hidden="true" className="mt-1.5 border-t border-line-3 p-2.5 text-xs text-ink-4">
            Demo · no authentication. Every user sees every record; switching user changes whose lines “my” means.
          </div>
        </div>
      )}
    </div>
  );
}
