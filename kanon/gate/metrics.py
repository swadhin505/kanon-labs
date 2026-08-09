"""Turning per-trial results into numbers you can compare between runs.

Two numbers per slice, and the gap between them is the point:

    pass@1    how often it works           (the optimistic number)
    pass^k    how often it works k times   (the number that survives production)

An agent that succeeds 4 times in 5 looks fine at pass@1 = 0.8 and is exposed at
pass^k = 0.0 for k = 5. Reporting only the first is how "it works on my machine"
becomes an incident.

`pass_hat_k` is the formula from tau2-bench -- three lines, so we copied the
lines rather than the module they live in (which also carries pandas, an auth
classifier and LLM-judge error tagging we have no use for).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from math import comb
from pathlib import Path
from typing import Any

from kanon.gate import invariants as invariant_registry
from kanon.gate.runner import StoryResult
from kanon.gate.scorer import diff
from kanon.gate.trajectory import Message, ToolCall

Slice = tuple[tuple[str, str], ...]


def slice_label(slice_: Slice) -> str:
    return " / ".join(value for _, value in slice_)


def pass_hat_k(trials: int, successes: int, k: int) -> float:
    """Probability that k trials drawn from this run would all have passed."""
    if not 1 <= k <= trials:
        raise ValueError(f"k must be between 1 and trials ({trials}), got {k}")
    if successes < k:
        return 0.0
    return comb(successes, k) / comb(trials, k)


@dataclass(frozen=True)
class EventSummary:
    step: int
    kind: str
    role: str | None = None
    content: str | None = None
    confirms: list[str] = field(default_factory=list)
    operation: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str | None = None
    deterministic: bool | None = None


@dataclass(frozen=True)
class StateChangeSummary:
    resource: str
    id: str
    op: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    fields: dict[str, tuple[Any, Any]]


@dataclass(frozen=True)
class ViolationSummary:
    message: str
    step: int | None


@dataclass(frozen=True)
class InvariantSummary:
    name: str
    description: str
    policy: str | None
    passed: bool
    violations: list[ViolationSummary] = field(default_factory=list)


@dataclass(frozen=True)
class TrialSummary:
    passed: bool
    state_ok: bool
    calls_ok: bool
    invariants_ok: bool
    interaction_ok: bool
    temporal_ok: bool
    outcome: str | None
    reasons: list[str]
    events: list[EventSummary]
    changes: list[StateChangeSummary]
    invariants: list[InvariantSummary]


@dataclass(frozen=True)
class StorySummary:
    id: str
    intent: str
    policy: str
    persona: str
    trials: int
    successes: int
    #: The do-nothing agent passed this story too, so it proves nothing.
    trivially_passed: bool
    #: How many policy rules this story checks. Zero is legal and reported.
    invariants: int
    labels: dict[str, str] = field(default_factory=dict)
    #: Why the first failing trial failed. Empty if every trial passed.
    reasons: list[str] = field(default_factory=list)
    #: Evidence for each attempt. Kept beside the aggregate so the UI can explain why.
    trial_details: list[TrialSummary] = field(default_factory=list)

    @property
    def slice(self) -> Slice:
        labels = self.labels or {
            "intent": self.intent,
            "policy": self.policy,
            "persona": self.persona,
        }
        conventional = ("intent", "policy", "persona")
        ordered = [*conventional, *sorted(set(labels) - set(conventional))]
        return tuple((key, labels[key]) for key in ordered if key in labels)

    @property
    def passed(self) -> bool:
        return self.successes == self.trials


@dataclass(frozen=True)
class SliceMetrics:
    slice: Slice
    stories: int
    pass_at_1: float
    pass_hat_k: float

    @property
    def label(self) -> str:
        return slice_label(self.slice)


@dataclass(frozen=True)
class RunReport:
    """Everything one run produced, in a form that survives to disk."""

    domain: str
    agent: str
    stories: list[StorySummary]
    #: Operations the agent asked for that the pack does not cover. Never empty
    #: silently -- an uncovered operation is a hole in the twin, not a pass.
    uncovered: dict[str, int] = field(default_factory=dict)
    #: Model calls this run cost, when the agent tracks them. Reported because
    #: a run is stories x trials x turns, and that bill is easy to underestimate.
    model_calls: int = 0

    # --- building --------------------------------------------------------

    @classmethod
    def from_results(
        cls,
        domain: str,
        agent: str,
        results: list[StoryResult],
        uncovered: dict[str, int],
        model_calls: int = 0,
    ) -> RunReport:
        summaries = []
        registered = invariant_registry.registered()
        for result in results:
            failed = next((t for t in result.trials if not t.score.passed), None)
            trial_details = []
            for trial in result.trials:
                events = []
                for step, event in enumerate(trial.trajectory.events):
                    if isinstance(event, Message):
                        events.append(
                            EventSummary(
                                step=step,
                                kind="message",
                                role=event.role,
                                content=event.content,
                                confirms=list(event.confirms),
                            )
                        )
                    elif isinstance(event, ToolCall):
                        events.append(
                            EventSummary(
                                step=step,
                                kind="tool_call",
                                operation=event.operation,
                                args=event.args,
                                result=event.result,
                                error=event.error,
                                deterministic=event.operation not in uncovered,
                            )
                        )

                changes = [
                    StateChangeSummary(
                        resource=change.resource,
                        id=change.id,
                        op=change.op,
                        before=change.before,
                        after=change.after,
                        fields=change.fields,
                    )
                    for change in diff(trial.before, trial.after)
                ]
                invariant_details = []
                for name in result.story.invariants:
                    definition = registered[name]
                    violations = [
                        ViolationSummary(item.message, item.step)
                        for item in trial.score.violations
                        if item.rule == name
                    ]
                    invariant_details.append(
                        InvariantSummary(
                            name=name,
                            description=definition.description,
                            policy=definition.policy,
                            passed=not violations,
                            violations=violations,
                        )
                    )
                trial_details.append(
                    TrialSummary(
                        passed=trial.score.passed,
                        state_ok=trial.score.state_ok,
                        calls_ok=trial.score.calls_ok,
                        invariants_ok=trial.score.invariants_ok,
                        interaction_ok=trial.score.interaction_ok,
                        temporal_ok=trial.score.temporal_ok,
                        outcome=trial.score.outcome,
                        reasons=list(trial.score.reasons),
                        events=events,
                        changes=changes,
                        invariants=invariant_details,
                    )
                )
            summaries.append(
                StorySummary(
                    id=result.story.id,
                    intent=result.story.intent,
                    policy=result.story.policy or "-",
                    persona=result.story.persona,
                    trials=len(result.trials),
                    successes=result.successes,
                    trivially_passed=result.trivially_passed,
                    invariants=len(result.story.invariants),
                    labels=dict(result.story.labels),
                    reasons=list(failed.score.reasons) if failed else [],
                    trial_details=trial_details,
                )
            )
        return cls(domain, agent, summaries, dict(uncovered), model_calls)

    # --- metrics ---------------------------------------------------------

    @property
    def trials(self) -> int:
        """Trials per story. Uniform across a run by construction."""
        return max((s.trials for s in self.stories), default=0)

    def slices(self, k: int) -> list[SliceMetrics]:
        """Per-slice metrics, ordered by slice so two runs line up."""
        grouped: dict[Slice, list[StorySummary]] = {}
        for summary in self.stories:
            grouped.setdefault(summary.slice, []).append(summary)

        return [
            SliceMetrics(
                slice=key,
                stories=len(group),
                pass_at_1=_mean(s.successes / s.trials for s in group),
                pass_hat_k=_mean(pass_hat_k(s.trials, s.successes, k) for s in group),
            )
            for key, group in sorted(grouped.items())
        ]

    def aggregate(self, k: int) -> SliceMetrics:
        """The single headline number -- the one that stays flat while a slice dies."""
        return SliceMetrics(
            slice=(("scope", "all"),),
            stories=len(self.stories),
            pass_at_1=_mean(s.successes / s.trials for s in self.stories),
            pass_hat_k=_mean(pass_hat_k(s.trials, s.successes, k) for s in self.stories),
        )

    # --- persistence -----------------------------------------------------

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> RunReport:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            domain=raw["domain"],
            agent=raw["agent"],
            stories=[_load_story(story) for story in raw["stories"]],
            uncovered=raw.get("uncovered", {}),
            model_calls=raw.get("model_calls", 0),
        )


def _mean(values) -> float:
    collected = list(values)
    return sum(collected) / len(collected) if collected else 0.0


def _load_story(raw: dict[str, Any]) -> StorySummary:
    details = []
    for trial in raw.get("trial_details", []):
        details.append(
            TrialSummary(
                passed=trial["passed"],
                state_ok=trial["state_ok"],
                calls_ok=trial["calls_ok"],
                invariants_ok=trial["invariants_ok"],
                interaction_ok=trial.get("interaction_ok", True),
                temporal_ok=trial.get("temporal_ok", True),
                outcome=trial.get("outcome"),
                reasons=trial.get("reasons", []),
                events=[EventSummary(**event) for event in trial.get("events", [])],
                changes=[StateChangeSummary(**change) for change in trial.get("changes", [])],
                invariants=[
                    InvariantSummary(
                        **{
                            **item,
                            "violations": [
                                ViolationSummary(**violation)
                                for violation in item.get("violations", [])
                            ],
                        }
                    )
                    for item in trial.get("invariants", [])
                ],
            )
        )
    return StorySummary(
        id=raw["id"],
        intent=raw["intent"],
        policy=raw["policy"],
        persona=raw["persona"],
        trials=raw["trials"],
        successes=raw["successes"],
        trivially_passed=raw["trivially_passed"],
        invariants=raw["invariants"],
        labels=raw.get("labels", {}),
        reasons=raw.get("reasons", []),
        trial_details=details,
    )
