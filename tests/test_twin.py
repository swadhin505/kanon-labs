"""The twin's runnable check: state persists, illegal moves are refused,
snapshot/restore round-trips, and nothing is faked silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanon.twin import InjectedResponse, Pack, Twin, TwinError
from kanon.twin.store import Snapshot

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


# --- referential integrity ------------------------------------------------


REFERENTIAL = {
    "name": "orders",
    "resources": {
        "customer": {"id_field": "id", "id_prefix": "CUS-", "seed": [{"id": "CUS-0001"}]},
        "order": {
            "id_field": "id",
            "id_prefix": "ORD-",
            "fields": {"customer_id": "string", "total": "number"},
            "references": {"customer_id": "customer"},
        },
    },
    "routes": {
        "place_order": {
            "resource": "order",
            "verb": "create",
            "requires": ["customer_id", "total"],
        },
        "move_order": {"resource": "order", "verb": "update", "accepts": ["customer_id"]},
        "close_customer": {"resource": "customer", "verb": "delete"},
    },
}


@pytest.fixture
def orders() -> Twin:
    return Twin(Pack.model_validate(REFERENTIAL))


def test_a_write_naming_a_record_that_does_not_exist_is_refused(orders: Twin) -> None:
    with pytest.raises(TwinError) as refusal:
        orders.call("place_order", {"customer_id": "CUS-9999", "total": 10})

    assert refusal.value.code == "unknown_reference"
    assert refusal.value.status == 404
    assert orders.state()["order"] == {}, "a refused write leaves nothing behind"

    orders.call("place_order", {"customer_id": "CUS-0001", "total": 10})
    assert len(orders.state()["order"]) == 1


def test_an_update_cannot_repoint_a_record_at_a_ghost(orders: Twin) -> None:
    orders.call("place_order", {"customer_id": "CUS-0001", "total": 10})

    with pytest.raises(TwinError, match="unknown_reference|does not identify"):
        orders.call("move_order", {"id": "ORD-0001", "customer_id": "CUS-4242"})

    assert orders.call("place_order", {"customer_id": "CUS-0001", "total": 1})
    assert orders.state()["order"]["ORD-0001"]["customer_id"] == "CUS-0001", (
        "the update rolled back"
    )


def test_deleting_a_record_something_points_at_is_refused(orders: Twin) -> None:
    orders.call("place_order", {"customer_id": "CUS-0001", "total": 10})

    with pytest.raises(TwinError) as refusal:
        orders.call("close_customer", {"id": "CUS-0001"})

    assert refusal.value.code == "reference_in_use"
    assert "order ORD-0001" in refusal.value.message, "it names what is holding the record"
    assert "CUS-0001" in orders.state()["customer"]


def test_an_unreferenced_record_still_deletes(orders: Twin) -> None:
    orders.call("close_customer", {"id": "CUS-0001"})
    assert orders.state()["customer"] == {}


def test_a_pack_whose_seed_is_already_broken_fails_to_load() -> None:
    broken = {
        **REFERENTIAL,
        "resources": {
            **REFERENTIAL["resources"],
            "order": {
                **REFERENTIAL["resources"]["order"],
                "seed": [{"id": "ORD-0001", "customer_id": "CUS-NOPE", "total": 5}],
            },
        },
    }
    with pytest.raises(ValueError, match="references missing customer"):
        Pack.model_validate(broken)


def test_a_reference_to_an_unknown_resource_fails_to_load() -> None:
    with pytest.raises(ValueError, match="references unknown resource"):
        Pack.model_validate(
            {
                "name": "typo",
                "resources": {
                    "order": {
                        "id_field": "id",
                        "fields": {"customer_id": "string"},
                        "references": {"customer_id": "custumer"},
                    }
                },
                "routes": {"place": {"resource": "order", "verb": "create"}},
            }
        )


def test_a_reference_field_must_be_declared() -> None:
    broken = {
        **REFERENTIAL,
        "resources": {
            **REFERENTIAL["resources"],
            "order": {
                **REFERENTIAL["resources"]["order"],
                "references": {"custmer_id": "customer"},
            },
        },
    }
    with pytest.raises(ValueError, match="references through undeclared field 'custmer_id'"):
        Pack.model_validate(broken)


def test_seed_field_types_are_checked_before_the_twin_runs() -> None:
    broken = {
        **REFERENTIAL,
        "resources": {
            **REFERENTIAL["resources"],
            "order": {
                **REFERENTIAL["resources"]["order"],
                "seed": [{"id": "ORD-0001", "customer_id": "CUS-0001", "total": "lots"}],
            },
        },
    }
    with pytest.raises(ValueError, match="invalid field types: total"):
        Pack.model_validate(broken)


def test_effect_fields_must_be_declared_numeric_fields() -> None:
    broken = {
        **REFERENTIAL,
        "routes": {
            **REFERENTIAL["routes"],
            "place_order": {
                **REFERENTIAL["routes"]["place_order"],
                "effects": [
                    {
                        "resource": "customer",
                        "id_from": "customer_id",
                        "field": "label",
                        "op": "set",
                        "value_from": "total",
                    }
                ],
            },
        },
    }
    with pytest.raises(ValueError, match="customer.label is not numeric"):
        Pack.model_validate(broken)


def test_effects_recheck_references_and_roll_back_every_write() -> None:
    pack = Pack.model_validate(
        {
            "name": "ownership",
            "resources": {
                "owner": {"id_field": "id", "seed": [{"id": "1"}]},
                "account": {
                    "id_field": "id",
                    "fields": {"owner_id": "number"},
                    "references": {"owner_id": "owner"},
                    "seed": [{"id": "A-1", "owner_id": 1}],
                },
                "change": {
                    "id_field": "id",
                    "fields": {"account_id": "string", "new_owner": "number"},
                },
            },
            "routes": {
                "change_owner": {
                    "resource": "change",
                    "verb": "create",
                    "requires": ["account_id", "new_owner"],
                    "effects": [
                        {
                            "resource": "account",
                            "id_from": "account_id",
                            "field": "owner_id",
                            "op": "set",
                            "value_from": "new_owner",
                        }
                    ],
                }
            },
        }
    )
    twin = Twin(pack)

    with pytest.raises(TwinError, match="does not identify an existing owner"):
        twin.call("change_owner", {"account_id": "A-1", "new_owner": 999})

    assert twin.state()["change"] == {}, "the source write was rolled back too"
    assert twin.state()["account"]["A-1"]["owner_id"] == 1


def test_unexpected_write_errors_still_roll_back(orders: Twin, monkeypatch) -> None:
    def explode(*_args) -> None:
        raise RuntimeError("broken effect")

    monkeypatch.setattr(orders, "_apply_effects", explode)
    with pytest.raises(RuntimeError, match="broken effect"):
        orders.call("place_order", {"customer_id": "CUS-0001", "total": 10})
    assert orders.state()["order"] == {}


def test_restore_rejects_orphans_and_keeps_the_good_state(orders: Twin) -> None:
    before = orders.state()
    broken = Snapshot(
        records={
            "customer": {},
            "order": {"ORD-0001": {"id": "ORD-0001", "customer_id": "CUS-NOPE", "total": 10}},
        },
        counters={"customer": 1, "order": 1},
        step=1,
    )

    with pytest.raises(TwinError, match="does not identify an existing customer"):
        orders.restore(broken)
    assert orders.state() == before


def test_invalid_story_given_is_rejected_without_replacing_good_state(twin: Twin) -> None:
    before = twin.state()

    with pytest.raises(TwinError) as refusal:
        twin.reset(
            {
                "claim": [
                    {
                        "claim_id": "CLM-9999",
                        "member_id": "MEM-NOPE",
                        "status": "submitted",
                    }
                ]
            }
        )

    assert refusal.value.code == "invalid_given"
    assert twin.state() == before


def test_faults_are_deterministic_and_apply_only_to_the_numbered_call(twin: Twin) -> None:
    twin.reset(
        faults=[
            {
                "operation": "get_member",
                "on_call": 1,
                "status": 429,
                "code": "rate_limited",
                "message": "retry later",
            }
        ]
    )

    with pytest.raises(TwinError) as refusal:
        twin.call("get_member", {"member_id": "MEM-0001"})
    assert refusal.value.code == "rate_limited"
    assert twin.call("get_member", {"member_id": "MEM-0001"})["member_id"] == "MEM-0001"


def test_a_successful_fault_can_return_malformed_provider_data_without_writing(twin: Twin) -> None:
    before = twin.state()
    twin.reset(
        faults=[
            {
                "operation": "submit_claim",
                "on_call": 1,
                "status": 200,
                "body": {"unexpected": "shape"},
            }
        ]
    )

    result = twin.call(
        "submit_claim", {"member_id": "MEM-0001", "service_code": "D2740", "amount": 800}
    )

    assert result == InjectedResponse(200, {"unexpected": "shape"})
    assert twin.state() == before


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
