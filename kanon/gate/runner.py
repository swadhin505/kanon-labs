"""Running a story against the twin, k times.

The runner owns the loop: it resets the twin, asks the agent for one action at a
time, applies it, records what happened, and stops. The agent under test only
ever decides *what to do next* -- it never touches the twin directly, so every
trajectory is recorded the same way whether the agent is a fixed script or an
LLM.

Each story is also run once against an agent that does nothing. If that run
passes, the story is not testing anything and is reported as invalid. tau2's own
docs concede this hole -- for airline task 1, "an agent that does nothing but
politely refuse will receive full reward 1.0". A scenario that cannot fail is
worse than no scenario, because it inflates the score.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from kanon.gate.scorer import Score, score
from kanon.gate.story import Story, UserTurn
from kanon.gate.trajectory import Message, ToolCall, Trajectory
from kanon.twin.engine import TwinError

#: Stops a confused agent from looping forever. A run that hits it is a failure
#: with a reason, never a hang.
DEFAULT_MAX_STEPS = 50


@dataclass(frozen=True)
class Call:
    """The agent wants to invoke a tool."""

    operation: str
    args: dict


@dataclass(frozen=True)
class Say:
    """The agent wants to reply to the user."""

    text: str


@dataclass(frozen=True)
class ObservedCall:
    """A call an out-of-process agent already made through the served twin."""

    operation: str
    args: dict
    result: Any
    error: str | None = None


#: `None` means the agent considers the task finished.
Action = Call | Say | ObservedCall | None


class Environment(Protocol):
    """The local Twin and its remote control-plane client share this surface."""

    uncovered: dict[str, int]

    def begin_run(self) -> None: ...

    def reset(self) -> None: ...

    def state(self) -> dict: ...

    def call(self, operation: str, args: dict | None = None) -> Any: ...


class Agent(Protocol):
    """The system under test."""

    name: str

    def start(self, story: Story) -> None:
        """Begin a fresh attempt at `story`. Called once per trial."""

    def next_action(self, trajectory: Trajectory) -> Action:
        """Decide the next move, given everything that has happened so far."""


class UserSimulator(Protocol):
    """Optional adaptive user. Authored story turns always run first."""

    def start(self, story: Story) -> None: ...

    def reply(self, trajectory: Trajectory) -> UserTurn | None: ...


class ScriptedAgent:
    """Replays a fixed list of actions per story. Deterministic on purpose.

    This is the stand-in until an LLM agent exists: it makes the whole gate
    testable, and it is how a regression gets staged deliberately in a demo.
    """

    def __init__(self, name: str, scripts: dict[str, list[Action]]) -> None:
        self.name = name
        self.scripts = scripts
        self._remaining: list[Action] = []

    def start(self, story: Story) -> None:
        self._remaining = list(self.scripts.get(story.id, []))

    def next_action(self, trajectory: Trajectory) -> Action:
        return self._remaining.pop(0) if self._remaining else None


class NullAgent:
    """Does nothing at all. The baseline every story has to beat."""

    name = "null"

    def start(self, story: Story) -> None:
        return None

    def next_action(self, trajectory: Trajectory) -> Action:
        return None


@dataclass(frozen=True)
class Trial:
    trajectory: Trajectory
    score: Score
    before: dict
    after: dict


@dataclass(frozen=True)
class StoryResult:
    story: Story
    agent: str
    trials: list[Trial]
    #: True if the do-nothing agent also passed. The story proves nothing.
    trivially_passed: bool

    @property
    def successes(self) -> int:
        return sum(1 for trial in self.trials if trial.score.passed)

    @property
    def slice(self) -> tuple[str, str, str]:
        return self.story.slice


def play(
    twin: Environment,
    story: Story,
    agent: Agent,
    max_steps: int = DEFAULT_MAX_STEPS,
    user: UserSimulator | None = None,
) -> Trial:
    """One attempt: reset the twin, let the agent act, score the result."""
    twin.reset()
    seeded = twin.state()
    trajectory = Trajectory()
    agent.start(story)
    if user:
        user.start(story)
    user_turns = list(story.user_turns)

    for _ in range(max_steps):
        action = agent.next_action(trajectory)
        if action is None:
            break
        if isinstance(action, Say):
            trajectory.add(Message("agent", action.text))
            user_turn = None
            if user_turns:
                candidate = user_turns[0]
                ready = candidate.after_call is None or any(
                    call.ok and call.operation == candidate.after_call
                    for _, call in trajectory.calls()
                )
                if ready:
                    user_turn = user_turns.pop(0)
            elif user:
                user_turn = user.reply(trajectory)
            if user_turn:
                trajectory.add(Message("user", user_turn.content, tuple(user_turn.confirms)))
            continue
        if isinstance(action, ObservedCall):
            trajectory.add(ToolCall(action.operation, action.args, action.result, action.error))
            continue
        try:
            result = twin.call(action.operation, action.args)
        except TwinError as refusal:
            # `result` carries the provider-shaped error body, not just the code:
            # a real agent has to read the error to react to it.
            trajectory.add(
                ToolCall(
                    action.operation, action.args, result=refusal.as_response(), error=refusal.code
                )
            )
        else:
            trajectory.add(ToolCall(action.operation, action.args, result=result))
    else:
        trajectory.add(Message("agent", f"[gave up after {max_steps} steps]"))

    final = twin.state()
    return Trial(trajectory, score(story, seeded, final, trajectory), seeded, final)


def run_story(
    twin: Environment,
    story: Story,
    agent: Agent,
    trials: int = 1,
    user: UserSimulator | None = None,
) -> StoryResult:
    """Run one story `trials` times, plus the do-nothing baseline once."""
    if trials < 1:
        raise ValueError("trials must be at least 1")

    trivial = play(twin, story, NullAgent()).score.passed
    results = [play(twin, story, agent, user=user) for _ in range(trials)]
    return StoryResult(story, agent.name, results, trivial)


def run_all(
    twin: Environment,
    stories: list[Story],
    agent: Agent,
    trials: int = 1,
    user: UserSimulator | None = None,
) -> list[StoryResult]:
    twin.begin_run()
    return [run_story(twin, story, agent, trials, user) for story in stories]
