"""Adaptive LLM user simulation, optional and never part of scoring."""

from __future__ import annotations

import os
from typing import Any

from kanon.gate.story import Story, UserTurn
from kanon.gate.trajectory import Message, Trajectory

DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_MAX_CALLS = 150
_STOP = ("###STOP###", "###TRANSFER###", "###OUT-OF-SCOPE###")

_PROMPT = """You are simulating one customer in a test conversation.

Persona: {persona}
Scenario: {goal}

- Write one natural customer message at a time.
- Follow only the scenario; never invent missing facts.
- Stay in character and reveal information progressively.
- Do not perform the support agent's job or call tools.
- When the scenario is complete, reply only with ###STOP###.
- If the scenario lacks required information, reply only with ###OUT-OF-SCOPE###.
"""


class LLMUserSimulator:
    """Generate replies after authored ``user_turns`` have been exhausted."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        max_calls: int = DEFAULT_MAX_CALLS,
        client: Any = None,
    ) -> None:
        self.model = model
        self.max_calls = max_calls
        self.calls_made = 0
        self._client = client
        self._messages: list[dict[str, str]] = []
        self._seen_events = 0

    def start(self, story: Story) -> None:
        self._messages = [
            {
                "role": "system",
                "content": _PROMPT.format(persona=story.persona, goal=story.goal.strip()),
            },
            {"role": "assistant", "content": story.goal.strip()},
        ]
        self._seen_events = 0

    def reply(self, trajectory: Trajectory) -> UserTurn | None:
        for event in trajectory.events[self._seen_events :]:
            if isinstance(event, Message):
                role = "user" if event.role == "agent" else "assistant"
                self._messages.append({"role": role, "content": event.content})
        self._seen_events = len(trajectory.events)

        if self.calls_made >= self.max_calls:
            return None
        self.calls_made += 1
        response = self._openai().chat.completions.create(
            model=self.model,
            messages=self._messages,
        )
        content = (response.choices[0].message.content or "").strip()
        if not content or any(token in content for token in _STOP):
            return None
        self._messages.append({"role": "assistant", "content": content})
        return UserTurn(content=content)

    def _openai(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            key = os.environ.get("MODEL_API_KEY") or os.environ.get("OPENAI_API_KEY")
            self._client = OpenAI(api_key=key)
        return self._client
