"""Scenarios: what the user wants, and what must be true afterwards.

A story is the unit the whole gate is organised around. It carries its own
slice -- (intent, policy, persona) -- because per-slice results are the point:
an aggregate score staying flat at 67% while one slice collapses to 33% is the
failure this product exists to catch.

Expectations are written against the *diff* between the seeded state and the
final state, not against a golden state dump. Three buckets:

    expect  changes that must happen
    allow   changes that may happen
    (rest)  anything else is forbidden -- default deny

Default-deny is only affordable because the twin is deterministic. Expectations
usually match records by meaningful fields; generated ids are deliberately not
part of confirmation metadata because they depend on the agent's path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Op = Literal["created", "changed", "deleted"]


class Change(BaseModel):
    """A pattern matching one entry in the state diff."""

    model_config = ConfigDict(extra="forbid")

    resource: str
    op: Op | None = None
    id: str | None = None
    #: Field values required on the record after the change. A subset -- fields
    #: not listed are not checked.
    fields: dict[str, Any] = {}


class Confirmation(BaseModel):
    """Structured consent resolved from the call that made a user turn eligible."""

    model_config = ConfigDict(extra="forbid")

    operation: str
    #: Argument on ``after_call`` identifying the record being confirmed.
    id_from: str | None = None


class UserTurn(BaseModel):
    """A user reply emitted after an eligible agent message.

    ``confirms`` is scorer metadata, not text interpretation. The runner derives
    its target from the successful triggering call, so retries and corrections
    cannot shift an id and turn real consent into a false policy accusation.
    """

    model_config = ConfigDict(extra="forbid")

    content: str
    confirms: list[Confirmation] = []
    #: If set, the turn waits until this tool has succeeded and the agent next
    #: speaks. This prevents narration before a decision from receiving a
    #: confirmation intended for the decision itself.
    after_call: str | None = None
    #: A required turn that never becomes eligible makes the trial incomplete,
    #: rather than silently letting a weaker conversation pass.
    required: bool = True

    @model_validator(mode="after")
    def _confirmation_is_anchored(self) -> UserTurn:
        if self.confirms and not self.after_call:
            raise ValueError("a confirming user turn requires after_call")
        return self


class CallExpectation(BaseModel):
    """A path constraint with alternatives, argument matching, and cardinality."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    operation: str | None = None
    any_of: list[str] = []
    args: dict[str, Any] = {}
    outcome: Literal["success", "error", "any"] = "success"
    minimum: int = Field(default=1, alias="min", ge=0)
    maximum: int | None = Field(default=None, alias="max", ge=0)

    @model_validator(mode="after")
    def _check(self) -> CallExpectation:
        if bool(self.operation) == bool(self.any_of):
            raise ValueError("a call expectation requires exactly one of operation or any_of")
        if len(set(self.any_of)) != len(self.any_of):
            raise ValueError("a call expectation has duplicate any_of operations")
        if self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("a call expectation max cannot be below min")
        return self

    @property
    def operations(self) -> tuple[str, ...]:
        return (self.operation,) if self.operation else tuple(self.any_of)


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_calls: int | None = Field(default=None, ge=0)
    model_calls: int | None = Field(default=None, ge=0)


class Fault(BaseModel):
    """One deterministic provider response injected on a numbered call."""

    model_config = ConfigDict(extra="forbid")

    operation: str
    on_call: int = Field(default=1, ge=1)
    status: int = Field(default=503, ge=100, le=599)
    code: str = "injected_failure"
    message: str = "injected provider failure"
    #: For a successful injected response, including deliberately malformed data.
    body: Any = None


class Outcome(BaseModel):
    """One acceptable terminal resolution; a story passes if any outcome matches."""

    model_config = ConfigDict(extra="forbid")

    name: str
    expect: list[Change] = []
    allow: list[Change] = []


class Story(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    intent: str
    persona: str
    policy: str | None = None
    #: What the user is trying to do, in their words. The scripted agent ignores
    #: this; the LLM user simulator will be driven by it.
    goal: str
    #: Facts available to the adaptive user, separate from prose so it need not
    #: invent answers to legitimate clarifying questions.
    knows: dict[str, Any] = {}
    does_not_know: list[str] = []
    #: Multi-turn replies, optionally anchored to a successful tool call.
    user_turns: list[UserTurn] = []

    #: Record upserts applied after the pack seed and before the trial baseline
    #: is captured. The twin validates the resulting world like normal seed data.
    given: dict[str, list[dict[str, Any]]] = {}
    faults: list[Fault] = []

    #: Backward-compatible shorthand for successful calls with min=1. New
    #: stories use ``calls`` for arguments, alternatives, errors, and counts.
    must_call: list[str] = []
    calls: list[CallExpectation] = []
    limits: Limits = Limits()
    invariants: list[str] = []
    expect: list[Change] = []
    allow: list[Change] = []
    outcomes: list[Outcome] = []
    never: list[Change] = []
    ever: list[Change] = []
    #: Conventional intent/policy/persona labels remain, while domains may add
    #: locale, channel, tenant, model, or any other slice dimension.
    labels: dict[str, str] = {}

    @model_validator(mode="after")
    def _check(self) -> Story:
        if self.outcomes and self.expect:
            raise ValueError("use outcomes or expect, not both")
        names = [outcome.name for outcome in self.outcomes]
        if len(set(names)) != len(names):
            raise ValueError("outcome names must be unique within a story")

        conventional = {
            "intent": self.intent,
            "policy": self.policy or "-",
            "persona": self.persona,
        }
        conflicts = [
            key
            for key, value in conventional.items()
            if key in self.labels and self.labels[key] != value
        ]
        if conflicts:
            raise ValueError(f"labels conflict with story fields: {', '.join(conflicts)}")
        if any(not key or not value for key, value in self.labels.items()):
            raise ValueError("label names and values cannot be empty")
        self.labels = {**conventional, **self.labels}
        return self

    @property
    def slice(self) -> tuple[tuple[str, str], ...]:
        conventional = ("intent", "policy", "persona")
        ordered = [*conventional, *sorted(set(self.labels) - set(conventional))]
        return tuple((key, self.labels[key]) for key in ordered)


def load_stories(path: str | Path) -> list[Story]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    stories = [Story.model_validate(item) for item in raw]

    ids = [story.id for story in stories]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate story ids: {sorted(duplicates)}")
    return stories
