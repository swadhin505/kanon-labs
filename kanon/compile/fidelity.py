"""The fidelity gate: does the twin agree with the real API about what is allowed?

An inferred state machine can pass validation and still be wrong. This is the only
strong defence, and it works by replaying recorded call sequences and comparing
**accept vs refuse**, plus any stable response fields explicitly recorded.

Comparing complete bodies would be theatre: ids and clocks intentionally differ,
and real providers return fields outside the agent's scope. A trace can therefore
carry an optional ``expect`` subset for stable business fields while the lifecycle
gate always compares accept-vs-refuse:

    the real API accepted it, the twin refuses  -> the pack is too strict
    the real API refused it, the twin accepts   -> the pack is too permissive,
                                                   which is the dangerous direction

Traces are sequences, not single calls, because a transition can only be reached by
getting there: capture-after-authorise passes, capture-twice must not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from kanon.twin import Pack, Twin, TwinError

Outcome = Literal["ok", "refused"]


class TraceCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: str
    args: dict[str, Any] = {}
    #: What the real API did. `refused` covers any 4xx/409 -- we compare the
    #: decision, not the status code, because the twin's error catalogue is a
    #: separate concern from its state machine.
    outcome: Outcome = "ok"
    #: Optional: the error code expected, when the recording captured one.
    error: str | None = None
    #: Optional stable subset of the real response. Dynamic ids/timestamps can
    #: simply be omitted rather than weakening the lifecycle comparison.
    expect: Any = None


class Trace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    calls: list[TraceCall]


@dataclass(frozen=True)
class Mismatch:
    trace: str
    step: int
    operation: str
    expected: str
    actual: str

    def describe(self) -> str:
        return (
            f"{self.trace} step {self.step} ({self.operation}): "
            f"real API {self.expected}, twin {self.actual}"
        )


@dataclass
class Fidelity:
    checked: int = 0
    mismatches: list[Mismatch] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches

    def summary(self) -> str:
        if not self.checked:
            return "no traces to replay -- the twin's state machine is unverified"
        verdict = "agrees" if self.ok else f"DISAGREES on {len(self.mismatches)}"
        return f"{self.checked} recorded calls replayed, twin {verdict}"


def load_traces(path: str | Path) -> list[Trace]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    return [Trace.model_validate(item) for item in raw]


def check_fidelity(pack: Pack, traces: list[Trace]) -> Fidelity:
    """Replay each trace against a fresh twin and compare the decisions."""
    result = Fidelity()

    for trace in traces:
        twin = Twin(pack)
        for step, call in enumerate(trace.calls):
            result.checked += 1
            if call.operation not in pack.routes:
                result.mismatches.append(
                    Mismatch(
                        trace.name,
                        step,
                        call.operation,
                        "a covered operation",
                        "uncovered (unsupported_operation)",
                    )
                )
                break
            actual_result: Any = None
            try:
                actual_result = twin.call(call.operation, dict(call.args))
                actual, code = "accepted", None
            except TwinError as refusal:
                actual, code = "refused", refusal.code

            expected = "accepted" if call.outcome == "ok" else "refused"
            if actual != expected:
                result.mismatches.append(
                    Mismatch(
                        trace.name,
                        step,
                        call.operation,
                        expected,
                        f"{actual} ({code})" if code else actual,
                    )
                )
                # Stop this trace: every later step is now running against the
                # wrong state, so further mismatches would be noise.
                break
            if call.error and code != call.error:
                result.mismatches.append(
                    Mismatch(
                        trace.name, step, call.operation, f"refused {call.error}", f"refused {code}"
                    )
                )
                break
            if call.expect is not None and not _contains(actual_result, call.expect):
                result.mismatches.append(
                    Mismatch(
                        trace.name,
                        step,
                        call.operation,
                        f"accepted with response matching {_short(call.expect)}",
                        f"accepted with {_short(actual_result)}",
                    )
                )
                break

    return result


def _contains(actual: Any, expected: Any) -> bool:
    """Recursive subset match for stable fields selected by a recording."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_contains(got, wanted) for got, wanted in zip(actual, expected, strict=True))
        )
    return actual == expected


def _short(value: Any, limit: int = 160) -> str:
    rendered = repr(value)
    return rendered if len(rendered) <= limit else rendered[: limit - 3] + "..."
