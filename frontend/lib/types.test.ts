import { describe, expect, it } from "vitest";
import { goldenResponses, schemaProperties } from "./fixtures";
import type { ExchangeResponse, Reading } from "./types";

// Every field the contract can carry, as the screen's types name it. A field added to or removed
// from exchange_response fails here until lib/types.ts follows.
const RESPONSE_KEYS = [
  "outcome",
  "readings",
  "gaps",
  "offer_investigation",
  "understood_as",
  "notice",
  "withheld",
  "ambiguous_terms",
  "request_for_input",
  "unanswered_parts", // task 4, the coverage check
  "referred_parts",
  "replaced",
  "coverage_notice",
] as const satisfies readonly (keyof ExchangeResponse)[];

const READING_KEYS = [
  "label",
  "title",
  "checked",
  "row_count",
  "result",
  "candidates",
  "columns",
  "headers",
  "identifiers",
  "rows",
  "total",
  "entity",
  "ordered_by",
  "not_held",
  "notice",
  "answer",
  "evidence",
  "answer_segments",
] as const satisfies readonly (keyof Reading)[];

describe("the screen's types follow the contract", () => {
  it("names every field of exchange_response and of a reading", () => {
    const { response, reading } = schemaProperties();
    expect([...RESPONSE_KEYS].sort()).toEqual([...response].sort());
    expect([...READING_KEYS].sort()).toEqual([...reading].sort());
  });

  it("every golden response uses only fields the types know", () => {
    for (const [name, r] of Object.entries(goldenResponses())) {
      for (const k of Object.keys(r)) expect(RESPONSE_KEYS, name).toContain(k);
      for (const reading of r.readings) {
        for (const k of Object.keys(reading)) expect(READING_KEYS, name).toContain(k);
        expect(reading.title, name).toBeTruthy();
      }
    }
  });
});
