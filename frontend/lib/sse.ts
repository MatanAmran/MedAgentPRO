// A server-sent events parser for a stream read with fetch (S1: not EventSource, which cannot send a
// body or a header, and reconnects on its own, which R8 forbids). Chunks may split an event, a line,
// or a multi-byte character anywhere; the parser keeps the remainder for the next chunk.

export interface SseEvent {
  event: string;
  data: string;
}

export function createSseParser(onEvent: (e: SseEvent) => void) {
  const decoder = new TextDecoder();
  let buffer = "";

  function dispatch(block: string) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line === "" || line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (data.length) onEvent({ event, data: data.join("\n") });
  }

  return {
    push(chunk: Uint8Array) {
      buffer += decoder.decode(chunk, { stream: true }).replace(/\r\n?/g, "\n");
      let end: number;
      while ((end = buffer.indexOf("\n\n")) !== -1) {
        dispatch(buffer.slice(0, end));
        buffer = buffer.slice(end + 2);
      }
    },
    /** Whatever is left when the stream ends. An unterminated event is not dispatched. */
    rest(): string {
      return buffer + decoder.decode();
    },
  };
}
