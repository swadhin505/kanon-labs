"""Record store for the twin.

Deterministic by construction: ids come from a per-resource counter (never a
uuid), listing is ordered, and there is no clock and no RNG anywhere in here.
Snapshot/restore is a deepcopy of the whole store -- cheap at test-suite sizes
and impossible to get subtly wrong.

# ponytail: whole-store deepcopy. If a twin ever holds enough records for that
# to show up in a profile, switch to a copy-on-write overlay per snapshot.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

Record = dict[str, Any]
#: The whole world, as the scorer and the invariants see it.
State = dict[str, dict[str, Record]]

_ID_TAIL = re.compile(r"(\d+)$")


def _id_tail(record_id: str) -> int:
    """Trailing integer of an id (``CLM-0007`` -> 7), or 0 if it has none."""
    match = _ID_TAIL.search(record_id)
    return int(match.group(1)) if match else 0


@dataclass(frozen=True)
class Snapshot:
    """An opaque, self-contained copy of a store's state."""

    records: dict[str, dict[str, Record]]
    counters: dict[str, int]
    step: int


class MissingRecord(KeyError):
    """Raised by :meth:`Store.require` when an id is not present."""


class Store:
    """``records[resource][id] -> record``, plus id counters and a step counter.

    ``step`` counts writes. It is the twin's logical clock: the engine derives
    timestamps from it, so a run's timestamps depend only on what the agent did.
    """

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Record]] = {}
        self.counters: dict[str, int] = {}
        self.step: int = 0

    # --- setup -----------------------------------------------------------

    def load(self, resource: str, id_field: str, seed: list[Record]) -> None:
        """Replace a resource's contents with `seed` and re-derive its counter.

        The counter continues past the seeded ids, so a freshly created record
        can never collide with a seeded one.
        """
        table = {str(record[id_field]): copy.deepcopy(record) for record in seed}
        self.records[resource] = table
        self.counters[resource] = max((_id_tail(i) for i in table), default=0)

    # --- reads (always copies; callers cannot reach into the store) -------

    def get(self, resource: str, record_id: str) -> Record | None:
        found = self.records.get(resource, {}).get(str(record_id))
        return copy.deepcopy(found) if found is not None else None

    def require(self, resource: str, record_id: str) -> Record:
        found = self.get(resource, record_id)
        if found is None:
            raise MissingRecord(f"{resource} {record_id!r} not found")
        return found

    def list(self, resource: str) -> list[Record]:
        """All records of `resource`, ordered by id so runs are reproducible."""
        table = self.records.get(resource, {})
        return [copy.deepcopy(table[i]) for i in sorted(table)]

    def state(self) -> dict[str, dict[str, Record]]:
        """The full state, for the scorer to diff. A copy, not a view."""
        return copy.deepcopy(self.records)

    # --- writes ----------------------------------------------------------

    def next_id(self, resource: str, prefix: str, width: int = 4) -> str:
        self.counters[resource] = self.counters.get(resource, 0) + 1
        return f"{prefix}{self.counters[resource]:0{width}d}"

    def put(self, resource: str, record_id: str, record: Record) -> Record:
        self.records.setdefault(resource, {})[str(record_id)] = copy.deepcopy(record)
        self.step += 1
        return copy.deepcopy(record)

    def delete(self, resource: str, record_id: str) -> Record:
        table = self.records.get(resource, {})
        if str(record_id) not in table:
            raise MissingRecord(f"{resource} {record_id!r} not found")
        removed = table.pop(str(record_id))
        self.step += 1
        return removed

    # --- control plane ---------------------------------------------------

    def snapshot(self) -> Snapshot:
        return Snapshot(
            records=copy.deepcopy(self.records),
            counters=dict(self.counters),
            step=self.step,
        )

    def restore(self, snapshot: Snapshot) -> None:
        self.records = copy.deepcopy(snapshot.records)
        self.counters = dict(snapshot.counters)
        self.step = snapshot.step
