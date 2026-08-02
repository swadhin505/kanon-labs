"""The twin's runnable check: state persists, illegal moves are refused,
snapshot/restore round-trips, and nothing is faked silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanon.twin import Pack, Twin, TwinError

PACK = Path(__file__).resolve().parents[1] / "data" / "health-insurance" / "pack.yaml"


@pytest.fixture
def twin() -> Twin:
    return Twin(Pack.from_yaml(PACK))


def submit(twin: Twin, amount: int = 400) -> str:
    claim = twin.call(
        "submit_claim", {"member_id": "MEM-0001", "service_code": "D2740", "amount": amount}
    )
    return claim["claim_id"]


def test_state_persists_across_calls(twin: Twin) -> None:
    claim_id = submit(twin)
    assert claim_id == "CLM-0002", "ids continue past the seeded records"

    fetched = twin.call("get_claim", {"claim_id": claim_id})
    assert fetched["status"] == "submitted"
    assert fetched["amount"] == 400
    assert fetched["submitted_at"] == "2026-01-01T00:00:00Z", "logical clock, not wall clock"

    second = twin.call(
        "submit_claim", {"member_id": "MEM-0001", "service_code": "D1110", "amount": 90}
    )
    assert second["submitted_at"] == "2026-01-01T00:00:01Z", "the clock advances one tick a write"

    twin.call("review_claim", {"claim_id": claim_id})
    twin.call("approve_claim", {"claim_id": claim_id, "approved_amount": 360})
    assert twin.call("get_claim", {"claim_id": claim_id})["approved_amount"] == 360


def test_illegal_transition_is_refused(twin: Twin) -> None:
    claim_id = submit(twin)

    with pytest.raises(TwinError) as caught:
        twin.call("pay_claim", {"claim_id": claim_id})  # never approved

    assert caught.value.code == "invalid_transition"
    assert caught.value.status == 409
    assert twin.call("get_claim", {"claim_id": claim_id})["status"] == "submitted", (
        "a refused call must not have written anything"
    )


def test_terminal_state_is_terminal(twin: Twin) -> None:
    claim_id = submit(twin)
    twin.call("deny_claim", {"claim_id": claim_id, "reason": "not covered"})

    with pytest.raises(TwinError):
        twin.call("review_claim", {"claim_id": claim_id})


def test_state_is_only_settable_through_a_route(twin: Twin) -> None:
    claim_id = submit(twin)
    with pytest.raises(TwinError, match="does not accept note, status"):
        twin.call("review_claim", {"claim_id": claim_id, "status": "paid", "note": "nice try"})

    claim = twin.call("get_claim", {"claim_id": claim_id})
    assert claim["status"] == "submitted", "a refused call must not change state"
    assert "note" not in claim


def test_snapshot_restore_round_trips(twin: Twin) -> None:
    before = twin.snapshot()
    baseline = twin.state()

    claim_id = submit(twin)
    twin.call("deny_claim", {"claim_id": claim_id, "reason": "duplicate"})
    assert twin.state() != baseline

    twin.restore(before)
    assert twin.state() == baseline
    assert submit(twin) == "CLM-0002", "the id counter rewinds with the state"


def test_uncovered_operation_is_flagged_not_faked(twin: Twin) -> None:
    with pytest.raises(TwinError) as caught:
        twin.call("appeal_claim", {"claim_id": "CLM-0001"})

    assert caught.value.status == 501
    assert twin.uncovered == {"appeal_claim": 1}


def test_missing_argument_and_missing_record(twin: Twin) -> None:
    with pytest.raises(TwinError) as missing_arg:
        twin.call("submit_claim", {"member_id": "MEM-0001", "amount": 10})
    assert missing_arg.value.code == "missing_parameter"
    assert "service_code" in missing_arg.value.message

    with pytest.raises(TwinError) as missing_record:
        twin.call("get_claim", {"claim_id": "CLM-9999"})
    assert missing_record.value.status == 404


def test_list_is_filtered_and_ordered(twin: Twin) -> None:
    submit(twin)
    submit(twin)

    claims = twin.call("list_claims", {"member_id": "MEM-0001"})
    assert [c["claim_id"] for c in claims] == ["CLM-0001", "CLM-0002", "CLM-0003"]
    assert [c["claim_id"] for c in twin.call("list_claims", {"status": "paid"})] == ["CLM-0001"]


def test_reads_cannot_mutate_the_store(twin: Twin) -> None:
    twin.call("get_claim", {"claim_id": "CLM-0001"})["amount"] = 999_999
    assert twin.call("get_claim", {"claim_id": "CLM-0001"})["amount"] == 180


def test_a_bad_pack_fails_at_load_not_at_run_time() -> None:
    with pytest.raises(ValueError, match="not declared states"):
        Pack.model_validate(
            {
                "name": "typo",
                "resources": {
                    "claim": {
                        "id_field": "claim_id",
                        "state_field": "status",
                        "transitions": {"submitted": ["aproved"], "approved": []},
                    }
                },
                "routes": {},
            }
        )


def test_a_declared_state_machine_must_actually_be_driven() -> None:
    with pytest.raises(ValueError, match="no route sets its state"):
        Pack.model_validate(
            {
                "name": "decorative-machine",
                "resources": {
                    "job": {
                        "id_field": "id",
                        "state_field": "status",
                        "transitions": {"pending": ["done"], "done": []},
                    }
                },
                "routes": {"create_job": {"resource": "job", "verb": "create"}},
            }
        )


def test_the_engine_rejects_schema_bypasses(twin: Twin) -> None:
    with pytest.raises(TwinError, match="does not accept injected") as extra:
        twin.call(
            "submit_claim",
            {
                "member_id": "MEM-0001",
                "service_code": "D0120",
                "amount": 400,
                "injected": "not in the operation schema",
            },
        )
    assert extra.value.code == "unexpected_parameter"

    with pytest.raises(TwinError, match="amount must be number") as wrong_type:
        twin.call(
            "submit_claim",
            {"member_id": "MEM-0001", "service_code": "D0120", "amount": "400"},
        )
    assert wrong_type.value.code == "invalid_parameter"


def test_effects_cannot_write_another_resources_server_owned_fields() -> None:
    with pytest.raises(ValueError, match="effect writes server-owned field job.status"):
        Pack.model_validate(
            {
                "name": "effect-bypass",
                "resources": {
                    "event": {"id_field": "id", "fields": {"job_id": "string"}},
                    "job": {"id_field": "id", "state_field": "status"},
                },
                "routes": {
                    "create_event": {
                        "resource": "event",
                        "verb": "create",
                        "requires": ["job_id"],
                        "effects": [
                            {
                                "resource": "job",
                                "id_from": "job_id",
                                "field": "status",
                                "op": "set",
                                "value_from": "job_id",
                            }
                        ],
                    }
                },
            }
        )
