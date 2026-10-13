// Test fixtures: the server's own golden responses (backend/tests/golden/snapshots), so the screen is
// tested on what the pipeline really returns, and a change there shows up here.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { ExchangeItem, ExchangeResponse, Reading } from "./types";

const DIR = join(__dirname, "..", "..", "backend", "tests", "golden", "snapshots");

export function goldenResponses(): Record<string, ExchangeResponse> {
  const out: Record<string, ExchangeResponse> = {};
  for (const f of readdirSync(DIR).filter((f) => f.startsWith("response__") && f.endsWith(".json"))) {
    out[f.replace(/^response__|\.json$/g, "")] = JSON.parse(readFileSync(join(DIR, f), "utf-8"));
  }
  return out;
}

export function schemaProperties(): { response: string[]; reading: string[] } {
  const bundle = JSON.parse(
    readFileSync(join(__dirname, "..", "..", "backend", "query_assistance", "medagentpro_schemas.json"), "utf-8"),
  );
  const s = bundle.schemas.exchange_response.schema;
  return { response: Object.keys(s.properties), reading: Object.keys(s.properties.readings.items.properties) };
}

export function item(response: ExchangeResponse, over: Partial<ExchangeItem> = {}): ExchangeItem {
  return { exchange_id: "EX-1", posed: "A question?", recorded: true, response, cost: null, rating: null, ...over };
}

export function reading(over: Partial<Reading> = {}): Reading {
  return { label: "r", title: "Reading 1", checked: "Examined: commitments", row_count: 0, ...over };
}
