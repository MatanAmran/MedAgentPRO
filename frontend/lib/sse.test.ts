import { describe, expect, it } from "vitest";
import { createSseParser, type SseEvent } from "./sse";

const enc = new TextEncoder();

function parse(chunks: Uint8Array[]) {
  const out: SseEvent[] = [];
  const p = createSseParser((e) => out.push(e));
  chunks.forEach((c) => p.push(c));
  return { out, rest: p.rest() };
}

const STREAM =
  'event: stage\ndata: {"stage": "intent"}\n\n' +
  'event: stage\ndata: {"stage": "retrieval"}\n\n' +
  'event: result\ndata: {"response": {"readings": [{"answer": "S-008 – late"}]}}\n\n';

describe("the SSE parser (S1: read with fetch)", () => {
  it("reads every event of a stream in one chunk", () => {
    const { out } = parse([enc.encode(STREAM)]);
    expect(out.map((e) => e.event)).toEqual(["stage", "stage", "result"]);
    expect(JSON.parse(out[2].data).response.readings[0].answer).toBe("S-008 – late");
  });

  it("gives the same events whatever the chunk boundaries, inside a line or a character", () => {
    const bytes = enc.encode(STREAM);
    const whole = parse([bytes]).out;
    for (let cut = 1; cut < bytes.length; cut++) {
      expect(parse([bytes.slice(0, cut), bytes.slice(cut)]).out).toEqual(whole);
    }
    expect(parse([...bytes].map((b) => new Uint8Array([b]))).out).toEqual(whole);
  });

  it("does not dispatch an event the stream cut off", () => {
    const { out, rest } = parse([enc.encode('event: result\ndata: {"partial"')]);
    expect(out).toEqual([]);
    expect(rest).toContain("partial");
  });

  it("reads CRLF line ends and ignores comments", () => {
    const { out } = parse([enc.encode(': keep-alive\r\n\r\nevent: error\r\ndata: {"error": "x"}\r\n\r\n')]);
    expect(out).toEqual([{ event: "error", data: '{"error": "x"}' }]);
  });
});
