from __future__ import annotations

from types import SimpleNamespace

from kanon.gate.runner import Say, ScriptedAgent, play
from kanon.gate.story import Story, UserTurn
from kanon.gate.user import LLMUserSimulator
from kanon.twin import Pack, Twin


class FakeClient:
    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.seen = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.seen.append(kwargs)
        message = SimpleNamespace(content=self.replies.pop(0))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


PACK = Pack.model_validate(
    {
        "name": "conversation",
        "resources": {"note": {"id_field": "id"}},
        "routes": {"get_note": {"resource": "note", "verb": "read"}},
    }
)


def test_adaptive_user_replies_after_authored_turns() -> None:
    story = Story(
        id="u1",
        intent="ask",
        persona="adversarial",
        goal="Ask for a refund and insist once.",
        user_turns=[UserTurn(content="I still want the refund.")],
    )
    agent = ScriptedAgent("agent", {"u1": [Say("No."), Say("The policy still says no.")]})
    client = FakeClient(["I understand. ###STOP###"])
    user = LLMUserSimulator(client=client)

    trial = play(Twin(PACK), story, agent, user=user)

    user_messages = [
        message.content for _, message in trial.trajectory.messages() if message.role == "user"
    ]
    assert user_messages == ["I still want the refund."]
    assert len(client.seen) == 1, "the authored turn runs first; the model fills only the next turn"
    assert "Persona: adversarial" in client.seen[0]["messages"][0]["content"]


def test_adaptive_user_adds_a_natural_reply() -> None:
    story = Story(id="u2", intent="ask", persona="confused", goal="Ask what a charge means.")
    agent = ScriptedAgent("agent", {"u2": [Say("Which charge?")]})
    user = LLMUserSimulator(client=FakeClient(["The 20 dollar charge from yesterday."]))

    trial = play(Twin(PACK), story, agent, user=user)

    assert any(
        message.role == "user" and "20 dollar" in message.content
        for _, message in trial.trajectory.messages()
    )
