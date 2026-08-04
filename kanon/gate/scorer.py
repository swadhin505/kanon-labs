"""Deciding whether a run passed, and saying why in words.

Three independent checks, multiplied:

    reward = state_ok x calls_ok x invariants_ok

Multiplicative, so 1.0 means everything held and anything less means zero. No
partial credit, and no LLM anywhere in here -- that is the whole trust argument.

The state check is a real diff, not a hash comparison. tau2 hashes the final
database and compares; that is cheap but it can only ever tell you "different",
which is useless as a failure message and cannot express "this change was
tolerable". We diff, then match each change against the story's expectations,
and anything unmatched is collateral damage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kanon.gate import invariants as invariant_registry
from kanon.gate.invariants import Violation
from kanon.gate.story import Change, Op, Story
from kanon.gate.trajectory import Trajectory
from kanon.twin.store import Record, State


@dataclass(frozen=True)
class Delta:
    """One record that differs between the seeded state and the final state."""

    resource: str
    id: str
    op: Op
    before: Record | None
    after: Record | None
    #: field -> (before, after), for op == "changed"
    fields: dict[str, tuple[Any, Any]]

    def describe(self) -> str:
        if self.op == "changed":
            edits = ", ".join(f"{k}: {b!r} -> {a!r}" for k, (b, a) in sorted(self.fields.items()))
            return f"{self.resource} {self.id} changed ({edits})"
        if self.op == "created" and self.after:
            values = ", ".join(f"{k}={v!r}" for k, v in sorted(self.after.items()))
            return f"{self.resource} {self.id} created ({values})"
        return f"{self.resource} {self.id} {self.op}"


def diff(before: State, after: State) -> list[Delta]:
    """Every difference between two states, ordered so runs are comparable."""
    deltas: list[Delta] = []
    for resource in sorted(set(before) | set(after)):
        old = before.get(resource, {})
        new = after.get(resource, {})
        for record_id in sorted(set(old) | set(new)):
            was, now = old.get(record_id), new.get(record_id)
            if was == now:
                continue
            if was is None:
                deltas.append(Delta(resource, record_id, "created", None, now, {}))
            elif now is None:
                deltas.append(Delta(resource, record_id, "deleted", was, None, {}))
            else:
                edits = {
                    key: (was.get(key), now.get(key))
                    for key in sorted(set(was) | set(now))
                    if was.get(key) != now.get(key)
                }
                deltas.append(Delta(resource, record_id, "changed", was, now, edits))
    return deltas


def matches(delta: Delta, pattern: Change) -> bool:
    if pattern.resource != delta.resource:
        return False
    if pattern.op is not None and pattern.op != delta.op:
        return False
    if pattern.id is not None and pattern.id != delta.id:
        return False
    record = delta.after if delta.after is not None else (delta.before or {})
    return all(record.get(key) == value for key, value in pattern.fields.items())


@dataclass(frozen=True)
class Score:
    reward: float
    state_ok: bool
    calls_ok: bool
    invariants_ok: bool
    #: One line per thing that went wrong. Empty when reward is 1.0.
    reasons: list[str]
    #: Structured policy evidence for reports and the scenario drill-in.
    violations: list[Violation]

    @property
    def passed(self) -> bool:
        return self.reward == 1.0


def score(story: Story, seeded: State, final: State, trajectory: Trajectory) -> Score:
    deltas = diff(seeded, final)
    reasons: list[str] = []

    # 1. State. Every expectation must be met, and nothing else may have moved.
    unmatched_expectations = [
        pattern for pattern in story.expect if not any(matches(d, pattern) for d in deltas)
    ]
    tolerated = story.expect + story.allow
    unmatched_deltas = [d for d in deltas if not any(matches(d, p) for p in tolerated)]

    # A near miss -- the right record, the wrong contents -- is one finding, not
    # two. Reporting "expected X" and "unexpected X" for the same record reads
    # like two separate problems and buries the actual difference.
    near_misses: set[tuple[str, str]] = set()
    for pattern in unmatched_expectations:
        wanted = pattern.model_dump(exclude_none=True)
        near = [
            d
            for d in unmatched_deltas
            if d.resource == pattern.resource and (pattern.id is None or d.id == pattern.id)
        ]
        if near:
            near_misses.update((d.resource, d.id) for d in near)
            found = "; ".join(d.describe() for d in near)
            reasons.append(f"expected {wanted}, but found {found}")
        else:
            reasons.append(f"expected change never happened: {wanted}")

    collateral = [d for d in unmatched_deltas if (d.resource, d.id) not in near_misses]
    for delta in collateral:
        reasons.append(f"unexpected change: {delta.describe()}")
    state_ok = not unmatched_expectations and not unmatched_deltas

    # 2. Calls. A refused call does not count as having called it.
    made = {call.operation for _, call in trajectory.calls() if call.ok}
    missing_calls = [operation for operation in story.must_call if operation not in made]
    for operation in missing_calls:
        reasons.append(f"never called {operation}")
    calls_ok = not missing_calls

    # 3. Policy invariants. A story checks exactly the rules it names -- an empty
    # list checks nothing, rather than quietly running every rule in the
    # registry. Stories with no rules are counted in the coverage report.
    violations = invariant_registry.check(final, trajectory, story.invariants)
    for violation in violations:
        reasons.append(violation.render(trajectory))
    invariants_ok = not violations

    passed = state_ok and calls_ok and invariants_ok
    return Score(float(passed), state_ok, calls_ok, invariants_ok, reasons, violations)
