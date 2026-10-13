"""
MedAgentPro - synthetic supply-chain store for the query assistance demo.

DEMO ONLY. The company inventory has not been received, so this builds a plausible
SQLite store against demo_data/schema.json. It is deterministic: the same seed gives
the same file, so a demo question asked twice meets the same records.

What it is meant to show, without scripting any one answer:
  - suppliers that differ in behaviour: reliable, occasionally late, chronically late,
    late against the requested date but on their own promise, and the reverse
  - the two readings of "late" diverging on real rows (late_by_delivery vs late_by_promise)
  - record-level gaps: columns that exist but are not always filled
    (promised_date, notes, bin_location, delay_reason), closed orders with no receipt held
  - a recorded delay reason only on a small share of late lines, so most "why" questions
    still have nothing recorded and go to an investigation
  - edge cases: a supplier with no orders, parts with no forecast, zero stock,
    cancelled orders, partial and short receipts, open lines past due

Usage (from backend/):
    python -m demo_data.seed                 # writes demo_data/demo.db
    python -m demo_data.seed --out x.db --seed 7
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
SCHEMA = HERE / "schema.json"
DEFAULT_OUT = HERE / "demo.db"

# The data ends here. Lines due after it are still open.
DATA_CUTOFF = date(2026, 10, 9)
YEAR_START = date(2026, 1, 5)

SQLITE_TYPES = {"text": "TEXT", "integer": "INTEGER", "real": "REAL", "date": "TEXT", "boolean": "INTEGER"}

PLANNERS = ["PL-01", "PL-02", "PL-03", "PL-04", "PL-05"]

REGIONS = {
    "EMEA": ["Germany", "Ireland", "Netherlands", "Israel", "Italy", "Czech Republic"],
    "AMER": ["United States", "Mexico", "Costa Rica", "Canada"],
    "APAC": ["China", "Malaysia", "Japan", "Singapore", "Taiwan"],
}

SUPPLIER_NAMES = [
    "Apex Polymers", "BioFlex Tubing", "Carden Medical Metals", "Delta Precision", "Eastgate Electronics",
    "Fairmont Sterile Packaging", "Gemini Coatings", "Halcyon Wire", "Ironbridge Machining", "Juno Cable Assemblies",
    "Kestrel Molding", "Lumen Optics", "Meridian Catheter Components", "Northwind Plastics", "Orion Microelectronics",
    "Pinnacle Springs", "Quarry Ceramics", "Redwood Adhesives", "Summit Fasteners", "Tidewater Labels",
    "Ultra Pure Silicones", "Vantage Sensors", "Westbrook Foils", "Xeno Guidewires", "Yardley Connectors",
    "Zenith Balloons", "Arcadia Steel", "Beacon PCB", "Corvus Resins", "Dynamo Motors",
    "Everest Valves", "Falcon Hypotubes", "Granite Enclosures", "Harbor Sterilization", "Indigo Inks",
    "Jasper Batteries", "Keystone Gaskets", "Lyra Displays", "Monarch Cartons", "Nova Shrink Tubing",
]

CATEGORIES = {
    "Catheter components": ["Balloon", "Catheter shaft", "Tip assembly", "Hub", "Strain relief"],
    "Guidewires": ["Guidewire core", "Coil", "Hypotube"],
    "Tubing": ["PTFE liner", "Pebax extrusion", "Nylon tubing", "Shrink tube"],
    "Electronics": ["Sensor board", "Connector", "Cable assembly", "Microcontroller", "Display module"],
    "Mechanical": ["Spring", "Fastener", "Valve", "Housing", "Gasket"],
    "Packaging": ["Tyvek pouch", "Carton", "Label roll", "Tray"],
    "Consumables": ["Adhesive", "Coating", "Silicone lubricant", "Ink"],
}

DELAY_REASONS = [
    "Raw material shortage at supplier",
    "Supplier capacity constraint",
    "Customs hold",
    "Quality hold at supplier",
    "Carrier delay",
    "Tooling repair",
]

NOTES = [
    "Expedite requested by planner",
    "Split shipment agreed with supplier",
    "Price confirmed for the quarter",
    "Certificate of conformance required with shipment",
    "Supplier asked to confirm the date by email",
    "Replaces a cancelled order",
    "Quantity increased after forecast review",
    "First article inspection required",
]


# --------------------------------------------------------------------------
# supplier behaviour
# --------------------------------------------------------------------------

# share of lines delivered after the requested date / promised after it / missing the promise
PROFILES = {
    "reliable":        dict(p_promise_late=0.03, p_miss_promise=0.03, delay=(1, 4)),
    "occasional":      dict(p_promise_late=0.10, p_miss_promise=0.15, delay=(2, 10)),
    "chronic":         dict(p_promise_late=0.35, p_miss_promise=0.45, delay=(5, 25)),
    "honest_late":     dict(p_promise_late=0.55, p_miss_promise=0.05, delay=(1, 5)),   # promises late, keeps it
    "overpromising":   dict(p_promise_late=0.02, p_miss_promise=0.50, delay=(3, 15)),  # promises on time, misses
}


def _profile_plan(n: int, rng: random.Random) -> list[str]:
    plan = ["chronic"] * 4 + ["honest_late"] * 3 + ["overpromising"] * 3 + ["occasional"] * 10
    plan += ["reliable"] * (n - len(plan))
    rng.shuffle(plan)
    return plan


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def create_tables(conn: sqlite3.Connection, schema: dict) -> None:
    for table, ent in schema["entities"].items():
        cols = ", ".join(f'"{c}" {SQLITE_TYPES[s["type"]]}' for c, s in ent["columns"].items())
        conn.execute(f'CREATE TABLE "{table}" ({cols})')


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def build(out: Path = DEFAULT_OUT, seed: int = 20261009) -> dict:
    rng = random.Random(seed)  # demo data generator, not security  # nosec B311
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    out = Path(out)
    if out.exists():
        out.unlink()
    conn = sqlite3.connect(out)
    create_tables(conn, schema)

    # suppliers ---------------------------------------------------------
    suppliers = []
    profiles = _profile_plan(len(SUPPLIER_NAMES), rng)
    for i, (name, prof) in enumerate(zip(SUPPLIER_NAMES, profiles, strict=True), start=1):
        region = rng.choice(list(REGIONS))
        suppliers.append(dict(supplier_id=f"S-{i:03d}", name=name, region=region,
                              country=rng.choice(REGIONS[region]), profile=prof))
    # S-040 holds no order at all: a supplier that exists but has nothing to report
    idle = suppliers[-1]["supplier_id"]
    active = [s for s in suppliers if s["supplier_id"] != idle]
    conn.executemany("INSERT INTO suppliers VALUES (?,?,?,?)",
                     [(s["supplier_id"], s["name"], s["region"], s["country"]) for s in suppliers])
    profile_of = {s["supplier_id"]: s["profile"] for s in suppliers}

    # parts -------------------------------------------------------------
    parts = []
    n = 0
    for cat, kinds in CATEGORIES.items():
        for _ in range(rng.randint(18, 24)):
            n += 1
            kind = rng.choice(kinds)
            reorder = rng.choice([20, 50, 100, 200, 500])
            r = rng.random()
            on_hand = 0 if r < 0.05 else rng.randint(0, reorder - 1) if r < 0.20 else rng.randint(reorder, reorder * 6)
            bin_loc = None if rng.random() < 0.15 else \
                f"{rng.choice('ABCDEF')}-{rng.randint(1, 30):02d}-{rng.randint(1, 6)}"
            parts.append(dict(part_id=f"P-{10000 + n * 37}",
                              description=f"{kind} {rng.choice('ABCDEFGHJK')}{rng.randint(10, 99)}",
                              category=cat, planner_id=rng.choice(PLANNERS), on_hand=on_hand,
                              reorder_point=reorder, bin_location=bin_loc,
                              suppliers=rng.sample(active, k=rng.choice([1, 1, 2, 2, 3])),
                              price=round(rng.uniform(0.4, 180.0), 2)))
    conn.executemany("INSERT INTO parts VALUES (?,?,?,?,?,?,?)",
                     [(p["part_id"], p["description"], p["category"], p["planner_id"], p["on_hand"],
                       p["reorder_point"], p["bin_location"]) for p in parts])

    # forecast: weekly demand, except a few parts that have none --------
    no_forecast = {p["part_id"] for p in rng.sample(parts, 6)}
    fc_rows = []
    for p in parts:
        if p["part_id"] in no_forecast:
            continue
        base = rng.randint(5, 120)
        for wk in range(1, 53):
            season = 1.0 + 0.25 * ((wk % 13) / 13)
            fc_rows.append((p["part_id"], wk, max(0, int(rng.gauss(base * season, base * 0.2)))))
    conn.executemany("INSERT INTO forecast VALUES (?,?,?)", fc_rows)

    # purchase orders, lines and receipts --------------------------------
    po_rows, line_rows, rc_rows = [], [], []
    po_n = line_n = rc_n = 0
    order_day = YEAR_START
    while order_day <= DATA_CUTOFF:
        for _ in range(rng.randint(2, 7)):
            sup = rng.choice(active)
            candidates = [p for p in parts if sup in p["suppliers"]] or [rng.choice(parts)]
            po_n += 1
            po_id = f"PO-{87000 + po_n * 3}"
            planner = rng.choice(candidates)["planner_id"]
            status_cancel = rng.random() < 0.03
            note = rng.choice(NOTES) if rng.random() < 0.25 else None
            lines_here = []
            for p in rng.sample(candidates, k=min(len(candidates), rng.choice([1, 1, 2, 2, 3, 4]))):
                line_n += 1
                prof = PROFILES[profile_of[sup["supplier_id"]]]
                qty = rng.choice([10, 20, 25, 50, 100, 200, 250, 500, 1000])
                price = round(p["price"] * rng.uniform(0.95, 1.05), 2)
                due = order_day + timedelta(days=rng.randint(14, 70))
                # promise
                if rng.random() < 0.08:
                    promised = None                                   # supplier never confirmed a date
                elif rng.random() < prof["p_promise_late"]:
                    promised = due + timedelta(days=rng.randint(*prof["delay"]))
                else:
                    promised = due - timedelta(days=rng.choice([0, 0, 0, 1, 2]))
                # delivery against the promise (or the due date where no promise was made)
                anchor = promised or due
                if rng.random() < prof["p_miss_promise"]:
                    delivered = anchor + timedelta(days=rng.randint(*prof["delay"]))
                else:
                    delivered = anchor - timedelta(days=rng.choice([0, 0, 1, 2, 3]))

                lines_here.append(dict(cid=f"C-{100000 + line_n}", part=p, qty=qty, price=price, due=due,
                                       promised=promised, delivered=delivered))

            any_open = False
            for ln in lines_here:
                received_qty, delivered_at, reason = 0, None, None
                receipts = []
                if not status_cancel and ln["delivered"] <= DATA_CUTOFF and rng.random() > 0.02:
                    # received: whole, split in two, or short
                    r = rng.random()
                    if r < 0.10:
                        first = ln["qty"] // 2
                        d2 = min(ln["delivered"] + timedelta(days=rng.randint(3, 15)), DATA_CUTOFF)
                        receipts = [(ln["delivered"], first), (d2, ln["qty"] - first)]
                    elif r < 0.15:
                        receipts = [(ln["delivered"], int(ln["qty"] * rng.uniform(0.6, 0.95)))]
                    else:
                        receipts = [(ln["delivered"], ln["qty"])]
                    received_qty = sum(q for _, q in receipts)
                    delivered_at = max(d for d, _ in receipts)
                    if delivered_at > ln["due"] and rng.random() < 0.07:
                        reason = rng.choice(DELAY_REASONS)            # rarely recorded
                elif not status_cancel and ln["delivered"] <= DATA_CUTOFF:
                    pass                                              # closed below with no receipt held
                if received_qty < ln["qty"] and not status_cancel:
                    any_open = True
                for d, q in receipts:
                    rc_n += 1
                    rc_rows.append((f"R-{500000 + rc_n}", ln["cid"], d.isoformat(), q))
                week = min(ln["due"].isocalendar()[1], 52)
                line_rows.append((ln["cid"], po_id, sup["supplier_id"], ln["part"]["part_id"], planner, week,
                                  ln["qty"], ln["price"], round(ln["qty"] * ln["price"], 2),
                                  ln["due"].isoformat(), _iso(ln["promised"]), _iso(delivered_at),
                                  received_qty, reason))

            if status_cancel:
                status = "cancelled"
            elif any_open:
                # a line received short is closed by the buyer once it is clearly final
                status = "open" if any(ln["delivered"] > DATA_CUTOFF - timedelta(days=30) for ln in lines_here) \
                    else rng.choice(["open", "closed"])
            else:
                status = "closed"
            po_rows.append((po_id, sup["supplier_id"], planner, order_day.isoformat(), status, note))
        order_day += timedelta(days=rng.choice([1, 1, 1, 2]))

    conn.executemany("INSERT INTO purchase_orders VALUES (?,?,?,?,?,?)", po_rows)
    conn.executemany("INSERT INTO commitments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", line_rows)
    conn.executemany("INSERT INTO receipts VALUES (?,?,?,?)", rc_rows)
    conn.commit()
    counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]  # table names from schema.json, quoted  # nosec B608
              for t in schema["entities"]}
    conn.close()
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--seed", type=int, default=20261009)
    a = ap.parse_args()
    counts = build(a.out, a.seed)
    print(f"wrote {a.out}")
    for t, c in counts.items():
        print(f"  {t:16} {c}")


if __name__ == "__main__":
    main()
