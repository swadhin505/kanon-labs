from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kanon.domain import Domain

pytest.importorskip("fastapi")
pytest.importorskip("fastmcp")

from fastapi.testclient import TestClient
from fastmcp import Client

from kanon.serve import create_app, create_http_app
from kanon.twin import Pack

DOMAIN = Path(__file__).resolve().parents[1] / "data" / "health-insurance"


def test_http_and_mcp_share_one_stateful_twin() -> None:
    app = create_app(Domain.load(DOMAIN).pack)
    paths = {
        body["operationId"]: path
        for path, methods in app.openapi()["paths"].items()
        for body in methods.values()
    }

    with TestClient(app) as http:
        member = http.post(paths["get_member"], json={"member_id": "MEM-0001"})
        assert member.status_code == 200
        assert member.json()["name"] == "Ada Okafor"

        bad = http.post(
            paths["submit_claim"],
            json={
                "member_id": "MEM-0001",
                "service_code": "D0120",
                "amount": 50,
                "injected": True,
            },
        )
        assert bad.status_code == 422

        created = http.post(
            paths["submit_claim"],
            json={"member_id": "MEM-0001", "service_code": "D0120", "amount": 50},
        ).json()

        async def check_mcp() -> None:
            async with Client(app.state.mcp) as client:
                tools = await client.list_tools()
                assert {tool.name for tool in tools} == set(Domain.load(DOMAIN).pack.routes)
                result = await client.call_tool("get_claim", {"claim_id": created["claim_id"]})
                assert result.data.amount == 50

        asyncio.run(check_mcp())


def test_a_served_twin_can_protect_its_tools_and_control_plane() -> None:
    app = create_http_app(Domain.load(DOMAIN).pack, token="secret")
    with TestClient(app) as client:
        assert client.get("/__admin/state").status_code == 401
        assert (
            client.get("/__admin/state", headers={"Authorization": "Bearer secret"}).status_code
            == 200
        )


def test_restore_rejects_an_orphaned_snapshot_without_mutating_state() -> None:
    app = create_http_app(Domain.load(DOMAIN).pack)
    with TestClient(app) as client:
        snapshot = client.get("/__admin/snapshot").json()
        snapshot["records"]["member"].pop("MEM-0001")

        refused = client.post("/__admin/restore", json=snapshot)

        assert refused.status_code == 404
        assert refused.json()["error"]["code"] == "unknown_reference"
        assert "MEM-0001" in client.get("/__admin/state").json()["member"]


def test_admin_reset_applies_story_given_and_faults_to_remote_agents() -> None:
    app = create_http_app(Domain.load(DOMAIN).pack)
    get_member = next(
        path
        for path, methods in app.openapi()["paths"].items()
        for body in methods.values()
        if body["operationId"] == "get_member"
    )
    with TestClient(app) as client:
        reset = client.post(
            "/__admin/reset",
            json={
                "given": {"member": [{"member_id": "MEM-0001", "status": "lapsed"}]},
                "faults": [
                    {
                        "operation": "get_member",
                        "on_call": 1,
                        "status": 503,
                        "code": "unavailable",
                    }
                ],
            },
        )

        assert reset.status_code == 200
        assert client.post(get_member, json={"member_id": "MEM-0001"}).status_code == 503
        recovered = client.post(get_member, json={"member_id": "MEM-0001"})
        assert recovered.status_code == 200
        assert recovered.json()["status"] == "lapsed"


def test_a_successful_injected_response_can_deliberately_break_the_schema() -> None:
    app = create_http_app(Domain.load(DOMAIN).pack)
    submit_claim = next(
        path
        for path, methods in app.openapi()["paths"].items()
        for body in methods.values()
        if body["operationId"] == "submit_claim"
    )
    with TestClient(app) as client:
        client.post(
            "/__admin/reset",
            json={
                "faults": [
                    {
                        "operation": "submit_claim",
                        "status": 200,
                        "body": {"unexpected": "shape"},
                    }
                ]
            },
        )

        response = client.post(
            submit_claim,
            json={"member_id": "MEM-0001", "service_code": "D2740", "amount": 800},
        )

        assert response.status_code == 200
        assert response.json() == {"unexpected": "shape"}


def test_request_only_arguments_do_not_break_compiled_pack_responses() -> None:
    pack = Pack.model_validate(
        {
            "name": "compiled-shape",
            "resources": {
                "charge": {
                    "id_field": "id",
                    "fields": {"amount": "integer"},
                }
            },
            "routes": {
                "create_charge": {
                    "resource": "charge",
                    "verb": "create",
                    "requires": ["amount"],
                    "accepts": ["capture"],
                    "argument_types": {"amount": "integer", "capture": "boolean"},
                }
            },
        }
    )
    app = create_http_app(pack)
    path = next(iter(app.openapi()["paths"]))

    with TestClient(app) as client:
        response = client.post(path, json={"amount": 500, "capture": True})

    assert response.status_code == 200
    assert response.json() == {"id": "0001", "amount": 500}
