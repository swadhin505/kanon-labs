"""Deciding whether a run passed, and saying why in words.

Five independent checks, multiplied:

    reward = state x calls x interaction x temporal x invariants

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
from kanon.gate.story import CallExpectation, Change, Op, Outcome, Story
from kanon.gate.trajectory import ToolCall, Trajectory
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
    interaction_ok: bool
    temporal_ok: bool
    #: Which acceptable outcome matched, when the story declares alternatives.
    outcome: str | None
    #: One line per thing that went wrong. Empty when reward is 1.0.
    reasons: list[str]
    #: Structured policy evidence for reports and the scenario drill-in.
    violations: list[Violation]

    @property
    def passed(self) -> bool:
        return self.reward == 1.0


@dataclass(frozen=True)
class _StateResult:
    ok: bool
    reasons: list[str]


def _score_state(expect: list[Change], allow: list[Change], deltas: list[Delta]) -> _StateResult:
    """Match one acceptable outcome against the terminal state diff."""
    unmatched_expectations = [
        pattern for pattern in expect if not any(matches(delta, pattern) for delta in deltas)
    ]
    tolerated = expect + allow
    unmatched_deltas = [
        delta for delta in deltas if not any(matches(delta, pattern) for pattern in tolerated)
    ]
    reasons: list[str] = []

    # A near miss -- the right record, the wrong contents -- is one finding, not
    # two. Reporting "expected X" and "unexpected X" for the same record reads
    # like two separate problems and buries the actual difference.
    near_misses: set[tuple[str, str]] = set()
    for pattern in unmatched_expectations:
        wanted = pattern.model_dump(exclude_none=True)
        near = [
            delta
            for delta in unmatched_deltas
            if delta.resource == pattern.resource and (pattern.id is None or delta.id == pattern.id)
        ]
        if near:
            near_misses.update((delta.resource, delta.id) for delta in near)
            found = "; ".join(delta.describe() for delta in near)
            reasons.append(f"expected {wanted}, but found {found}")
        else:
            reasons.append(f"expected change never happened: {wanted}")

    for delta in unmatched_deltas:
        if (delta.resource, delta.id) not in near_misses:
            reasons.append(f"unexpected change: {delta.describe()}")
    return _StateResult(not unmatched_expectations and not unmatched_deltas, reasons)


def _call_matches(call: ToolCall, expected: CallExpectation) -> bool:
    if call.operation not in expected.operations:
        return False
    if expected.outcome == "success" and not call.ok:
        return False
    if expected.outcome == "error" and call.ok:
        return False
    return all(call.args.get(key) == value for key, value in expected.args.items())


def _score_temporal(
    story: Story, seeded: State, trajectory: Trajectory
) -> tuple[bool, list[str]]:
    if not story.never and not story.ever:
        return True, []

    previous = seeded
    changes: list[tuple[int, Delta]] = []
    missing_evidence = []
    for step, call in trajectory.calls():
        if call.state_after is None:
            missing_evidence.append(step)
            continue
        changes.extend((step, delta) for delta in diff(previous, call.state_after))
        previous = call.state_after

    reasons = []
    if missing_evidence:
        reasons.append(
            "temporal assertions lack state evidence after steps "
            + ", ".join(str(step) for step in missing_evidence)
        )
    for pattern in story.never:
        found = [(step, delta) for step, delta in changes if matches(delta, pattern)]
        for step, delta in found:
            reasons.append(f"forbidden transient change at step {step}: {delta.describe()}")
    for pattern in story.ever:
        if not any(matches(delta, pattern) for _, delta in changes):
            reasons.append(
                f"required transient change never happened: {pattern.model_dump(exclude_none=True)}"
            )
    return not reasons, reasons


def score(
    story: Story,
    seeded: State,
    final: State,
    trajectory: Trajectory,
    *,
    interaction_reasons: list[str] | None = None,
    model_calls: int = 0,
) -> Score:
    deltas = diff(seeded, final)
    reasons: list[str] = []

    # 1. State. Every expectation in one outcome must be met, and nothing else
    # may have moved. Alternative outcomes are OR; each outcome remains strict.
    alternatives = story.outcomes or [Outcome(name="expected", expect=story.expect)]
    state_results = [
        _score_state(outcome.expect, story.allow + outcome.allow, deltas)
        for outcome in alternatives
    ]
    matched = next((index for index, result in enumerate(state_results) if result.ok), None)
    state_ok = matched is not None
    selected_outcome = (
        alternatives[matched].name if matched is not None and story.outcomes else None
    )
    if not state_ok:
        closest = min(
            range(len(state_results)), key=lambda index: len(state_results[index].reasons)
        )
        prefix = f"outcome {alternatives[closest].name!r}: " if story.outcomes else ""
        reasons.extend(prefix + reason for reason in state_results[closest].reasons)

    # 2. Calls. Legacy must_call remains readable; richer constraints cover
    # alternatives, arguments, failures, retries, and cardinality.
    made = {call.operation for _, call in trajectory.calls() if call.ok}
    missing_calls = [operation for operation in story.must_call if operation not in made]
    for operation in missing_calls:
        reasons.append(f"never called {operation}")
    call_failures = list(missing_calls)
    all_calls = [call for _, call in trajectory.calls()]
    for expected in story.calls:
        count = sum(_call_matches(call, expected) for call in all_calls)
        label = expected.operation or f"any of {', '.join(expected.any_of)}"
        if count < expected.minimum:
            reasons.append(f"called {label} {count}x, expected at least {expected.minimum}x")
            call_failures.append(label)
        if expected.maximum is not None and count > expected.maximum:
            reasons.append(f"called {label} {count}x, expected at most {expected.maximum}x")
            call_failures.append(label)
    if story.limits.tool_calls is not None and len(all_calls) > story.limits.tool_calls:
        reasons.append(
            f"used {len(all_calls)} tool calls, limit is {story.limits.tool_calls}"
        )
        call_failures.append("tool_calls")
    if story.limits.model_calls is not None and model_calls > story.limits.model_calls:
        reasons.append(f"used {model_calls} model calls, limit is {story.limits.model_calls}")
        call_failures.append("model_calls")
    calls_ok = not call_failures

    # 3. Interaction completeness. A required stimulus that never ran cannot be
    # reported as policy coverage.
    interaction_reasons = list(interaction_reasons or [])
    reasons.extend(interaction_reasons)
    interaction_ok = not interaction_reasons

    # 4. Temporal assertions inspect each call's state, not only the endpoint.
    temporal_ok, temporal_reasons = _score_temporal(story, seeded, trajectory)
    reasons.extend(temporal_reasons)

    # 5. Policy invariants. A story checks exactly the rules it names -- an empty
    # list checks nothing, rather than quietly running every rule in the
    # registry. Stories with no rules are counted in the coverage report.
    violations = invariant_registry.check(final, trajectory, story.invariants)
    for violation in violations:
        reasons.append(violation.render(trajectory))
    invariants_ok = not violations

    passed = state_ok and calls_ok and interaction_ok and temporal_ok and invariants_ok
    return Score(
        float(passed),
        state_ok,
        calls_ok,
        invariants_ok,
        interaction_ok,
        temporal_ok,
        selected_outcome,
        reasons,
        violations,
    )
