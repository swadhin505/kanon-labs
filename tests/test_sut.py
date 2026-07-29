"""The LLM agent's runnable check, without touching a network.

A fake client stands in for the model, so the loop, the tool schemas and the
tool-call/result pairing are all tested offline and for free. The one test that
does call a real model is marked `live` and deselected by default.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kanon.domain import Domain
from kanon.gate.runner import play, run_story
from kanon.sut import LLMAgent, tool_schemas

INSURANCE = Domain.load(Path(__file__).resolve().parents[1] / "data" / "health-insurance")
STORIES = {story.id: story for story in INSURANCE.stories}


# --- a fake model --------------------------------------------------------


@dataclass
class FakeFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    id: str
    function: FakeFunction


@dataclass
class FakeMessage:
    content: str | None = None
    tool_calls: list[FakeToolCall] | None = None


@dataclass
class FakeClient:
    """Replays a scripted list of assistant turns and records what it was sent."""

    turns: list[FakeMessage]
    seen: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.seen.append(kwargs)
        message = self.turns.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def call(name: str, **args: Any) -> FakeMessage:
    return FakeMessage(
        tool_calls=[FakeToolCall(f"call_{name}", FakeFunction(name, json.dumps(args)))]
    )


def agent_with(turns: list[FakeMessage], name: str = "fake") -> LLMAgent:
    return LLMAgent(name, INSURANCE.pack, "You are a claims agent.", client=FakeClient(turns))


# --- tool schemas --------------------------------------------------------


def test_schemas_carry_types_so_amounts_are_numbers() -> None:
    schemas = {schema["name"]: schema for schema in tool_schemas(INSURANCE.pack)}

    submit = schemas["submit_claim"]["parameters"]
    assert submit["properties"]["amount"] == {"type": "number"}
    assert submit["properties"]["member_id"] == {"type": "string"}
    assert set(submit["required"]) == {"member_id", "service_code", "amount"}
    assert submit["additionalProperties"] is False

    # No argument an operation has no business taking. Filing a claim cannot
    # set the payout, and approving one cannot rewrite the billed amount.
    assert set(submit["properties"]) == {"member_id", "service_code", "amount"}
    assert set(schemas["approve_claim"]["parameters"]["properties"]) == {
        "claim_id",
        "approved_amount",
    }


def test_schemas_expose_only_what_the_pack_declares() -> None:
    schemas = {schema["name"]: schema for schema in tool_schemas(INSURANCE.pack)}
    assert set(schemas) == set(INSURANCE.pack.routes)

    # Server-owned fields are never writable. `status` is still offered on
    # list_claims, because reading by status is not the same as setting it.
    for name in ("submit_claim", "review_claim", "approve_claim", "pay_claim"):
        properties = schemas[name]["parameters"]["properties"]
        assert "status" not in properties
        assert "submitted_at" not in properties

    # A read takes its id; a list takes its filters.
    assert set(schemas["get_claim"]["parameters"]["required"]) == {"claim_id"}
    assert set(schemas["list_claims"]["parameters"]["properties"]) == {"member_id", "status"}
    assert "annual coverage cap" in schemas["get_plan"]["description"]


# --- the loop ------------------------------------------------------------


def test_a_full_tool_calling_run_scores_like_any_other() -> None:
    from kanon.twin import Twin

    agent = agent_with(
        [
            call("get_member", member_id="MEM-0001"),
            call("submit_claim", member_id="MEM-0001", service_code="D2740", amount=800),
            call("review_claim", claim_id="CLM-0002"),
            call("approve_claim", claim_id="CLM-0002", approved_amount=720),
            FakeMessage(content="Approved for 720."),
        ]
    )

    trial = play(Twin(INSURANCE.pack), STORIES["hi-001"], agent)
    assert trial.score.passed, trial.score.reasons

    # Every tool call was answered, in order, with the twin's real reply.
    sent = agent._client.seen[-1]["messages"]
    results = [m for m in sent if isinstance(m, dict) and m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in results] == [
        "call_get_member",
        "call_submit_claim",
        "call_review_claim",
        "call_approve_claim",
    ]
    assert json.loads(results[1]["content"])["claim_id"] == "CLM-0002"


def test_the_agent_sees_the_error_body_when_the_twin_refuses() -> None:
    from kanon.twin import Twin

    agent = agent_with(
        [
            call("pay_claim", claim_id="CLM-0002"),  # does not exist yet
            FakeMessage(content="That claim does not exist."),
        ]
    )
    play(Twin(INSURANCE.pack), STORIES["hi-001"], agent)

    sent = agent._client.seen[-1]["messages"]
    results = [m for m in sent if isinstance(m, dict) and m.get("role") == "tool"]
    assert json.loads(results[0]["content"])["error"]["code"] == "not_found"


def test_malformed_tool_arguments_fail_visibly() -> None:
    from kanon.twin import Twin

    broken = FakeMessage(
        tool_calls=[FakeToolCall("call_1", FakeFunction("submit_claim", '{"member_id": '))]
    )
    agent = agent_with([broken, FakeMessage(content="Sorry.")])

    trial = play(Twin(INSURANCE.pack), STORIES["hi-001"], agent)
    assert not trial.score.passed
    assert any(
        "missing_parameter" in trial.trajectory.describe(s) for s, _ in trial.trajectory.calls()
    )


def test_a_model_that_never_stops_is_capped() -> None:
    from kanon.twin import Twin

    agent = LLMAgent(
        "loop",
        INSURANCE.pack,
        "system",
        max_turns=3,
        client=FakeClient([call("get_member", member_id="MEM-0001") for _ in range(50)]),
    )

    trial = play(Twin(INSURANCE.pack), STORIES["hi-001"], agent)
    assert "stopped after 3 assistant turns" in trial.trajectory.describe(
        len(trial.trajectory.events) - 1
    )


# --- the one test that costs money --------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    not (os.environ.get("MODEL_API_KEY") or os.environ.get("OPENAI_API_KEY")),
    reason="no MODEL_API_KEY / OPENAI_API_KEY set",
)
def test_a_real_model_can_be_measured() -> None:
    """Run with `pytest -m live`. This is where pass^k stops being decorative."""
    from kanon.twin import Twin

    result = run_story(Twin(INSURANCE.pack), STORIES["hi-001"], INSURANCE.agent("llm"), trials=3)
    print(f"\nhi-001: {result.successes}/3 trials passed")
    for trial in result.trials:
        for reason in trial.score.reasons:
            print(f"  - {reason}")
    assert not result.trivially_passed
