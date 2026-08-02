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

Default-deny is only affordable because the twin is deterministic: ids are
counted and timestamps come from a logical clock, so an expected record's exact
id is predictable. That is the payoff for banning uuids and wall clocks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

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


class UserTurn(BaseModel):
    """A user reply emitted after an eligible agent message.

    ``confirms`` is scorer metadata, not text interpretation. A scenario author
    can mark ``pay_claim:CLM-0002`` as explicitly confirmed without asking a
    model or a keyword matcher to guess what the sentence meant.
    """

    model_config = ConfigDict(extra="forbid")

    content: str
    confirms: list[str] = []
    #: If set, the turn waits until this tool has succeeded and the agent next
    #: speaks. This prevents narration before a decision from receiving a
    #: confirmation intended for the decision itself.
    after_call: str | None = None

    @model_validator(mode="after")
    def _confirmation_is_anchored(self) -> UserTurn:
        if self.confirms and not self.after_call:
            raise ValueError("a confirming user turn requires after_call")
        return self


class Story(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    intent: str
    persona: str
    policy: str | None = None
    #: What the user is trying to do, in their words. The scripted agent ignores
    #: this; the LLM user simulator will be driven by it.
    goal: str
    #: Multi-turn replies, optionally anchored to a successful tool call.
    user_turns: list[UserTurn] = []

    #: Operations that must have been called successfully at least once. This is
    #: the trajectory half of the check, and it is what stops a read-only story
    #: from being passed by an agent that does nothing.
    must_call: list[str] = []
    invariants: list[str] = []
    expect: list[Change] = []
    allow: list[Change] = []

    @property
    def slice(self) -> tuple[str, str, str]:
        return (self.intent, self.policy or "-", self.persona)


def load_stories(path: str | Path) -> list[Story]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    stories = [Story.model_validate(item) for item in raw]

    ids = [story.id for story in stories]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate story ids: {sorted(duplicates)}")
    return stories
