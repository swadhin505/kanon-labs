"""A real LLM agent as the system under test.

This is the first place in the product where an LLM appears at run time -- and it
is on the side being *measured*, never the side doing the measuring. The twin and
the scorer stay pure; if this file were deleted, the gate would still work.

It implements the same two methods `ScriptedAgent` does, so nothing else in the
gate changes. The runner still owns the loop and still records every call, which
is why a scripted agent and a real one produce identically-shaped trajectories.

One thing genuinely changes when this agent runs: **pass^k stops being
decorative.** A script cannot be flaky; a model can. The environment is still
deterministic, so any variance between trials is the agent's.
"""

from __future__ import annotations

import json
import os
from typing import Any

from kanon.gate.runner import Action, Call, Say
from kanon.gate.story import Story
from kanon.gate.trajectory import Trajectory
from kanon.sut.tools import openai_tools
from kanon.twin.pack import Pack

#: The model is a config value, not a constant, because it is the thing under
#: test. Override per agent in the domain's agents.py.
DEFAULT_MODEL = "gpt-5-mini"

#: Cap on assistant turns per *trial*. Stops one confused conversation from
#: looping. The runner's `max_steps` is a second, coarser backstop.
DEFAULT_MAX_TURNS = 10

#: Cap on model calls for the whole *run*, across every story and every trial.
#: This is the one that actually protects the bill: turns-per-trial bounds one
#: conversation, but a run is stories x trials x turns, and that multiplies fast.
#: Exhausting it fails the remaining stories loudly rather than quietly passing
#: them -- a truncated run must never look like a clean one.
DEFAULT_MAX_CALLS = 150


class LLMAgent:
    """Drives a tool-calling model through one story.

    The conversation lives here rather than in the trajectory, because the API
    needs its own message history with tool-call ids the trajectory has no
    reason to carry. The trajectory stays the neutral record used for scoring.
    """

    def __init__(
        self,
        name: str,
        pack: Pack,
        system: str,
        model: str = DEFAULT_MODEL,
        max_turns: int = DEFAULT_MAX_TURNS,
        max_calls: int = DEFAULT_MAX_CALLS,
        client: Any = None,
    ) -> None:
        self.name = name
        self.model = model
        self.system = system
        self.max_turns = max_turns
        self.max_calls = max_calls
        #: Model calls made across the whole run. Deliberately not reset by
        #: `start()` -- the budget covers the run, not one trial.
        self.calls_made = 0
        self._tools = openai_tools(pack)
        self._client = client

        self._messages: list[Any] = []
        self._queue: list[Action] = []
        #: Tool-call ids issued to the runner but not yet answered, in order.
        self._awaiting: list[str] = []
        self._turns = 0
        self._done = False

    # --- the Agent protocol ----------------------------------------------

    def start(self, story: Story) -> None:
        self._messages = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": story.goal.strip()},
        ]
        self._queue = []
        self._awaiting = []
        self._turns = 0
        self._done = False

    def next_action(self, trajectory: Trajectory) -> Action:
        # Hand back whatever the last assistant turn produced, one at a time.
        if self._queue:
            return self._queue.pop(0)

        if self._awaiting:
            # Every issued tool call is answered before the next request --
            # results for a parallel batch go back together, in order.
            self._answer_tool_calls(trajectory)
        elif self._done:
            return None

        # Say it once, then stop -- not on every step until the runner gives up.
        if self.calls_made >= self.max_calls:
            self._done = True
            return Say(f"[run budget of {self.max_calls} model calls is spent]")
        if self._turns >= self.max_turns:
            self._done = True
            return Say(f"[stopped after {self.max_turns} assistant turns]")

        self._ask()
        return self._queue.pop(0) if self._queue else None

    # --- talking to the model --------------------------------------------

    def _ask(self) -> None:
        self._turns += 1
        self.calls_made += 1
        response = self._openai().chat.completions.create(
            model=self.model,
            messages=self._messages,
            tools=self._tools,
        )
        message = response.choices[0].message
        self._messages.append(message)

        if getattr(message, "content", None):
            self._queue.append(Say(message.content))

        tool_calls = getattr(message, "tool_calls", None) or []
        for call in tool_calls:
            self._awaiting.append(call.id)
            self._queue.append(Call(call.function.name, _parse_args(call.function.arguments)))

        if not tool_calls:
            self._done = True

    def _answer_tool_calls(self, trajectory: Trajectory) -> None:
        """Feed the twin's replies back, paired with the ids we were given.

        The runner applies calls in the order we returned them, so the trailing
        N tool calls in the trajectory line up with the N ids we are waiting on.
        """
        recent = [call for _, call in trajectory.calls()][-len(self._awaiting) :]
        for call_id, call in zip(self._awaiting, recent, strict=True):
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(call.result, default=str),
                }
            )
        self._awaiting = []

    def _openai(self) -> Any:
        if self._client is None:
            from openai import OpenAI  # imported lazily so the gate runs without it

            key = os.environ.get("MODEL_API_KEY") or os.environ.get("OPENAI_API_KEY")
            self._client = OpenAI(api_key=key)
        return self._client


def _parse_args(raw: str | None) -> dict[str, Any]:
    """Tool arguments arrive as a JSON string and are not guaranteed valid.

    ponytail: malformed JSON becomes an empty argument set, so the twin refuses
    it with `missing_parameter` and the failure shows up in the report as a real
    failure. Upgrade to a synthetic error result if this ever needs to be
    distinguished from an agent that genuinely sent nothing.
    """
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
