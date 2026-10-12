# Demo data

**Demo only.** A synthetic supply-chain store for demonstrating the query assistance
(chat) pipeline until the company data structure arrives. It is not the placeholder in
`query_assistance/schema.json`, which the compiler tests rely on and which stays as it is.

| File | What it is |
|---|---|
| `schema.json` | Tables, columns, identifiers, entitlement per role, and metrics |
| `relations.json` | The joins the model may name |
| `seed.py` | Builds `demo.db`, deterministically (same seed, same file) |
| `demo.db` | Generated, not committed |

Build it, from `backend/`:

```powershell
python -m demo_data.seed
```

## What is in it

About 40 suppliers, 150 parts, 1,000 purchase orders, 2,000 order lines and a year of
weekly forecast. 2026, data cut off on 9 October.

Suppliers differ in behaviour: reliable, occasionally late, chronically late, promising late
and keeping it, promising on time and missing it. So the two readings of "late"
(`late_by_delivery`, `late_by_promise`) return different suppliers on the same question.

Columns that exist but are not always filled, so the demo shows record-level gaps:
`commitments.promised_date`, `purchase_orders.notes`, `parts.bin_location`,
`commitments.delay_reason`.

`delay_reason` is a reason a person recorded in the company system. It is filled on under
10 percent of late lines, so most "why" questions find nothing recorded and are referred to
an investigation. Where one is recorded, the chat quotes it as recorded, never as a cause
it established.

Edge cases: a supplier with no orders (S-040), parts with no forecast, zero stock, stock below
the reorder point, cancelled orders, partial and short receipts, closed orders with no receipt
held, open lines past due.

## Roles

`PLANNER` sees only the orders and lines whose `planner_id` is the user's (PL-01 to PL-05).
`APPROVER` sees everything. Suppliers, parts, receipts and forecast are open to both.
