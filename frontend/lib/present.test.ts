import { describe, expect, it } from "vitest";
import { reading } from "./fixtures";
import { groupRows, readingsSentence } from "./present";
import type { ExchangeResponse } from "./types";

const ROWS = [
  { supplier_id: "S-031", po_id: "PO-87710" },
  { supplier_id: "S-044", po_id: "PO-88019" },
  { supplier_id: "S-031", po_id: "PO-87954" },
];

describe("Q3: grouping by structure only", () => {
  it("groups by the first identifier where another identifier exists and a value repeats", () => {
    const g = groupRows(reading({ columns: ["supplier_id", "po_id"], identifiers: ["supplier_id", "po_id"], rows: ROWS }));
    expect(g?.by).toBe("supplier_id");
    expect(g?.groups.map((x) => [x.key, x.rows.map((r) => r.po_id)])).toEqual([
      ["S-031", ["PO-87710", "PO-87954"]],
      ["S-044", ["PO-88019"]],
    ]);
  });

  it("keeps the order in which each group first appears (ordered_by holds)", () => {
    const rows = [ROWS[1], ROWS[0], ROWS[2]];
    const g = groupRows(reading({ identifiers: ["supplier_id", "po_id"], rows }));
    expect(g?.groups.map((x) => x.key)).toEqual(["S-044", "S-031"]);
  });

  it("does not group with one identifier column (a count per supplier)", () => {
    const rows = [
      { supplier_id: "S-008", late_count: 4 },
      { supplier_id: "S-008", late_count: 2 },
    ];
    expect(groupRows(reading({ identifiers: ["supplier_id"], rows }))).toBeNull();
  });

  it("does not group where no value of the first identifier repeats", () => {
    expect(groupRows(reading({ identifiers: ["supplier_id", "po_id"], rows: ROWS.slice(0, 2) }))).toBeNull();
  });

  it("does not group without identifiers (an older response)", () => {
    expect(groupRows(reading({ rows: ROWS }))).toBeNull();
  });
});

describe("Q4: the sentence over more than one reading", () => {
  const two = (over: Partial<ExchangeResponse> = {}): ExchangeResponse => ({
    outcome: "answered",
    readings: [reading({ label: "a" }), reading({ label: "b" })],
    gaps: [],
    ...over,
  });

  it("names the one ambiguous term", () => {
    expect(readingsSentence(two({ ambiguous_terms: ["late"] }))).toBe("“late” can be read in 2 ways, so each is shown.");
  });

  it("falls back to the fixed sentence with no term, or several", () => {
    expect(readingsSentence(two())).toBe("The question can be read in 2 ways, so each is shown.");
    expect(readingsSentence(two({ ambiguous_terms: ["late", "open"] }))).toBe(
      "The question can be read in 2 ways, so each is shown.",
    );
  });

  it("says nothing over one reading", () => {
    expect(readingsSentence(two({ readings: [reading()] }))).toBeNull();
  });
});
