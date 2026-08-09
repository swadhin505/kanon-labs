from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from kanon.domain import Domain
from kanon.gate.runner import play
from kanon.serve import create_http_app
from kanon.sut import ExternalAgent, RemoteTwin

DOMAIN = Path(__file__).resolve().parents[1] / "data" / "health-insurance"
INSURANCE = Domain.load(DOMAIN)
STORY = next(story for story in INSURANCE.stories if story.id == "hi-001")


def test_any_external_agent_is_observed_and_scored_without_replaying_its_calls() -> None:
    app = create_http_app(INSURANCE.pack, token="twin-secret")
    with TestClient(app, headers={"Authorization": "Bearer twin-secret"}) as http:
        paths = {
            body["operationId"]: path
            for path, methods in app.openapi()["paths"].items()
            for body in methods.values()
        }
        environment = RemoteTwin("http://twin", "twin-secret", client=http)
        seen_messages = []

        def invoke(messages: list[dict[str, str]]) -> str:
            seen_messages.append(messages)
            calls = [
                ("get_member", {"member_id": "MEM-0001"}),
                (
                    "submit_claim",
                    {"member_id": "MEM-0001", "service_code": "D2740", "amount": 800},
                ),
                ("review_claim", {"claim_id": "CLM-0002"}),
                ("approve_claim", {"claim_id": "CLM-0002", "approved_amount": 720}),
            ]
            for operation, body in calls:
                assert http.post(paths[operation], json=body).status_code == 200
            return "Approved for 720."

        trial = play(environment, STORY, ExternalAgent("external", environment, invoke))

    assert trial.score.passed, trial.score.reasons
    assert [call.operation for _, call in trial.trajectory.calls()] == [
        "get_member",
        "submit_claim",
        "review_claim",
        "approve_claim",
    ]
    assert all(call.state_after is not None for _, call in trial.trajectory.calls())
    assert seen_messages[0][0]["role"] == "user"
