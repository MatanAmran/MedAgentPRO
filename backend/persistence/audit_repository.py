"""
MedAgentPro - the single write path (Figure 6, 30_ARCHITECTURE 7.1, 7.2, 7.5, 7.7, 7.9).

AuditRepository.write_event is the only way anything reaches storage. In one
transaction it appends the audit entry, updates the investigation and stores the
step output (NFR 5). It consults the circuit breaker first (NFR 9), chains each
entry to the previous one of its investigation with an HMAC whose key is held
outside the database (3.1.5, NFR 1), and calls the notification hook after the
transaction commits (3.3).
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from shared.circuit_breaker import CircuitBreaker

GENESIS = "0" * 64


@dataclass
class AuditEntry:
    """The fields of 7.2. Absent optional fields are not written."""
    investigation_id: str
    step_ordinal: int
    agent: str
    event_type: str
    role: str
    attempt_number: int = 1
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    on_behalf_of: str | None = None
    reasoning_steps: list | None = None
    evidence_reviewed: list | None = None
    confidence: float | None = None
    status_before: str | None = None
    status_after: str | None = None
    expected_input_type: str | None = None
    superseded_reason: str | None = None
    model_identity: str | None = None       # model and prompt version
    input_tokens: int | None = None         # NFR 3, only where a model was called
    output_tokens: int | None = None
    summary_produced: str | None = None     # only on the summarization step

    def to_record(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if v is not None}
        if self.attempt_number > 1 and not self.superseded_reason:
            raise ValueError("an entry for a repeated attempt must state why it supersedes the earlier one")
        return d


def canonical(record: dict) -> bytes:
    return json.dumps({k: v for k, v in record.items() if k not in ("hash", "_id")},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def chain_hash(key: bytes, record: dict) -> str:
    return hmac.new(key, canonical(record), hashlib.sha256).hexdigest()


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

class Storage(Protocol):
    def transaction(self): ...
    def get_investigation(self, investigation_id: str, tx=None) -> dict | None: ...
    def put_investigation(self, doc: dict, tx) -> None: ...
    def append_audit(self, record: dict, tx) -> None: ...
    def put_step_output(self, investigation_id: str, step_ordinal: int, output: Any, tx) -> None: ...
    def get_step_output(self, investigation_id: str, step_ordinal: int) -> Any: ...
    def audit_trail(self, investigation_id: str) -> list[dict]: ...


class MemoryStorage:
    """All three collections in memory. A transaction works on a copy and commits it whole,
    so a failure inside leaves nothing behind, as a MongoDB transaction does."""

    def __init__(self):
        self.investigations: dict[str, dict] = {}
        self.audit_log: list[dict] = []
        self.step_outputs: dict[tuple[str, int], Any] = {}

    @contextmanager
    def transaction(self):
        tx = {"inv": copy.deepcopy(self.investigations), "log": list(self.audit_log),
              "out": dict(self.step_outputs)}
        yield tx
        self.investigations, self.audit_log, self.step_outputs = tx["inv"], tx["log"], tx["out"]

    def get_investigation(self, investigation_id, tx=None):
        src = tx["inv"] if tx else self.investigations
        d = src.get(investigation_id)
        return copy.deepcopy(d) if d else None

    def put_investigation(self, doc, tx):
        tx["inv"][doc["investigation_id"]] = copy.deepcopy(doc)

    def append_audit(self, record, tx):
        tx["log"].append(copy.deepcopy(record))

    def put_step_output(self, investigation_id, step_ordinal, output, tx):
        tx["out"][(investigation_id, step_ordinal)] = copy.deepcopy(output)

    def get_step_output(self, investigation_id, step_ordinal):
        return copy.deepcopy(self.step_outputs.get((investigation_id, step_ordinal)))

    def audit_trail(self, investigation_id):
        return [copy.deepcopy(r) for r in self.audit_log if r["investigation_id"] == investigation_id]

    def delete_step_outputs(self, investigation_id):
        self.step_outputs = {k: v for k, v in self.step_outputs.items() if k[0] != investigation_id}


class MongoStorage:
    """Deployment storage. Needs a replica set, since MongoDB transactions require one."""

    def __init__(self, client: Any, db: str = "medagentpro"):
        self.client = client
        self.db = client[db]

    @contextmanager
    def transaction(self):
        with self.client.start_session() as s:
            with s.start_transaction():
                yield s

    def get_investigation(self, investigation_id, tx=None):
        return self.db.investigations.find_one({"investigation_id": investigation_id}, {"_id": 0}, session=tx)

    def put_investigation(self, doc, tx):
        self.db.investigations.replace_one({"investigation_id": doc["investigation_id"]}, doc, upsert=True, session=tx)

    def append_audit(self, record, tx):
        self.db.audit_log.insert_one(dict(record), session=tx)

    def put_step_output(self, investigation_id, step_ordinal, output, tx):
        self.db.step_outputs.replace_one({"investigation_id": investigation_id, "step_ordinal": step_ordinal},
                                         {"investigation_id": investigation_id, "step_ordinal": step_ordinal,
                                          "output": output}, upsert=True, session=tx)

    def get_step_output(self, investigation_id, step_ordinal):
        d = self.db.step_outputs.find_one({"investigation_id": investigation_id, "step_ordinal": step_ordinal})
        return d["output"] if d else None

    def audit_trail(self, investigation_id):
        return list(self.db.audit_log.find({"investigation_id": investigation_id}, {"_id": 0}).sort("seq", 1))


# --------------------------------------------------------------------------
# the repository
# --------------------------------------------------------------------------

class ChainBroken(Exception):
    pass


class AuditRepository:
    def __init__(self, storage: Storage, breaker: CircuitBreaker, chain_key: bytes,
                 on_written: Callable[[dict], None] | None = None):
        if not chain_key:
            raise ValueError("the chain key is read from the environment's secrets, never from the database")
        self.storage = storage
        self.breaker = breaker
        self.key = chain_key
        self.on_written = on_written   # the planned connection point for server push (3.3)

    def write_event(self, entry: AuditEntry, investigation_update: dict | None = None,
                    step_output: Any = None) -> dict:
        """One transaction: audit entry, investigation state, step output (NFR 5).
        Raises CircuitOpen without writing when the database is treated as down."""
        return self._write([entry], investigation_update, step_output)[0]

    def write_run(self, entries: list[AuditEntry], run_update: dict | None = None) -> list[dict]:
        """Every entry of a run and its operational record in one transaction (NFR 5). Used by
        a query assistance exchange, which is recorded only once it ends (4.9, Test 23)."""
        if not entries or len({e.investigation_id for e in entries}) != 1:
            raise ValueError("a run is written as entries of one run")
        return self._write(entries, run_update, None)

    def _write(self, entries: list[AuditEntry], update: dict | None, step_output: Any) -> list[dict]:
        records = [e.to_record() for e in entries]
        run_id = entries[0].investigation_id

        def do():
            with self.storage.transaction() as tx:
                inv = self.storage.get_investigation(run_id, tx) or {
                    "investigation_id": run_id, "audit_head": GENESIS, "audit_seq": 0}
                inv.update(update or {})
                for entry, record in zip(entries, records):
                    record["seq"] = inv["audit_seq"] + 1
                    record["prev_hash"] = inv["audit_head"]
                    record["hash"] = chain_hash(self.key, record)
                    self.storage.append_audit(record, tx)
                    if entry.status_after:
                        inv["status"] = entry.status_after
                    inv["audit_head"], inv["audit_seq"] = record["hash"], record["seq"]
                    inv["updated_at"] = record["timestamp"]
                self.storage.put_investigation(inv, tx)
                if step_output is not None:
                    self.storage.put_step_output(run_id, entries[0].step_ordinal, step_output, tx)
            return records

        written = self.breaker.call(do)
        if self.on_written:
            for record in written:
                self.on_written(record)
        return written

    def completed_output(self, investigation_id: str, step_ordinal: int) -> Any:
        """Option C (7.8): a rerun reads a recorded output instead of executing the step again."""
        return self.breaker.call(lambda: self.storage.get_step_output(investigation_id, step_ordinal))

    def verify_chain(self, investigation_id: str) -> int:
        """Recomputes the chain and compares its end with the head held in the investigation,
        so an edited, removed or inserted entry is found, the last one included (7.9)."""
        trail = sorted(self.storage.audit_trail(investigation_id), key=lambda r: r["seq"])
        prev = GENESIS
        for i, r in enumerate(trail, 1):
            if r["seq"] != i:
                raise ChainBroken(f"{investigation_id}: entry {i} missing or reordered")
            if r["prev_hash"] != prev or chain_hash(self.key, r) != r["hash"]:
                raise ChainBroken(f"{investigation_id}: entry {i} altered")
            prev = r["hash"]
        inv = self.storage.get_investigation(investigation_id) or {}
        if inv.get("audit_head", GENESIS) != prev or inv.get("audit_seq", 0) != len(trail):
            raise ChainBroken(f"{investigation_id}: head does not match the last entry")
        return len(trail)
