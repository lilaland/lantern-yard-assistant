"""The belief ledger: what the assistant retains, what it rechecks, and why.

SQLite, one file under the state directory. Four kinds of belief:

- ``policy``      a rule the manager chose (how to treat inbound stock, which source wins).
- ``preference``  an ordering or reporting preference (buffer days, SKU, uplift).
- ``assertion``   a fact about the world, stated by a human or read from evidence.
- ``observation`` a detector result at a given dataset revision.

Who may write what is enforced here, not in prompts:

- policy / preference: only ``human:*`` sources; never rechecked against data.
- observation: only ``detector:*`` sources; rechecked every run.
- assertion: human or evidence sources. A newer statement from a source with equal
  or higher authority supersedes; a lower-authority source (evidence against a
  human statement) marks the older belief ``conflicted`` and never overwrites it.

Nothing is deleted. Every change keeps a chain (``supersedes`` / ``conflicts_with``)
that ``why(subject)`` can walk.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

KINDS = ("policy", "preference", "assertion", "observation")
STATUSES = ("active", "superseded", "conflicted")
RECHECKS = ("never", "each_run", "on_revision_change")
DRAFT_STATUSES = ("proposed", "drafted", "awaiting_approval", "approved", "submitting", "unknown",
                  "accepted", "failed", "cancelled")
PENDING_DRAFT_STATUSES = ("drafted", "awaiting_approval", "approved", "submitting", "unknown")

HUMAN_PREFIXES = ("human:",)
EVIDENCE_PREFIXES = ("invoice:", "note:", "count:", "catalog:", "vendor:", "api:", "supplier_document:")
DETECTOR_PREFIXES = ("detector:",)


class LedgerError(ValueError):
    """A write that the ledger rules forbid."""


def authority(source: str) -> int:
    """3 human, 2 evidence record, 1 detector. Anything else is rejected."""
    if source.startswith(HUMAN_PREFIXES):
        return 3
    if source.startswith(EVIDENCE_PREFIXES):
        return 2
    if source.startswith(DETECTOR_PREFIXES):
        return 1
    raise LedgerError(f"unknown source prefix: {source!r}")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


@dataclass(frozen=True)
class Belief:
    id: str
    kind: str
    subject: str
    statement: str
    value: Any
    source: str
    recorded_at: str
    confirmed_at: str
    supersedes: str | None
    conflicts_with: str | None
    status: str
    recheck: str
    revision: int | None

    @property
    def is_human(self) -> bool:
        return authority(self.source) == 3

    def one_line(self) -> str:
        return f"{self.kind:11s} {self.subject:34s} {dumps(self.value):40s} {self.source} ({self.recorded_at[:16]})"


@dataclass(frozen=True)
class Outcome:
    action: str  # added | unchanged | superseded | conflicted
    belief: Belief
    previous: Belief | None = None


@dataclass(frozen=True)
class Draft:
    draft_id: str
    vendor_id: str
    order: dict
    status: str
    created_at: str
    updated_at: str
    order_id: str | None
    last_error: str | None


SCHEMA = """
CREATE TABLE IF NOT EXISTS beliefs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    statement TEXT NOT NULL,
    value_json TEXT NOT NULL,
    source TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    supersedes TEXT,
    conflicts_with TEXT,
    status TEXT NOT NULL,
    recheck TEXT NOT NULL,
    revision INTEGER
);
CREATE INDEX IF NOT EXISTS beliefs_subject ON beliefs (subject, kind, status);
CREATE TABLE IF NOT EXISTS drafts (
    draft_id TEXT PRIMARY KEY,
    vendor_id TEXT NOT NULL,
    order_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    order_id TEXT,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ----- rules ---------------------------------------------------------------

    @staticmethod
    def _check_write(kind: str, source: str, recheck: str | None) -> str:
        if kind not in KINDS:
            raise LedgerError(f"unknown kind {kind!r}")
        auth = authority(source)
        if kind in ("policy", "preference"):
            if auth != 3:
                raise LedgerError(f"{kind} beliefs can only come from a human source, not {source!r}")
            if recheck not in (None, "never"):
                raise LedgerError(f"{kind} beliefs are never rechecked against data")
            return "never"
        if kind == "observation":
            if auth != 1:
                raise LedgerError(f"observations can only be written by detectors, not {source!r}")
            if recheck not in (None, "each_run"):
                raise LedgerError("observations are always rechecked each run")
            return "each_run"
        # assertion
        if auth == 1:
            raise LedgerError("assertions need a human or evidence source, not a detector")
        if recheck is None:
            return "never" if auth == 3 else "on_revision_change"
        if recheck not in RECHECKS:
            raise LedgerError(f"unknown recheck {recheck!r}")
        return recheck

    # ----- writes --------------------------------------------------------------

    def record(self, kind: str, subject: str, statement: str, value: Any, source: str,
               recheck: str | None = None, revision: int | None = None) -> Outcome:
        """Record a belief, applying the supersede / conflict rules against the current one."""
        recheck = self._check_write(kind, source, recheck)
        value_json = dumps(value)
        current = self._current(kind, subject)
        now = _now()
        active = next((b for b in current if b.status == "active"), None)
        if active is not None and dumps(active.value) == value_json and active.source == source:
            self.db.execute("UPDATE beliefs SET confirmed_at=?, revision=COALESCE(?, revision) WHERE id=?",
                            (now, revision, active.id))
            self.db.commit()
            return Outcome("unchanged", self.get(active.id), None)

        new_id = uuid.uuid4().hex[:12]
        action = "added"
        supersedes = conflicts_with = None
        if active is not None:
            if kind != "assertion" or authority(source) >= authority(active.source):
                action = "superseded"
                supersedes = active.id
                for b in current:  # also closes any earlier conflicted copies
                    self.db.execute("UPDATE beliefs SET status='superseded' WHERE id=?", (b.id,))
            else:
                action = "conflicted"
                conflicts_with = active.id
                self.db.execute("UPDATE beliefs SET status='conflicted' WHERE id=?", (active.id,))
        self.db.execute(
            "INSERT INTO beliefs (id, kind, subject, statement, value_json, source, recorded_at, confirmed_at, "
            "supersedes, conflicts_with, status, recheck, revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id, kind, subject, statement, value_json, source, now, now, supersedes, conflicts_with,
             "active", recheck, revision))
        self.db.commit()
        return Outcome(action, self.get(new_id), active)

    def supersede(self, old_id: str, statement: str, value: Any, source: str,
                  recheck: str | None = None, revision: int | None = None) -> Outcome:
        old = self.get(old_id)
        if old is None:
            raise LedgerError(f"no belief {old_id}")
        return self.record(old.kind, old.subject, statement, value, source, recheck, revision)

    # ----- reads ---------------------------------------------------------------

    def _row(self, r: sqlite3.Row) -> Belief:
        return Belief(r["id"], r["kind"], r["subject"], r["statement"], json.loads(r["value_json"]), r["source"],
                      r["recorded_at"], r["confirmed_at"], r["supersedes"], r["conflicts_with"], r["status"],
                      r["recheck"], r["revision"])

    def _current(self, kind: str, subject: str) -> list[Belief]:
        rows = self.db.execute("SELECT * FROM beliefs WHERE kind=? AND subject=? AND status IN ('active','conflicted') "
                               "ORDER BY recorded_at", (kind, subject)).fetchall()
        return [self._row(r) for r in rows]

    def get(self, belief_id: str) -> Belief | None:
        r = self.db.execute("SELECT * FROM beliefs WHERE id=?", (belief_id,)).fetchone()
        return self._row(r) if r else None

    def active(self, kind: str | None = None, subject: str | None = None) -> list[Belief]:
        sql, args = "SELECT * FROM beliefs WHERE status='active'", []
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if subject:
            sql += " AND subject=?"
            args.append(subject)
        sql += " ORDER BY kind, subject, recorded_at"
        return [self._row(r) for r in self.db.execute(sql, args)]

    def one(self, kind: str, subject: str) -> Belief | None:
        rows = self.active(kind, subject)
        return rows[-1] if rows else None

    def value(self, kind: str, subject: str, default: Any = None) -> Any:
        b = self.one(kind, subject)
        return b.value if b is not None else default

    def conflicts(self) -> list[tuple[Belief, Belief | None]]:
        """(older conflicted belief, the newer active belief that disagrees with it)."""
        out = []
        for r in self.db.execute("SELECT * FROM beliefs WHERE status='conflicted' ORDER BY recorded_at"):
            old = self._row(r)
            newer = self.db.execute("SELECT * FROM beliefs WHERE conflicts_with=? AND status='active'", (old.id,)).fetchone()
            out.append((old, self._row(newer) if newer else None))
        return out

    def why(self, subject: str) -> list[Belief]:
        """Every belief ever recorded about a subject, oldest first, any status."""
        rows = self.db.execute("SELECT * FROM beliefs WHERE subject=? ORDER BY recorded_at, rowid", (subject,))
        return [self._row(r) for r in rows]

    def all(self, include_closed: bool = False) -> list[Belief]:
        sql = "SELECT * FROM beliefs" + ("" if include_closed else " WHERE status='active'")
        return [self._row(r) for r in self.db.execute(sql + " ORDER BY kind, subject, recorded_at")]

    def counts(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {k: {s: 0 for s in STATUSES} for k in KINDS}
        for r in self.db.execute("SELECT kind, status, COUNT(*) AS n FROM beliefs GROUP BY kind, status"):
            out[r["kind"]][r["status"]] = r["n"]
        return out

    # ----- meta ----------------------------------------------------------------

    def meta(self, key: str, default: str | None = None) -> str | None:
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value))
        self.db.commit()

    # ----- drafts --------------------------------------------------------------

    def save_draft(self, draft_id: str, vendor_id: str, order: dict, status: str) -> Draft:
        if status not in DRAFT_STATUSES:
            raise LedgerError(f"unknown draft status {status!r}")
        now = _now()
        self.db.execute("INSERT INTO drafts (draft_id, vendor_id, order_json, status, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?)", (draft_id, vendor_id, dumps(order), status, now, now))
        self.db.commit()
        return self.draft(draft_id)

    def update_draft(self, draft_id: str, status: str, order_id: str | None = None,
                     last_error: str | None = None) -> Draft:
        if status not in DRAFT_STATUSES:
            raise LedgerError(f"unknown draft status {status!r}")
        self.db.execute("UPDATE drafts SET status=?, updated_at=?, order_id=COALESCE(?, order_id), last_error=? "
                        "WHERE draft_id=?", (status, _now(), order_id, last_error, draft_id))
        self.db.commit()
        return self.draft(draft_id)

    def _draft_row(self, r: sqlite3.Row) -> Draft:
        return Draft(r["draft_id"], r["vendor_id"], json.loads(r["order_json"]), r["status"], r["created_at"],
                     r["updated_at"], r["order_id"], r["last_error"])

    def draft(self, draft_id: str) -> Draft | None:
        r = self.db.execute("SELECT * FROM drafts WHERE draft_id=?", (draft_id,)).fetchone()
        return self._draft_row(r) if r else None

    def drafts(self, statuses: Iterable[str] | None = None) -> list[Draft]:
        rows = self.db.execute("SELECT * FROM drafts ORDER BY created_at").fetchall()
        out = [self._draft_row(r) for r in rows]
        return [d for d in out if statuses is None or d.status in statuses]

    def pending_drafts(self) -> list[Draft]:
        return self.drafts(PENDING_DRAFT_STATUSES)
