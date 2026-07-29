"""What happened during one run: the messages exchanged and the tools called.

One flat, ordered event log rather than separate message and call lists, because
most of the interesting rules are about *order* -- "the agent paid before anyone
confirmed" is a statement about where a message sits relative to a call. A
position in this log is called a **step**, and it is what a violation cites.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "agent"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class ToolCall:
    operation: str
    args: dict[str, Any]
    result: Any = None
    #: Error code the twin refused with, or None if the call succeeded.
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


Event = Message | ToolCall


@dataclass
class Trajectory:
    """The ordered record of one agent/user run."""

    events: list[Event] = field(default_factory=list)

    def add(self, event: Event) -> int:
        """Append an event and return its step."""
        self.events.append(event)
        return len(self.events) - 1

    def calls(self, operation: str | None = None) -> list[tuple[int, ToolCall]]:
        """`(step, call)` pairs, in order, optionally filtered by operation."""
        return [
            (step, event)
            for step, event in enumerate(self.events)
            if isinstance(event, ToolCall) and (operation is None or event.operation == operation)
        ]

    def messages(self) -> list[tuple[int, Message]]:
        return [
            (step, event) for step, event in enumerate(self.events) if isinstance(event, Message)
        ]

    def describe(self, step: int) -> str:
        """A one-line rendering of a step, for failure output."""
        event = self.events[step]
        if isinstance(event, Message):
            return f"step {step}: {event.role}: {event.content}"
        args = ", ".join(f"{k}={v!r}" for k, v in event.args.items())
        outcome = "ok" if event.ok else f"refused: {event.error}"
        return f"step {step}: {event.operation}({args}) -> {outcome}"
