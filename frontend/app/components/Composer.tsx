"use client";

import { forwardRef, useState } from "react";

export const MAX_QUESTION = 1000;

/** The question box. While a question runs it is locked (R8): one question at a time, no retry. */
export const Composer = forwardRef<HTMLTextAreaElement, { locked: boolean; onSend: (q: string) => void }>(
  function Composer({ locked, onSend }, ref) {
    const [text, setText] = useState("");
    const question = text.trim();
    const canSend = !locked && question.length > 0 && question.length <= MAX_QUESTION;

    function send() {
      if (!canSend) return;
      onSend(question);
      setText("");
    }

    return (
      <div className="flex flex-col gap-2">
        <form
          className={`flex items-center gap-3 rounded-[20px] border-[1.5px] border-line-2 bg-white py-2 pl-5 pr-2 shadow-composer focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-ink ${
            locked ? "opacity-70" : ""
          }`}
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          <textarea
            ref={ref}
            aria-label="Question"
            className="max-h-40 min-h-10 flex-1 resize-none bg-transparent py-2 text-base text-ink outline-none placeholder:text-ink-4"
            placeholder={locked ? "Waiting for the answer…" : "Ask about suppliers, orders, parts or deliveries"}
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
            className="bg-mark-gradient flex h-12 w-12 shrink-0 items-center justify-center rounded-[14px] shadow-[0_8px_18px_-8px_rgba(185,28,28,0.8),inset_0_1px_0_rgba(255,255,255,0.3)] disabled:bg-none disabled:bg-[#D6D3D1] disabled:shadow-none"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 19V5M5 12l7-7 7 7" />
            </svg>
          </button>
        </form>
        <p className="text-center text-xs text-ink-4">
          Answers come only from records. A cause is never inferred; a question that needs one is offered an investigation.
        </p>
      </div>
    );
  },
);
