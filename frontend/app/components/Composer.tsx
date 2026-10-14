"use client";

import { useState } from "react";

export const MAX_QUESTION = 1000;

/** The question box. While a question runs it is locked (R8): one question at a time, no retry. */
export function Composer({ locked, onSend }: { locked: boolean; onSend: (q: string) => void }) {
  const [text, setText] = useState("");
  const question = text.trim();
  const canSend = !locked && question.length > 0 && question.length <= MAX_QUESTION;

  function send() {
    if (!canSend) return;
    onSend(question);
    setText("");
  }

  return (
    <form
      className="flex items-end gap-2 rounded-2xl border border-zinc-400 bg-white p-2 pl-4 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-zinc-900"
      onSubmit={(e) => {
        e.preventDefault();
        send();
      }}
    >
      <textarea
        aria-label="Question"
        className="max-h-40 min-h-10 flex-1 resize-none bg-transparent py-2 text-zinc-900 outline-none placeholder:text-zinc-500"
        placeholder="Ask a question about the data"
        rows={1}
        maxLength={MAX_QUESTION}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            send();
          }
        }}
      />
      <button
        type="submit"
        aria-label="Send"
        disabled={!canSend}
        className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-zinc-900 text-lg text-white disabled:opacity-30"
      >
        ↑
      </button>
    </form>
  );
}
